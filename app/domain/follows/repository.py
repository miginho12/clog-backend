"""Follows Repository — 팔로우 추가/삭제/조회 (likes 패턴)."""
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.follows.models import Follow
from app.domain.users.models import User


class FollowRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def exists(self, *, follower_id: UUID, following_id: UUID) -> bool:
        result = await self.session.execute(
            select(Follow.id).where(
                Follow.follower_id == follower_id,
                Follow.following_id == following_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def add(self, *, follower_id: UUID, following_id: UUID) -> bool:
        """팔로우 추가. 새로 추가 True, 이미 있으면 False (idempotent)."""
        if await self.exists(
            follower_id=follower_id, following_id=following_id
        ):
            return False
        self.session.add(
            Follow(follower_id=follower_id, following_id=following_id)
        )
        await self.session.flush()
        return True

    async def remove(self, *, follower_id: UUID, following_id: UUID) -> None:
        await self.session.execute(
            delete(Follow).where(
                Follow.follower_id == follower_id,
                Follow.following_id == following_id,
            )
        )
        await self.session.flush()

    async def count_followers(self, *, user_id: UUID) -> int:
        """이 사용자를 팔로우하는 사람 수 (탈퇴 사용자 제외)."""
        result = await self.session.execute(
            select(func.count())
            .select_from(Follow)
            .join(User, Follow.follower_id == User.id)
            .where(Follow.following_id == user_id, User.deleted_at.is_(None))
        )
        return int(result.scalar_one())

    async def count_following(self, *, user_id: UUID) -> int:
        """이 사용자가 팔로우하는 사람 수 (탈퇴 사용자 제외)."""
        result = await self.session.execute(
            select(func.count())
            .select_from(Follow)
            .join(User, Follow.following_id == User.id)
            .where(Follow.follower_id == user_id, User.deleted_at.is_(None))
        )
        return int(result.scalar_one())

    async def list_followers(self, *, user_id: UUID) -> list[User]:
        """이 사용자를 팔로우하는 사용자 목록 (최신순)."""
        result = await self.session.execute(
            select(User)
            .join(Follow, Follow.follower_id == User.id)
            .where(Follow.following_id == user_id, User.deleted_at.is_(None))
            .order_by(Follow.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_following(self, *, user_id: UUID) -> list[User]:
        """이 사용자가 팔로우하는 사용자 목록 (최신순)."""
        result = await self.session.execute(
            select(User)
            .join(Follow, Follow.following_id == User.id)
            .where(Follow.follower_id == user_id, User.deleted_at.is_(None))
            .order_by(Follow.created_at.desc())
        )
        return list(result.scalars().all())

    async def following_ids(
        self, *, follower_id: UUID, user_ids: list[UUID]
    ) -> set[UUID]:
        """viewer 가 팔로우 중인 사용자 id 집합 (여러 명 한 번에, N+1 방지).
        프로필/목록에서 각 사용자의 팔로우 여부 표시용.
        """
        if not user_ids:
            return set()
        result = await self.session.execute(
            select(Follow.following_id).where(
                Follow.follower_id == follower_id,
                Follow.following_id.in_(user_ids),
            )
        )
        return {row[0] for row in result.all()}
