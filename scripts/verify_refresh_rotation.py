"""refresh token 회전 + 재사용 감지 검증 (감사 M2, DB rollback · Redis 키 정리).

시나리오:
  - 회전 신호 없음(옛 클라이언트) → access 만, 같은 refresh 계속 사용 가능
  - 회전 → 새 refresh 발급, 쓴 토큰은 rotated 로 폐기, 새 토큰으로 다시 회전(체인)
  - 회전 폐기 토큰 재사용 — 유예(30s) 안: 401 만(현재 토큰 유지) / 밖: 사용자 refresh 전부 폐기
  - 로그아웃 폐기 토큰 재사용 → 401 만 (전체 폐기 안 함)
  - 같은 토큰 동시 회전 2건 → 정확히 1건만 성공 (Lua 원자 처리)
  - 재사용 감지는 다른 사용자에 영향 없음
실행:
    uv run python -m scripts.verify_refresh_rotation
"""

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401 — 전체 모델 로드(FK 참조 등록)
from app.core.security import decode_access_token, decode_refresh_token
from app.domain.auth.exceptions import RefreshTokenNotFound
from app.domain.auth.repository import RedisRefreshTokenRepository
from app.domain.auth.service import AuthService
from app.domain.users.models import User
from app.domain.users.repository import UserRepository
from app.infra.db.engine import close_engine, init_engine
from app.infra.redis import close_redis, init_redis

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

_results: list[bool] = []
_jtis: set[str] = set()
_users: set[str] = set()


def check(name: str, ok: bool) -> None:
    _results.append(ok)
    print(f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}", name)


def track(*tokens: str | None) -> None:
    for t in tokens:
        if t:
            _jtis.add(decode_refresh_token(t).jti)


def make_user(nickname: str) -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"rot_{uid}@clog.test",
        nickname=f"{nickname}_{uid}",
        auth_provider="local",
        email_verified=True,
    )


async def refresh_ok(auth: AuthService, token: str, *, rotate: bool) -> tuple[str, str | None] | None:
    try:
        return await auth.refresh_access_token(token, rotate=rotate)
    except RefreshTokenNotFound:
        return None


async def run(session: AsyncSession, redis) -> None:  # type: ignore[no-untyped-def]
    repo = RedisRefreshTokenRepository(redis)
    auth = AuthService(
        session=session,
        refresh_repo=repo,
        user_repo=UserRepository(session),
        email_verify_repo=None,  # type: ignore[arg-type]
        password_reset_repo=None,  # type: ignore[arg-type]
    )
    alice, bob = make_user("alice"), make_user("bob")
    session.add_all([alice, bob])
    await session.flush()
    _users.update({str(alice.id), str(bob.id)})

    # ── 옛 클라이언트 (회전 신호 없음) ────────────────────
    legacy = await auth._issue_token_pair(alice.id)
    track(legacy.refresh_token)
    r1 = await refresh_ok(auth, legacy.refresh_token, rotate=False)
    r2 = await refresh_ok(auth, legacy.refresh_token, rotate=False)
    check("회전 신호 없음 → access 만 (refresh None)", r1 is not None and r1[1] is None)
    check("회전 신호 없음 → 같은 refresh 로 다시 갱신 가능", r2 is not None)
    check("발급된 access 가 유효", r1 is not None and decode_access_token(r1[0]).sub == str(alice.id))

    # ── 회전 체인 ─────────────────────────────────────────
    t0 = (await auth._issue_token_pair(alice.id)).refresh_token
    track(t0)
    a1 = await refresh_ok(auth, t0, rotate=True)
    t1 = a1[1] if a1 else None
    track(t1)
    check("회전 → 새 refresh 발급(다른 jti)", bool(t1) and decode_refresh_token(t1).jti != decode_refresh_token(t0).jti)  # type: ignore[arg-type]
    rec0 = await repo.get_record(decode_refresh_token(t0).jti)
    check("쓴 토큰은 revoked_reason=rotated", rec0 is not None and rec0.revoked and rec0.revoked_reason == "rotated")
    a2 = await refresh_ok(auth, t1, rotate=True)  # type: ignore[arg-type]
    t2 = a2[1] if a2 else None
    track(t2)
    check("새 토큰으로 다시 회전(체인)", bool(t2))

    # ── 유예 안 재사용 (여러 탭 경쟁) ─────────────────────
    check("회전 폐기 토큰 재사용(유예 안) → 401", await refresh_ok(auth, t1, rotate=True) is None)  # type: ignore[arg-type]
    rec2 = await repo.get_record(decode_refresh_token(t2).jti)  # type: ignore[arg-type]
    check("유예 안 재사용은 현재 토큰을 폐기하지 않음", rec2 is not None and not rec2.revoked)

    # ── 로그아웃 폐기 토큰 재사용 → 401 만 ────────────────
    side = (await auth._issue_token_pair(alice.id)).refresh_token  # 다른 기기 세션
    track(side)
    await auth.logout(side)
    check("로그아웃한 토큰 재사용 → 401", await refresh_ok(auth, side, rotate=True) is None)
    rec2 = await repo.get_record(decode_refresh_token(t2).jti)  # type: ignore[arg-type]
    check("로그아웃 토큰 재사용은 전체 폐기를 일으키지 않음", rec2 is not None and not rec2.revoked)

    # ── 유예 밖 재사용 = 탈취 ─────────────────────────────
    bob_tok = (await auth._issue_token_pair(bob.id)).refresh_token
    track(bob_tok)
    key = f"refresh_token:{decode_refresh_token(t1).jti}"  # type: ignore[arg-type]
    data = json.loads(await redis.get(key))
    data["revoked_at"] = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()  # 60초 전 회전으로 조작
    await redis.set(key, json.dumps(data), ex=await redis.ttl(key))
    check("회전 폐기 토큰 재사용(유예 밖) → 401", await refresh_ok(auth, t1, rotate=True) is None)  # type: ignore[arg-type]
    rec2 = await repo.get_record(decode_refresh_token(t2).jti)  # type: ignore[arg-type]
    check("→ 사용자 refresh 전부 폐기(reason=reuse_detected)", rec2 is not None and rec2.revoked and rec2.revoked_reason == "reuse_detected")
    check("→ 현재 토큰으로도 갱신 불가(재로그인 필요)", await refresh_ok(auth, t2, rotate=True) is None)  # type: ignore[arg-type]
    check("→ 다른 사용자(bob)는 영향 없음", await refresh_ok(auth, bob_tok, rotate=False) is not None)

    # ── 동시 회전 경쟁 ────────────────────────────────────
    race = (await auth._issue_token_pair(bob.id)).refresh_token
    track(race)
    results = await asyncio.gather(refresh_ok(auth, race, rotate=True), refresh_ok(auth, race, rotate=True))
    for r in results:
        if r:
            track(r[1])
    check("같은 토큰 동시 회전 2건 → 정확히 1건 성공", sum(r is not None for r in results) == 1)


async def main() -> int:
    engine = init_engine()
    redis = init_redis()
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            session = AsyncSession(
                bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False, autoflush=False
            )
            try:
                await run(session, redis)
            finally:
                await session.close()
                await outer.rollback()  # 사용자 fixture 전부 되돌림
    finally:
        # 이 검증이 만든 Redis 키만 정리
        keys = [f"refresh_token:{j}" for j in _jtis] + [f"user_tokens:{u}" for u in _users]
        if keys:
            await redis.delete(*keys)
        await close_redis()
        await close_engine()

    passed, total = sum(_results), len(_results)
    print(f"\n{'─' * 40}")
    if passed == total:
        print(f"{GREEN}ALL PASS{RESET}  ({passed}/{total})  — DB rollback · Redis 키 {len(_jtis)}개 정리")
        return 0
    print(f"{RED}FAILED{RESET}  ({passed}/{total})")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
