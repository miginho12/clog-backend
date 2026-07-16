"""승인 팔로워의 비공개 계정 글 접근 검증 (rollback — 실제 변경 없음).

비공개 계정 priv 의 public 글에 대해:
- 비팔로워/pending 요청자: 안 보임
- accepted 팔로워: 보임
- 본인: 보임
공개 계정 회귀 없음.

실행:
    uv run python -m scripts.verify_feed_follower_access
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.domain.climbing.repository import ClimbingRepository
from app.domain.follows.repository import FollowRepository
from app.domain.follows.service import FollowService
from app.domain.notifications.repository import NotificationRepository
from app.domain.notifications.service import NotificationService
from app.domain.users.models import User
from app.domain.users.repository import UserRepository
from app.infra.db.engine import close_engine, init_engine

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
_results: list[bool] = []


def check(name, ok):
    _results.append(ok)
    print(f"  [{(GREEN+'PASS' if ok else RED+'FAIL')+RESET}] {name}")


def make_user(nick, *, is_public=True):
    uid = uuid.uuid4().hex[:8]
    return User(email=f"ffa_{uid}@clog.test", nickname=f"{nick}_{uid}",
               auth_provider="local", email_verified=True, is_public=is_public)


def fsvc(session):
    return FollowService(
        session=session, repository=FollowRepository(session),
        user_repo=UserRepository(session),
        notification_service=NotificationService(session, NotificationRepository(session)),
    )


async def run(session: AsyncSession):
    crepo = ClimbingRepository(session)
    fs = fsvc(session)

    priv = make_user("priv", is_public=False)
    follower = make_user("follower")   # 수락될 팔로워
    pender = make_user("pender")       # 요청만 한 사람
    stranger = make_user("stranger")   # 무관
    session.add_all([priv, follower, pender, stranger])
    await session.flush()

    log = await crepo.create(
        user_id=priv.id, grade_raw="V4", grade_system="v_scale",
        gym_name="짐", categories=[], attempts=1, is_success=True,
        visibility="public",
    )
    await session.flush()
    lid = log.id

    # follower 는 요청 → priv 수락 / pender 는 요청만
    await fs.follow(follower_id=follower.id, following_id=priv.id)
    await fs.accept_request(owner_id=priv.id, requester_id=follower.id)
    await fs.follow(follower_id=pender.id, following_id=priv.id)  # pending 유지

    def ids(rows): return {r.id for r in rows}

    print("\n[비공개 계정 글 접근]")
    r, _ = await crepo.list_feed(viewer_id=stranger.id)
    check("무관한 사람: 안 보임", lid not in ids(r))

    r, _ = await crepo.list_feed(viewer_id=pender.id)
    check("요청(pending)만 한 사람: 안 보임", lid not in ids(r))

    r, _ = await crepo.list_feed(viewer_id=follower.id)
    check("수락된 팔로워: 보임", lid in ids(r))

    r, _ = await crepo.list_feed(viewer_id=priv.id)
    check("본인: 보임", lid in ids(r))

    r, _ = await crepo.list_feed(viewer_id=None)
    check("비로그인: 안 보임", lid not in ids(r))

    print("\n[author_id 필터로 비공개 프로필 조회]")
    r, _ = await crepo.list_feed(viewer_id=follower.id, author_id=priv.id)
    check("수락 팔로워가 priv 프로필 글 조회 → 보임", lid in ids(r))
    r, _ = await crepo.list_feed(viewer_id=pender.id, author_id=priv.id)
    check("pending 이 priv 프로필 글 조회 → 안 보임", lid not in ids(r))


async def main():
    engine = init_engine()
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint",
                               expire_on_commit=False, autoflush=False)
        try:
            await run(session)
        finally:
            await session.close()
            await outer.rollback()
    await close_engine()
    total, passed = len(_results), sum(_results)
    print(f"\n{'─'*40}")
    if passed == total:
        print(f"{GREEN}ALL PASS{RESET}  ({passed}/{total})  — DB 변경 없음(rollback)")
        return 0
    print(f"{RED}FAILED{RESET}  ({passed}/{total})")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
