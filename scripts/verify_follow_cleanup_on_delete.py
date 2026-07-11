"""탈퇴 시 팔로우 관계 정리 검증 (rollback — 실제 변경 없음).

시나리오: A→B, B→C 팔로우 상태에서 B 가 탈퇴하면
- B 관련 follows 행이 모두 사라짐
- C 의 팔로워 목록/카운트에서 B 가 빠짐
- A 의 팔로잉 목록/카운트에서 B 가 빠짐

실행:
    uv run python -m scripts.verify_follow_cleanup_on_delete
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.domain.follows.repository import FollowRepository
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
        email=f"fol_{uid}@clog.test",
        nickname=f"{nickname}_{uid}",
        auth_provider="local",
        email_verified=True,
    )


async def run(session: AsyncSession) -> None:
    urepo = UserRepository(session)
    users = UserService(session, urepo)
    frepo = FollowRepository(session)

    a, b, c = make_user("A"), make_user("B"), make_user("C")
    session.add_all([a, b, c])
    await session.flush()

    # A→B, B→C, A→C
    await frepo.add(follower_id=a.id, following_id=b.id)
    await frepo.add(follower_id=b.id, following_id=c.id)
    await frepo.add(follower_id=a.id, following_id=c.id)
    await session.flush()

    print("\n[탈퇴 전]")
    check("B 의 팔로워 수 1 (A)", await frepo.count_followers(user_id=b.id) == 1)
    check("C 의 팔로워 수 2 (A,B)", await frepo.count_followers(user_id=c.id) == 2)
    check("A 의 팔로잉 수 2 (B,C)", await frepo.count_following(user_id=a.id) == 2)

    print("\n[B 탈퇴]")
    await users.deactivate_account(user_id=b.id)

    print("\n[탈퇴 후]")
    check("C 의 팔로워 수 1 (A만)", await frepo.count_followers(user_id=c.id) == 1)
    c_followers = await frepo.list_followers(user_id=c.id)
    check("C 팔로워 목록에 B 없음", all(u.id != b.id for u in c_followers))
    check("A 의 팔로잉 수 1 (C만)", await frepo.count_following(user_id=a.id) == 1)
    a_following = await frepo.list_following(user_id=a.id)
    check("A 팔로잉 목록에 B 없음", all(u.id != b.id for u in a_following))


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
