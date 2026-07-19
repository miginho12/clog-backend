"""비밀번호 찾기(코드 방식) E2E 검증 (rollback — 실제 변경 없음).

흐름 전체를 검증: 코드 요청 → 코드 확인(reset_token 발급) → 새 비밀번호 설정.
실제 메일 발송은 네트워크를 타므로 mock 으로 대체(서비스 로직 자체를 검증하는
게 목적 — SMTP 연결 성공 여부는 별개 관심사).

검증 항목:
- 코드 요청 시 실제 메일 발송 함수가 올바른 인자로 호출되는지
- 존재하지 않는 이메일 / OAuth 전용 계정: 예외 없이 조용히 종료(계정 열거 방어)
- 틀린 코드: PasswordResetCodeInvalid
- 맞는 코드: reset_token 발급 + 코드 재사용 불가(일회용)
- 틀린/만료 reset_token: PasswordResetTokenInvalid
- 맞는 reset_token + 새 비밀번호: 실제로 로그인 가능한 해시로 바뀜, 토큰 재사용 불가

실행:
    uv run python -m scripts.verify_password_reset
"""

import asyncio
import sys
import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.core.password import verify_password
from app.domain.auth.exceptions import (
    PasswordResetCodeInvalid,
    PasswordResetTokenInvalid,
)
from app.domain.auth.password_reset_repository import PasswordResetRepository
from app.domain.auth.service import AuthService
from app.domain.users.models import User
from app.infra.db.engine import close_engine, init_engine
from app.infra.redis import close_redis, init_redis

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
_results: list[bool] = []


def check(name, ok):
    _results.append(ok)
    print(f"  [{(GREEN+'PASS' if ok else RED+'FAIL')+RESET}] {name}")


async def expect_raises(coro, exc_type, name):
    try:
        await coro
        check(name, False)
    except exc_type:
        check(name, True)
    except Exception as e:
        check(f"{name} (예상과 다른 예외: {type(e).__name__})", False)


def make_local_user(nick, password_hash):
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"pr_{uid}@clog.test", nickname=f"{nick}_{uid}",
        auth_provider="local", email_verified=True, password_hash=password_hash,
    )


def make_kakao_user(nick):
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"pr_{uid}@clog.test", nickname=f"{nick}_{uid}",
        auth_provider="kakao", auth_provider_id=f"kakao_{uid}",
        email_verified=True, password_hash=None,
    )


async def run(session: AsyncSession, redis) -> None:
    from app.core.password import hash_password

    old_hash = hash_password("OldPassw0rd!!!")
    local_user = make_local_user("local", old_hash)
    kakao_user = make_kakao_user("kakao")
    session.add_all([local_user, kakao_user])
    await session.flush()

    reset_repo = PasswordResetRepository(redis)
    auth = AuthService(
        session=session, refresh_repo=None, user_repo=_UserRepoAdapter(session),
        email_verify_repo=None, password_reset_repo=reset_repo,
    )

    print("\n[코드 요청 — 계정 열거 방어]")
    with patch(
        "app.domain.auth.service.send_password_reset_email", new_callable=AsyncMock
    ) as mock_send:
        await auth.request_password_reset("no-such-email@clog.test")
        check("존재하지 않는 이메일: 예외 없이 종료", True)
        check("존재하지 않는 이메일: 메일 발송 안 함", mock_send.await_count == 0)

        await auth.request_password_reset(kakao_user.email)
        check("OAuth 전용 계정: 예외 없이 종료", True)
        check("OAuth 전용 계정: 메일 발송 안 함", mock_send.await_count == 0)

        await auth.request_password_reset(local_user.email)
        check("local 계정: 메일 발송 1회 호출", mock_send.await_count == 1)
        sent_code = mock_send.call_args.kwargs["code"]
        check("발송된 코드가 6자리 숫자", sent_code.isdigit() and len(sent_code) == 6)

    print("\n[코드 확인]")
    await expect_raises(
        auth.verify_password_reset_code(email=local_user.email, code="000000"),
        PasswordResetCodeInvalid,
        "틀린 코드 → PasswordResetCodeInvalid",
    )
    reset_token = await auth.verify_password_reset_code(
        email=local_user.email, code=sent_code
    )
    check("맞는 코드 → reset_token 발급", bool(reset_token))
    await expect_raises(
        auth.verify_password_reset_code(email=local_user.email, code=sent_code),
        PasswordResetCodeInvalid,
        "같은 코드 재사용 불가(일회용)",
    )

    print("\n[새 비밀번호 설정]")
    await expect_raises(
        auth.confirm_password_reset(
            reset_token="garbage-token", new_password="NewPassw0rd!!!"
        ),
        PasswordResetTokenInvalid,
        "가짜 reset_token → PasswordResetTokenInvalid",
    )

    await auth.confirm_password_reset(
        reset_token=reset_token, new_password="NewPassw0rd!!!"
    )
    await session.refresh(local_user)
    check(
        "비밀번호가 실제로 새 값으로 바뀜",
        verify_password("NewPassw0rd!!!", local_user.password_hash),
    )
    check(
        "예전 비밀번호는 더 이상 안 맞음",
        not verify_password("OldPassw0rd!!!", local_user.password_hash),
    )

    await expect_raises(
        auth.confirm_password_reset(
            reset_token=reset_token, new_password="AnotherPassw0rd!!!"
        ),
        PasswordResetTokenInvalid,
        "reset_token 재사용 불가(일회용)",
    )


class _UserRepoAdapter:
    """UserRepository 를 직접 import 하지 않고 세션만으로 get_by_email 제공."""

    def __init__(self, session: AsyncSession):
        from app.domain.users.repository import UserRepository

        self._repo = UserRepository(session)

    async def get_by_email(self, email: str):
        return await self._repo.get_by_email(email)


async def main():
    engine = init_engine()
    redis = init_redis()
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint",
                               expire_on_commit=False, autoflush=False)
        try:
            await run(session, redis)
        finally:
            await session.close()
            await outer.rollback()
    await close_engine()
    await close_redis()
    total, passed = len(_results), sum(_results)
    print(f"\n{'─'*40}")
    if passed == total:
        print(f"{GREEN}ALL PASS{RESET}  ({passed}/{total})  — DB 변경 없음(rollback)")
        return 0
    print(f"{RED}FAILED{RESET}  ({passed}/{total})")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
