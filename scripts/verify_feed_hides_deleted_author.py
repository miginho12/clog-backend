"""탈퇴 작성자 글 숨김 검증 (rollback — 실제 변경 없음).

Step 3.5: 사용자가 탈퇴(soft delete)하면 그의 climbing_logs 가
피드(list_feed)와 단건조회(get_by_id)에서 사라져야 한다.

실행:
    uv run python -m scripts.verify_feed_hides_deleted_author
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.domain.climbing.repository import ClimbingRepository
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


def make_user(nickname: str) -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"feed_{uid}@clog.test",
        nickname=f"{nickname}_{uid}",
        auth_provider="local",
        email_verified=True,
    )


async def run(session: AsyncSession) -> None:
    urepo = UserRepository(session)
    users = UserService(session, urepo)
    crepo = ClimbingRepository(session)

    author = make_user("author")
    session.add(author)
    await session.flush()

    # 공개글 1건 작성 (최소 필드)
    log = await crepo.create(
        user_id=author.id,
        grade_raw="V4",
        grade_system="v_scale",
        gym_name="테스트짐",
        categories=[],
        attempts=1,
        is_success=True,
        visibility="public",
    )
    await session.flush()
    log_id = log.id

    print("\n[탈퇴 전 — 노출됨]")
    got = await crepo.get_by_id(log_id)
    check("단건 조회 보임", got is not None)
    items, _ = await crepo.list_feed(viewer_id=None, author_id=author.id)
    check("피드에 보임", any(x.id == log_id for x in items))

    print("\n[작성자 탈퇴]")
    await users.deactivate_account(user_id=author.id)

    print("\n[탈퇴 후 — 숨김]")
    got2 = await crepo.get_by_id(log_id)
    check("단건 조회 숨김 (None)", got2 is None)
    items2, _ = await crepo.list_feed(viewer_id=None, author_id=author.id)
    check("피드에서 숨김", all(x.id != log_id for x in items2))
    items3, _ = await crepo.list_feed(viewer_id=None)
    check("전체 피드에서도 숨김", all(x.id != log_id for x in items3))


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
