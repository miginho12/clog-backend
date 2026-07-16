"""팔로우 승인제 검증 (rollback — 실제 변경 없음).

- 공개 계정 팔로우 → 즉시 accepted, 팔로워 수 +1
- 비공개 계정 팔로우 → pending, 팔로워 수 그대로(아직 아님), 요청함에 뜸
- 요청 수락 → accepted, 팔로워 수 +1, 요청함에서 사라짐
- 요청 거절 → 관계 삭제
- pending 중 언팔로우(취소) → 관계 삭제
- idempotent: 이미 관계 있으면 기존 상태 반환

실행:
    uv run python -m scripts.verify_follow_approval
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.domain.follows.exceptions import FollowRequestNotFound
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


def check(name: str, ok: bool) -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}")


def make_user(nickname, *, is_public=True) -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"fa_{uid}@clog.test",
        nickname=f"{nickname}_{uid}",
        auth_provider="local",
        email_verified=True,
        is_public=is_public,
    )


async def expect(coro, exc_type, name):
    try:
        await coro
        check(name, False)
    except exc_type:
        check(name, True)


def svc(session):
    return FollowService(
        session=session,
        repository=FollowRepository(session),
        user_repo=UserRepository(session),
        notification_service=NotificationService(
            session, NotificationRepository(session)
        ),
    )


async def run(session: AsyncSession) -> None:
    s = svc(session)
    repo = FollowRepository(session)

    pub_a = make_user("pub_a", is_public=True)
    pub_b = make_user("pub_b", is_public=True)
    priv = make_user("priv", is_public=False)
    req = make_user("requester", is_public=True)
    session.add_all([pub_a, pub_b, priv, req])
    await session.flush()

    print("\n[공개 계정 팔로우 → 즉시 accepted]")
    st = await s.follow(follower_id=pub_a.id, following_id=pub_b.id)
    check("반환 상태 accepted", st == "accepted")
    check("pub_b 팔로워 수 1", await repo.count_followers(user_id=pub_b.id) == 1)

    print("\n[비공개 계정 팔로우 → pending]")
    st = await s.follow(follower_id=pub_a.id, following_id=priv.id)
    check("반환 상태 pending", st == "pending")
    check("priv 팔로워 수 0 (아직 수락 전)", await repo.count_followers(user_id=priv.id) == 0)
    check("pub_a 팔로잉에 priv 미포함(accepted만)",
          priv.id not in await repo.following_ids(follower_id=pub_a.id, user_ids=[priv.id]))
    check("priv 요청함에 pub_a 있음", await repo.count_pending_requests(user_id=priv.id) == 1)

    print("\n[idempotent — 다시 팔로우하면 기존 상태]")
    st = await s.follow(follower_id=pub_a.id, following_id=priv.id)
    check("중복 요청 → pending 반환", st == "pending")
    check("요청 여전히 1건", await repo.count_pending_requests(user_id=priv.id) == 1)

    print("\n[요청 수락]")
    await s.accept_request(owner_id=priv.id, requester_id=pub_a.id)
    check("priv 팔로워 수 1", await repo.count_followers(user_id=priv.id) == 1)
    check("요청함 비었음", await repo.count_pending_requests(user_id=priv.id) == 0)
    check("get_follow_status=accepted",
          await s.get_follow_status(follower_id=pub_a.id, following_id=priv.id) == "accepted")

    print("\n[요청 거절]")
    st = await s.follow(follower_id=req.id, following_id=priv.id)  # 새 요청
    check("req→priv pending", st == "pending")
    await s.reject_request(owner_id=priv.id, requester_id=req.id)
    check("거절 후 관계 없음(None)",
          await s.get_follow_status(follower_id=req.id, following_id=priv.id) is None)
    await expect(
        s.reject_request(owner_id=priv.id, requester_id=req.id),
        FollowRequestNotFound,
        "없는 요청 거절 → FollowRequestNotFound",
    )

    print("\n[pending 중 언팔로우 = 요청 취소]")
    await s.follow(follower_id=req.id, following_id=priv.id)  # 다시 요청
    await s.unfollow(follower_id=req.id, following_id=priv.id)
    check("요청 취소 후 관계 없음",
          await s.get_follow_status(follower_id=req.id, following_id=priv.id) is None)

    print("\n[수락할 요청 없을 때 accept → 에러]")
    await expect(
        s.accept_request(owner_id=priv.id, requester_id=pub_b.id),
        FollowRequestNotFound,
        "없는 요청 수락 → FollowRequestNotFound",
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
