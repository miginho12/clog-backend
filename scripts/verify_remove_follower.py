"""팔로워 삭제 + 요청 수 검증 (rollback — 실제 변경 없음).

- remove_follower: owner 가 자신의 팔로워를 끊음 → 그 팔로워의 팔로잉에서도 사라짐
- 언팔로우와 방향 구분: remove_follower 는 내가 following 인 관계만 지움(내가 follower 인 건 유지)
- count_pending_requests: 요청 수 정확

실행:
    uv run python -m scripts.verify_remove_follower
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.domain.follows.repository import FollowRepository
from app.domain.follows.service import FollowService
from app.domain.notifications.repository import NotificationRepository
from app.domain.notifications.service import NotificationService
from app.domain.users.models import User
from app.domain.users.repository import UserRepository
from app.infra.db.engine import close_engine, init_engine

GREEN = "\033[92m"; RED = "\033[91m"; RESET = "\033[0m"
_results: list[bool] = []


def check(name, ok):
    _results.append(ok)
    print(f"  [{(GREEN+'PASS' if ok else RED+'FAIL')+RESET}] {name}")


def make_user(nick, *, is_public=True):
    uid = uuid.uuid4().hex[:8]
    return User(email=f"rf_{uid}@clog.test", nickname=f"{nick}_{uid}",
               auth_provider="local", email_verified=True, is_public=is_public)


def svc(session):
    return FollowService(
        session=session, repository=FollowRepository(session),
        user_repo=UserRepository(session),
        notification_service=NotificationService(session, NotificationRepository(session)),
    )


async def run(session: AsyncSession):
    s = svc(session)
    repo = FollowRepository(session)

    owner = make_user("owner")
    fan = make_user("fan")        # owner 를 팔로우 (owner 의 팔로워)
    idol = make_user("idol")      # owner 가 팔로우 (owner 의 팔로잉)
    priv = make_user("priv", is_public=False)
    session.add_all([owner, fan, idol, priv])
    await session.flush()

    # fan → owner, owner → idol (둘 다 공개라 accepted)
    await s.follow(follower_id=fan.id, following_id=owner.id)
    await s.follow(follower_id=owner.id, following_id=idol.id)

    check("owner 팔로워 1 (fan)", await repo.count_followers(user_id=owner.id) == 1)
    check("owner 팔로잉 1 (idol)", await repo.count_following(user_id=owner.id) == 1)

    print("\n[팔로워 삭제 — owner 가 fan 을 끊음]")
    await s.remove_follower(owner_id=owner.id, follower_id=fan.id)
    check("owner 팔로워 0", await repo.count_followers(user_id=owner.id) == 0)
    check("fan 의 팔로잉에서 owner 사라짐",
          owner.id not in await repo.following_ids(follower_id=fan.id, user_ids=[owner.id]))
    check("owner 팔로잉(idol)은 그대로 유지",
          await repo.count_following(user_id=owner.id) == 1)

    print("\n[idempotent — 이미 없는 팔로워 삭제]")
    await s.remove_follower(owner_id=owner.id, follower_id=fan.id)
    check("에러 없이 통과 (여전히 0)", await repo.count_followers(user_id=owner.id) == 0)

    print("\n[요청 수 카운트]")
    await s.follow(follower_id=idol.id, following_id=priv.id)  # priv 에 요청
    check("priv 요청 수 1", await repo.count_pending_requests(user_id=priv.id) == 1)
    await s.reject_request(owner_id=priv.id, requester_id=idol.id)
    check("거절 후 요청 수 0", await repo.count_pending_requests(user_id=priv.id) == 0)


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
