"""유저 검색 + 비공개 정책 검증 (rollback — 실제 변경 없음).

Step 1: search_by_nickname — 부분검색, 접두 우선, 탈퇴/차단/본인 제외
Step 2: get_user_for_viewer — 비공개여도 UserProfilePrivate 안 던지고 반환

실행:
    uv run python -m scripts.verify_user_search
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
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


def make_user(nickname, *, is_public=True, is_banned=False, deleted=False) -> User:
    from datetime import UTC, datetime

    uid = uuid.uuid4().hex[:8]
    u = User(
        email=f"srch_{uid}@clog.test",
        nickname=nickname,
        auth_provider="local",
        email_verified=True,
        is_public=is_public,
        is_banned=is_banned,
    )
    if deleted:
        u.deleted_at = datetime.now(UTC)
    return u


async def run(session: AsyncSession) -> None:
    repo = UserRepository(session)
    users = UserService(session, repo)

    # 고유 접두어로 이 테스트 사용자만 걸리게
    P = f"zz{uuid.uuid4().hex[:6]}"
    climber = make_user(f"{P}_climber")       # 접두 일치
    climbing = make_user(f"{P}_climbing")     # 접두 일치
    myclimber = make_user(f"my{P}_climber")   # 부분 일치(접두 아님)
    banned = make_user(f"{P}_banned", is_banned=True)
    gone = make_user(f"{P}_gone", deleted=True)
    private = make_user(f"{P}_private", is_public=False)
    viewer = make_user(f"{P}_viewer")

    session.add_all([climber, climbing, myclimber, banned, gone, private, viewer])
    await session.flush()

    print("\n[검색 — 부분/접두/제외]")
    items, has_next = await users.search_users(query=P, viewer_id=viewer.id)
    names = [u.nickname for u in items]

    check("부분검색으로 접두·부분 일치 모두 잡힘",
          climber.nickname in names and myclimber.nickname in names)
    check("차단 사용자 제외", banned.nickname not in names)
    check("탈퇴 사용자 제외", gone.nickname not in names)
    check("본인(viewer) 제외", viewer.nickname not in names)
    check("비공개 사용자도 검색됨", private.nickname in names)
    # 접두 일치가 부분 일치보다 앞: climber/climbing 이 myclimber 보다 먼저
    idx_prefix = min(names.index(climber.nickname), names.index(climbing.nickname))
    idx_partial = names.index(myclimber.nickname)
    check("접두 일치가 부분 일치보다 먼저 정렬", idx_prefix < idx_partial)

    print("\n[페이지네이션]")
    pg, hn = await users.search_users(query=P, viewer_id=viewer.id, page=1, page_size=2)
    check("page_size=2 → 2건 반환", len(pg) == 2)
    check("has_next=True (더 있음)", hn is True)

    print("\n[비공개 정책 — 메타는 공개]")
    got = await users.get_user_for_viewer(
        target_user_id=private.id, viewer_user_id=viewer.id
    )
    check("비공개 프로필도 예외 없이 반환됨", got.id == private.id)
    check("반환된 프로필의 is_public=False", got.is_public is False)
    check("bio 등 메타 접근 가능", hasattr(got, "nickname") and got.nickname == private.nickname)


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
