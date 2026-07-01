"""Comments Repository — CRUD + 게시물별 목록 (대댓글 포함)."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.comments.models import Comment


class CommentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, comment_id: UUID) -> Comment | None:
        result = await self.session.execute(
            select(Comment)
            .options(selectinload(Comment.user))
            .where(
                Comment.id == comment_id,
                Comment.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_by_log(self, log_id: UUID) -> list[Comment]:
        """게시물의 모든 활성 댓글 (최상위 + 대댓글 전부).

        정렬: 고정 먼저 → 생성순. 대댓글 트리 정규화는 service 에서.
        """
        result = await self.session.execute(
            select(Comment)
            .options(selectinload(Comment.user))
            .where(
                Comment.climbing_log_id == log_id,
                Comment.deleted_at.is_(None),
            )
            .order_by(
                Comment.is_pinned.desc(),
                Comment.created_at.asc(),
            )
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        user_id: UUID,
        log_id: UUID,
        content: str,
        parent_id: UUID | None,
    ) -> Comment:
        comment = Comment(
            user_id=user_id,
            climbing_log_id=log_id,
            content=content,
            parent_id=parent_id,
        )
        self.session.add(comment)
        await self.session.flush()
        return comment

    async def update_content(self, comment: Comment, content: str) -> Comment:
        comment.content = content
        await self.session.flush()
        return comment

    async def soft_delete(self, comment: Comment) -> None:
        comment.deleted_at = func.now()
        await self.session.flush()

    async def count_by_log(self, log_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Comment)
            .where(
                Comment.climbing_log_id == log_id,
                Comment.deleted_at.is_(None),
            )
        )
        return int(result.scalar_one())
