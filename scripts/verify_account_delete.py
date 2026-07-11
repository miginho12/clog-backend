"""계정 탈퇴 E2E 검증 (rollback — 실제 변경 없음).

Step 3(UserService.deactivate_account) 를 검증한다.
- soft delete: deleted_at 세팅, get_by_id_active 에서 사라짐
- 익명화: email/nickname 이 deleted_ 접두 값으로 변경
- 재가입 가능성: 익명화 후 원래 email/nickname 슬롯이 비어 조회 시 None
- 멱등: 이미 탈퇴한 계정 재탈퇴 → UserNotFound

실행:
    uv run python -m scripts.verify_account_delete
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.core.password import hash_password
from app.domain.users.exceptions import UserNotFound
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


def make_user(nickname: str, *, provider: str = "local") -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"del_{uid}@clog.test",
        nickname=f"{nickname}_{uid}",
        auth_provider=provider,
        auth_provider_id=None if provider == "local" else f"oauth_{uid}",
        email_verified=True,
        password_hash=hash_password("SomePass1234!@") if provider == "local" else None,
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

    u = make_user("victim")
    session.add(u)
    await session.flush()

    orig_email = u.email
    orig_nick = u.nickname
    uid = u.id

    print("\n[탈퇴 처리]")
    await users.deactivate_account(user_id=uid)

    fresh = await repo.get_by_id(uid)  # 삭제 포함 조회
    check("deleted_at 세팅됨", fresh.deleted_at is not None)
    check("email 익명화됨", fresh.email.startswith("deleted_") and fresh.email.endswith("@deleted.clog"))
    check("nickname 익명화됨", fresh.nickname.startswith("deleted_"))

    active = await repo.get_by_id_active(uid)
    check("활성 조회에서 사라짐 (get_by_id_active=None)", active is None)

    print("\n[재가입 슬롯 반환]")
    check("원래 email 슬롯 비어있음", await repo.get_by_email(orig_email) is None)
    check("원래 nickname 슬롯 비어있음", await repo.get_by_nickname(orig_nick) is None)

    print("\n[멱등/에러]")
    await expect(
        users.deactivate_account(user_id=uid),
        UserNotFound,
        "이미 탈퇴한 계정 재탈퇴 → UserNotFound",
    )
    await expect(
        users.deactivate_account(user_id=uuid.uuid4()),
        UserNotFound,
        "없는 사용자 탈퇴 → UserNotFound",
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
