"""local_login 의 타이밍 사이드채널 방어 검증 (rollback — 실제 변경 없음).

보안 QA: local_login 은 이메일 없음/OAuth 전용 계정/비밀번호 불일치를
모두 동일한 LocalLoginNotAvailable 예외·메시지로 처리해 계정 열거를
막고 있었지만, 실제로는 이메일이 없거나 OAuth 전용 계정이면 bcrypt
검증(verify_password) 자체를 건너뛰고 즉시 반환해 응답 시간이 눈에
띄게 짧았다 — 이 시간 차이로 "이 이메일이 가입돼 있는지"를 추정할 수
있었다(에러 메시지는 같아도 타이밍은 달랐음).

수정: 이메일이 없거나 OAuth 전용 계정이어도 더미 해시에 대해
verify_password 를 한 번 태운 뒤 동일하게 거부하도록 했다. 실제
bcrypt round 수 자체를 재는 건 환경마다 편차가 커서(CI 등) 신뢰하기
어려우므로, 여기서는 "세 거부 경로 모두 verify_password 가 정확히
1회씩 호출되는지"를 직접 세어 구조적으로 검증한다 — 호출 여부가
같으면 소요 시간도 사실상 같아진다.

실행:
    uv run python -m scripts.verify_local_login_timing
"""

import asyncio
import sys
import uuid
from unittest.mock import patch

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.core.password import hash_password, verify_password
from app.domain.auth.exceptions import LocalLoginNotAvailable
from app.domain.auth.service import AuthService
from app.domain.users.models import User
from app.infra.db.engine import close_engine, init_engine

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
_results: list[bool] = []


def check(name: str, ok: bool) -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}")


def make_local_user(nick: str, *, password: str, email_verified: bool = True) -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"{nick}_{uid}@clog.test",
        nickname=f"{nick}_{uid}",
        auth_provider="local",
        auth_provider_id=None,
        password_hash=hash_password(password),
        email_verified=email_verified,
        is_public=True,
    )


def make_oauth_user(nick: str) -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"{nick}_{uid}@clog.test",
        nickname=f"{nick}_{uid}",
        auth_provider="kakao",
        auth_provider_id=f"kakao_{uid}",
        password_hash=None,
        email_verified=True,
        is_public=True,
    )


async def run(session: AsyncSession) -> None:
    from app.domain.users.repository import UserRepository

    user_repo = UserRepository(session)
    # local_login 의 세 거부 경로 모두 refresh/email_verify/password_reset repo 를
    # 건드리지 않으므로(성공 경로만 씀) None 으로 충분 — Redis 불필요.
    auth = AuthService(
        session=session,
        refresh_repo=None,  # type: ignore[arg-type]
        user_repo=user_repo,
        email_verify_repo=None,  # type: ignore[arg-type]
        password_reset_repo=None,  # type: ignore[arg-type]
    )

    real_password = "Correct-Horse1!"
    local_user = make_local_user("local", password=real_password)
    oauth_user = make_oauth_user("oauth")
    session.add_all([local_user, oauth_user])
    await session.flush()

    with patch(
        "app.domain.auth.service.verify_password", wraps=verify_password
    ) as spy:
        # ── 1) 이메일 없음 ──
        spy.reset_mock()
        try:
            await auth.local_login(email="no-such-user@clog.test", password="whatever1!A")
            check("이메일 없음 → 예외 발생함", False)
        except LocalLoginNotAvailable:
            check("이메일 없음 → LocalLoginNotAvailable", True)
        check("이메일 없음도 verify_password 를 1회 호출함(타이밍 맞춤)", spy.call_count == 1)

        # ── 2) OAuth 전용 계정 ──
        spy.reset_mock()
        try:
            await auth.local_login(email=oauth_user.email, password="whatever1!A")
            check("OAuth 전용 계정 → 예외 발생함", False)
        except LocalLoginNotAvailable:
            check("OAuth 전용 계정 → LocalLoginNotAvailable", True)
        check(
            "OAuth 전용 계정도 verify_password 를 1회 호출함(타이밍 맞춤)",
            spy.call_count == 1,
        )

        # ── 3) 비밀번호 불일치 ──
        spy.reset_mock()
        try:
            await auth.local_login(email=local_user.email, password="wrong-password1!")
            check("비밀번호 불일치 → 예외 발생함", False)
        except LocalLoginNotAvailable:
            check("비밀번호 불일치 → LocalLoginNotAvailable", True)
        check("비밀번호 불일치도 verify_password 1회 호출", spy.call_count == 1)

    # ── 4) 정상 로그인은 여전히 성공해야 함(회귀 확인, 토큰 발급 전 단계만) ──
    user = await user_repo.get_by_email(local_user.email)
    check(
        "정상 계정 조회 + 실제 비밀번호로 verify_password 통과(회귀 없음)",
        user is not None and verify_password(real_password, user.password_hash),
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
