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

    async def add(self, *, user_id: UUID, log_id: UUID) -> None:
        # UNIQUE 제약으로 중복은 DB 레벨에서 방지. 이미 있으면 무시(idempotent).
        if await self.exists(user_id=user_id, log_id=log_id):
            return
        self.session.add(Like(user_id=user_id, climbing_log_id=log_id))
        await self.session.flush()

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
