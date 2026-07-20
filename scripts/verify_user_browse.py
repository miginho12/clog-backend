"""검색 탭 '클라이머' 브라우즈 모드 검증 (rollback — 실제 변경 없음).

GET /users/search 의 q 를 비워서 호출하면(검색어 없음) 검색어 필터 없이
전체 활성 유저를 닉네임순으로 반환해야 한다. 탈퇴/차단/본인은 그대로
제외되고, 검색어가 있을 때의 접두일치 우선 정렬은 그대로 동작해야 한다.

실행:
    uv run python -m scripts.verify_user_browse
"""

import asyncio
import sys
import uuid
from datetime import UTC

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.domain.users.models import User
from app.domain.users.repository import UserRepository
from app.infra.db.engine import close_engine, init_engine

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
_results: list[bool] = []


def check(name: str, ok: bool) -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}")


def make_user(nick: str, *, is_banned: bool = False, deleted: bool = False) -> User:
    uid = uuid.uuid4().hex[:8]
    from datetime import datetime

    return User(
        email=f"browse_{uid}@clog.test",
        nickname=f"{nick}_{uid}",
        auth_provider="local",
        email_verified=True,
        is_public=True,
        is_banned=is_banned,
        deleted_at=datetime.now(UTC) if deleted else None,
    )


async def run(session: AsyncSession) -> None:
    repo = UserRepository(session)

    me = make_user("aBrowseMe")
    active1 = make_user("bBrowseActive1")
    active2 = make_user("cBrowseActive2")
    banned = make_user("dBrowseBanned", is_banned=True)
    deleted = make_user("eBrowseDeleted", deleted=True)
    session.add_all([me, active1, active2, banned, deleted])
    await session.flush()

    # ── 브라우즈 모드 (q="") ──
    users, has_next = await repo.search_by_nickname(
        query="", exclude_user_id=me.id, page=1, page_size=50
    )
    ids = {u.id for u in users}
    check("브라우즈: 본인 제외", me.id not in ids)
    check("브라우즈: 활성 유저 포함", active1.id in ids and active2.id in ids)
    check("브라우즈: 차단 유저 제외", banned.id not in ids)
    check("브라우즈: 탈퇴 유저 제외", deleted.id not in ids)
    check("브라우즈: 닉네임 오름차순 정렬", users == sorted(users, key=lambda u: u.nickname))
    check("브라우즈: has_next False(페이지 안에 다 들어옴)", has_next is False)

    # ── 검색 모드는 기존과 동일하게 동작해야 함(회귀 확인) ──
    filtered, _ = await repo.search_by_nickname(
        query="BrowseActive1", exclude_user_id=me.id, page=1, page_size=20
    )
    filtered_ids = {u.id for u in filtered}
    check("검색 모드: 매칭 유저만", filtered_ids == {active1.id})


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
