"""admin 사용자 차단 E2E 검증 (rollback — 실제 변경 없음).

Step 3(User.is_banned + 로그인/활동 차단 + admin ban/unban)을 검증한다.

★ 선행: is_banned 컬럼 마이그레이션이 적용돼 있어야 한다.
    uv run alembic upgrade head
실행:
    uv run python -m scripts.verify_admin_ban

savepoint 패턴으로 서비스 내부 commit 을 savepoint 로 가두고, 마지막에
바깥 트랜잭션을 rollback 하므로 DB 에 아무것도 남지 않는다. (차단 로그인
경로는 토큰 발급 전에 거부되므로 Redis 도 건드리지 않는다.)
"""

import asyncio
import sys
import uuid

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401 — 전체 모델 로드(FK 참조 등록)
from app.api.dependencies import get_current_user
from app.core.password import hash_password
from app.core.security import create_access_token
from app.domain.auth.exceptions import AccountBanned
from app.domain.auth.service import AuthService
from app.domain.users.exceptions import CannotBanSelf, UserNotFound
from app.domain.users.models import User
from app.domain.users.repository import UserRepository
from app.domain.users.service import UserService
from app.infra.db.engine import close_engine, init_engine

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

_results: list[bool] = []


def check(name: str, ok: bool) -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}")


def make_user(
    nickname: str,
    *,
    is_admin: bool = False,
    is_banned: bool = False,
    password: str | None = None,
) -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"ban_{uid}@clog.test",
        nickname=f"{nickname}_{uid}",
        auth_provider="local",
        email_verified=True,
        is_admin=is_admin,
        is_banned=is_banned,
        password_hash=hash_password(password) if password else None,
    )


def bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


async def expect(coro, exc_type: type[Exception], name: str) -> None:
    try:
        await coro
        check(name, False)
    except exc_type:
        check(name, True)


async def run(session: AsyncSession) -> None:
    repo = UserRepository(session)
    users = UserService(session, repo)
    auth = AuthService(
        session=session,
        refresh_repo=None,  # 차단 경로는 토큰 발급 전 거부 → 미사용
        user_repo=repo,
        email_verify_repo=None,
        password_reset_repo=None,  # 이 검증에서는 미사용
    )

    admin = make_user("admin", is_admin=True)
    normal = make_user("normal")
    session.add_all([admin, normal])
    await session.flush()

    # ── admin ban / unban ───────────────────────────────────
    print("\n[사용자 차단 서비스]")
    u = await users.set_ban(user_id=normal.id, banned=True, actor_id=admin.id)
    check("admin 이 사용자 차단 → is_banned True", u.is_banned is True)

    u = await users.set_ban(user_id=normal.id, banned=False, actor_id=admin.id)
    check("admin 이 차단 해제 → is_banned False", u.is_banned is False)

    await expect(
        users.set_ban(user_id=admin.id, banned=True, actor_id=admin.id),
        CannotBanSelf,
        "자기 자신 차단 시도 → CannotBanSelf(400)",
    )
    await expect(
        users.set_ban(user_id=uuid.uuid4(), banned=True, actor_id=admin.id),
        UserNotFound,
        "없는 사용자 차단 → UserNotFound(404)",
    )

    # ── 활동 차단 (get_current_user) ─────────────────────────
    print("\n[활동 차단 — get_current_user]")
    await users.set_ban(user_id=normal.id, banned=True, actor_id=admin.id)
    banned_token = create_access_token(str(normal.id))
    try:
        await get_current_user(credentials=bearer(banned_token), user_repo=repo)
        check("차단 사용자 토큰 → 403 거부", False)
    except HTTPException as e:
        check("차단 사용자 토큰 → 403 거부", e.status_code == 403)

    active = make_user("active")
    session.add(active)
    await session.flush()
    ok_token = create_access_token(str(active.id))
    got = await get_current_user(credentials=bearer(ok_token), user_repo=repo)
    check("미차단 사용자 토큰 → 정상 통과", got.id == active.id)

    # ── 로그인 차단 ─────────────────────────────────────────
    print("\n[로그인 차단]")
    await expect(
        auth.login(normal.id),  # 카카오/시뮬 경로
        AccountBanned,
        "login(차단 사용자) → AccountBanned(403)",
    )

    pw = "BannedPass1234!"
    local_banned = make_user("local_banned", is_banned=True, password=pw)
    session.add(local_banned)
    await session.flush()
    await expect(
        auth.local_login(email=local_banned.email, password=pw),
        AccountBanned,
        "local_login(차단 사용자) → AccountBanned(403)",
    )


async def main() -> int:
    engine = init_engine()
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
            autoflush=False,
        )
        try:
            await run(session)
        finally:
            await session.close()
            await outer.rollback()  # fixture 포함 전부 되돌림 (DB 무변경)
    await close_engine()

    total = len(_results)
    passed = sum(_results)
    print(f"\n{'─' * 40}")
    if passed == total:
        print(f"{GREEN}ALL PASS{RESET}  ({passed}/{total})  — DB 변경 없음(rollback)")
        return 0
    print(f"{RED}FAILED{RESET}  ({passed}/{total})")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
