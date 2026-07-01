"""CommentLikes Service — 댓글 좋아요 토글 + 대상 접근 검증."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.climbing.repository import ClimbingRepository
from app.domain.comment_likes.exceptions import CommentLikeTargetNotFound
from app.domain.comment_likes.repository import CommentLikeRepository
from app.domain.comments.repository import CommentRepository

logger = get_logger(__name__)


class CommentLikeService:
    def __init__(
        self,
        session: AsyncSession,
        repository: CommentLikeRepository,
        comment_repo: CommentRepository,
        climbing_repo: ClimbingRepository,
    ):
        self.session = session
        self.repo = repository
        self.comment_repo = comment_repo
        self.climbing_repo = climbing_repo

    async def _assert_visible_comment(
        self, *, comment_id: UUID, viewer_id: UUID
    ) -> None:
        """댓글이 존재하고, 그 게시물에 접근 가능한지 검증."""
        comment = await self.comment_repo.get_by_id(comment_id)
        if comment is None:
            raise CommentLikeTargetNotFound(str(comment_id))
        log = await self.climbing_repo.get_by_id(comment.climbing_log_id)
        if log is None:
            raise CommentLikeTargetNotFound(str(comment_id))
        if log.visibility == "private" and log.user_id != viewer_id:
            raise CommentLikeTargetNotFound(str(comment_id))

    async def like(self, *, user_id: UUID, comment_id: UUID) -> int:
        await self._assert_visible_comment(
            comment_id=comment_id, viewer_id=user_id
        )
        await self.repo.add(user_id=user_id, comment_id=comment_id)
        await self.session.commit()
        count = await self.repo.count(comment_id=comment_id)
        logger.info(
            "comment_like_added",
            comment_id=str(comment_id),
            user_id=str(user_id),
        )
        return count

    async def unlike(self, *, user_id: UUID, comment_id: UUID) -> int:
        await self._assert_visible_comment(
            comment_id=comment_id, viewer_id=user_id
        )
        await self.repo.remove(user_id=user_id, comment_id=comment_id)
        await self.session.commit()
        count = await self.repo.count(comment_id=comment_id)
        logger.info(
            "comment_like_removed",
            comment_id=str(comment_id),
            user_id=str(user_id),
        )
        return count
