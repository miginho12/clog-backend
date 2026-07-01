"""CommentLikes Repository — 좋아요 추가/삭제/조회 + 배치 집계."""

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comment_likes.models import CommentLike


class CommentLikeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def exists(self, *, user_id: UUID, comment_id: UUID) -> bool:
        result = await self.session.execute(
            select(CommentLike.id).where(
                CommentLike.user_id == user_id,
                CommentLike.comment_id == comment_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def add(self, *, user_id: UUID, comment_id: UUID) -> None:
        if await self.exists(user_id=user_id, comment_id=comment_id):
            return
        self.session.add(
            CommentLike(user_id=user_id, comment_id=comment_id)
        )
        await self.session.flush()

    async def remove(self, *, user_id: UUID, comment_id: UUID) -> None:
        await self.session.execute(
            delete(CommentLike).where(
                CommentLike.user_id == user_id,
                CommentLike.comment_id == comment_id,
            )
        )
        await self.session.flush()

    async def count(self, *, comment_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(CommentLike)
            .where(CommentLike.comment_id == comment_id)
        )
        return int(result.scalar_one())

    async def count_by_comments(
        self, comment_ids: list[UUID]
    ) -> dict[UUID, int]:
        """여러 댓글의 좋아요 수 배치 집계 (N+1 방지)."""
        if not comment_ids:
            return {}
        result = await self.session.execute(
            select(CommentLike.comment_id, func.count())
            .where(CommentLike.comment_id.in_(comment_ids))
            .group_by(CommentLike.comment_id)
        )
        return {row[0]: int(row[1]) for row in result.all()}

    async def liked_comment_ids(
        self, *, user_id: UUID, comment_ids: list[UUID]
    ) -> set[UUID]:
        """viewer 가 좋아요한 댓글 id 집합 (배치)."""
        if not comment_ids:
            return set()
        result = await self.session.execute(
            select(CommentLike.comment_id).where(
                CommentLike.user_id == user_id,
                CommentLike.comment_id.in_(comment_ids),
            )
        )
        return {row[0] for row in result.all()}
