"""Likes Repository — 좋아요 추가/삭제/조회."""

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.likes.models import Like


class LikeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def exists(self, *, user_id: UUID, log_id: UUID) -> bool:
        result = await self.session.execute(
            select(Like.id).where(
                Like.user_id == user_id,
                Like.climbing_log_id == log_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def add(self, *, user_id: UUID, log_id: UUID) -> bool:
        """좋아요 추가. 새로 추가하면 True, 이미 있으면 False (idempotent)."""
        if await self.exists(user_id=user_id, log_id=log_id):
            return False
        self.session.add(Like(user_id=user_id, climbing_log_id=log_id))
        await self.session.flush()
        return True

    async def remove(self, *, user_id: UUID, log_id: UUID) -> None:
        await self.session.execute(
            delete(Like).where(
                Like.user_id == user_id,
                Like.climbing_log_id == log_id,
            )
        )
        await self.session.flush()

    async def count(self, *, log_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Like)
            .where(Like.climbing_log_id == log_id)
        )
        return int(result.scalar_one())

    async def count_by_logs(
        self, log_ids: list[UUID]
    ) -> dict[UUID, int]:
        """여러 게시물의 좋아요 수를 한 번에 집계 (N+1 방지).

        반환: {log_id: count}. 좋아요 0 인 게시물은 dict 에 없음(호출측에서 0 처리).
        """
        if not log_ids:
            return {}
        result = await self.session.execute(
            select(Like.climbing_log_id, func.count())
            .where(Like.climbing_log_id.in_(log_ids))
            .group_by(Like.climbing_log_id)
        )
        return {row[0]: int(row[1]) for row in result.all()}

    async def liked_log_ids(
        self, *, user_id: UUID, log_ids: list[UUID]
    ) -> set[UUID]:
        """viewer 가 좋아요한 게시물 id 집합 (여러 게시물 한 번에)."""
        if not log_ids:
            return set()
        result = await self.session.execute(
            select(Like.climbing_log_id).where(
                Like.user_id == user_id,
                Like.climbing_log_id.in_(log_ids),
            )
        )
        return {row[0] for row in result.all()}
