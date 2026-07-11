"""비밀번호 변경 E2E 검증 (rollback — 실제 변경 없음).

Step 2(UserService.change_password) 를 검증한다.
savepoint 패턴으로 서비스 내부 commit 을 savepoint 로 가두고, 마지막에
바깥 트랜잭션을 rollback 하므로 DB 에 아무것도 남지 않는다.

실행:
    uv run python -m scripts.verify_password_change
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401 — 전체 모델 로드(FK 참조 등록)
from app.core.password import PasswordPolicyError, hash_password, verify_password
from app.domain.users.exceptions import (
    CurrentPasswordMismatch,
    PasswordChangeNotAllowed,
    UserNotFound,
)
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


def make_user(nickname: str, *, provider: str = "local", password: str | None = None) -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"pw_{uid}@clog.test",
        nickname=f"{nickname}_{uid}",
        auth_provider=provider,
        auth_provider_id=None if provider == "local" else f"oauth_{uid}",
        email_verified=True,
        password_hash=hash_password(password) if password else None,
    )


async def expect(coro, exc_type: type[Exception], name: str) -> None:
    try:
        await coro
        check(name, False)
    except exc_type:
        check(name, True)


async def run(session: AsyncSession) -> None:
    repo = UserRepository(session)
    users = UserService(session, repo)

    old_pw = "OldPass1234!@"
    new_pw = "NewPass5678!@"

    local = make_user("local", password=old_pw)
    kakao = make_user("kakao", provider="kakao")  # password_hash 없음
    session.add_all([local, kakao])
    await session.flush()

    print("\n[정상 변경]")
    await users.change_password(
        user_id=local.id, current_password=old_pw, new_password=new_pw
    )
    check("변경 후 새 비번으로 검증 성공", verify_password(new_pw, local.password_hash))
    check("변경 후 옛 비번은 실패", not verify_password(old_pw, local.password_hash))

    print("\n[실패 케이스]")
    await expect(
        users.change_password(
            user_id=local.id, current_password="wrong-password", new_password="Another9999!@"
        ),
        CurrentPasswordMismatch,
        "현재 비번 불일치 → CurrentPasswordMismatch",
    )
    await expect(
        users.change_password(
            user_id=kakao.id, current_password="x", new_password="Another9999!@"
        ),
        PasswordChangeNotAllowed,
        "OAuth 계정 변경 시도 → PasswordChangeNotAllowed",
    )
    await expect(
        users.change_password(
            user_id=local.id, current_password=new_pw, new_password="weak"
        ),
        PasswordPolicyError,
        "약한 새 비번 → PasswordPolicyError",
    )
    await expect(
        users.change_password(
            user_id=uuid.uuid4(), current_password="x", new_password="Another9999!@"
        ),
        UserNotFound,
        "없는 사용자 → UserNotFound",
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
            await outer.rollback()
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
