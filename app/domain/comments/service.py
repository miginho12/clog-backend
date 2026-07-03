"""Comments Service — CRUD + 대댓글 트리 정규화 + 권한/메타 주입."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.climbing.repository import ClimbingRepository
from app.domain.comments.exceptions import (
    CommentForbidden,
    CommentNotFound,
    CommentParentInvalid,
    CommentTargetNotFound,
)
from app.domain.comments.models import Comment
from app.domain.comments.repository import CommentRepository
from app.domain.comment_likes.repository import CommentLikeRepository

logger = get_logger(__name__)


class CommentService:
    def __init__(
        self,
        session: AsyncSession,
        repository: CommentRepository,
        climbing_repo: ClimbingRepository,
        like_repo: CommentLikeRepository,
    ):
        self.session = session
        self.repo = repository
        self.climbing_repo = climbing_repo
        self.like_repo = like_repo

    async def _get_visible_log(self, *, log_id: UUID, viewer_id: UUID | None):
        """대상 게시물이 존재하고 볼 수 있는지 검증 후 반환."""
        log = await self.climbing_repo.get_by_id(log_id)
        if log is None:
            raise CommentTargetNotFound(str(log_id))
        if log.visibility == "private" and log.user_id != viewer_id:
            raise CommentTargetNotFound(str(log_id))
        return log

    def _attach_meta(
        self,
        comments: list[Comment],
        *,
        viewer_id: UUID | None,
        log_owner_id: UUID,
        reply_counts: dict[UUID, int] | None = None,
        like_counts: dict[UUID, int] | None = None,
        liked_ids: set[UUID] | None = None,
    ) -> None:
        """is_mine / can_pin / reply_count / like_count / liked_by_me 동적 주입."""
        rc = reply_counts or {}
        lc = like_counts or {}
        liked = liked_ids or set()
        for c in comments:
            c.is_mine = viewer_id is not None and c.user_id == viewer_id
            # 고정 권한 = 게시물 작성자 (댓글 작성자 아님)
            c.can_pin = viewer_id is not None and viewer_id == log_owner_id
            c.reply_count = rc.get(c.id, 0)
            c.like_count = lc.get(c.id, 0)
            c.liked_by_me = c.id in liked

    async def list_comments(
        self, *, log_id: UUID, viewer_id: UUID | None
    ) -> tuple[list[Comment], list[Comment], int, dict]:
        """게시물 댓글 목록.

        반환: (최상위 리스트, 대댓글 리스트, 전체 수, {parent_id: reply_count})
        대댓글 트리 정규화(1depth)는 라우터/응답 조립에서.
        """
        log = await self._get_visible_log(log_id=log_id, viewer_id=viewer_id)
        all_comments = await self.repo.list_by_log(log_id)

        tops = [c for c in all_comments if c.parent_id is None]
        replies = [c for c in all_comments if c.parent_id is not None]

        # 좋아요 배치 집계 (N+1 방지)
        all_ids = [c.id for c in all_comments]
        like_counts = await self.like_repo.count_by_comments(all_ids)
        liked_ids = (
            await self.like_repo.liked_comment_ids(
                user_id=viewer_id, comment_ids=all_ids
            )
            if viewer_id is not None
            else set()
        )

        # 대댓글 수 집계 (parent_id 기준)
        reply_counts: dict[UUID, int] = {}
        for r in replies:
            # 대댓글의 부모가 대댓글이면(2depth+) 최상위로 귀속시키는 정규화는
            # 데이터가 1depth 로만 생성되므로 parent_id 그대로 사용
            reply_counts[r.parent_id] = reply_counts.get(r.parent_id, 0) + 1

        self._attach_meta(
            tops,
            viewer_id=viewer_id,
            log_owner_id=log.user_id,
            reply_counts=reply_counts,
            like_counts=like_counts,
            liked_ids=liked_ids,
        )
        self._attach_meta(
            replies,
            viewer_id=viewer_id,
            log_owner_id=log.user_id,
            like_counts=like_counts,
            liked_ids=liked_ids,
        )

        total = len(all_comments)
        return tops, replies, total, reply_counts

    async def create_comment(
        self,
        *,
        user_id: UUID,
        log_id: UUID,
        content: str,
        parent_id: UUID | None,
    ) -> Comment:
        log = await self._get_visible_log(log_id=log_id, viewer_id=user_id)

        # 대댓글이면 부모 검증: 같은 게시물의 활성 댓글이어야
        if parent_id is not None:
            parent = await self.repo.get_by_id(parent_id)
            if parent is None or parent.climbing_log_id != log_id:
                raise CommentParentInvalid(str(parent_id))
            # 2depth+ 방지: 대댓글의 부모는 항상 최상위로 (인스타 방식)
            if parent.parent_id is not None:
                parent_id = parent.parent_id

        comment = await self.repo.create(
            user_id=user_id,
            log_id=log_id,
            content=content,
            parent_id=parent_id,
        )
        await self.session.commit()
        await self.session.refresh(comment, ["user"])
        comment.is_mine = True
        comment.can_pin = user_id == log.user_id
        comment.reply_count = 0
        comment.like_count = 0
        comment.liked_by_me = False
        logger.info(
            "comment_created",
            comment_id=str(comment.id),
            log_id=str(log_id),
        )
        return comment

    async def update_comment(
        self, *, comment_id: UUID, user_id: UUID, content: str
    ) -> Comment:
        comment = await self.repo.get_by_id(comment_id)
        if comment is None:
            raise CommentNotFound(str(comment_id))
        if comment.user_id != user_id:
            raise CommentForbidden(str(comment_id))
        comment = await self.repo.update_content(comment, content)
        await self.session.commit()
        await self.session.refresh(comment, ["user"])
        comment.is_mine = True
        comment.like_count = await self.like_repo.count(comment_id=comment.id)
        comment.liked_by_me = await self.like_repo.exists(
            user_id=user_id, comment_id=comment.id
        )
        logger.info("comment_updated", comment_id=str(comment_id))
        return comment

    async def delete_comment(
        self, *, comment_id: UUID, user_id: UUID
    ) -> None:
        comment = await self.repo.get_by_id(comment_id)
        if comment is None:
            raise CommentNotFound(str(comment_id))
        if comment.user_id != user_id:
            raise CommentForbidden(str(comment_id))
        await self.repo.soft_delete(comment)
        await self.session.commit()
        logger.info("comment_deleted", comment_id=str(comment_id))

    async def set_pin(
        self, *, comment_id: UUID, user_id: UUID, pinned: bool
    ) -> Comment:
        """댓글 고정/해제. 게시물 작성자만 가능 (여러 개 가능).

        댓글 작성자가 아니라 '게시물 소유자' 권한임에 주의.
        """
        comment = await self.repo.get_by_id(comment_id)
        if comment is None:
            raise CommentNotFound(str(comment_id))
        log = await self.climbing_repo.get_by_id(comment.climbing_log_id)
        if log is None:
            raise CommentNotFound(str(comment_id))
        if log.user_id != user_id:
            # 게시물 작성자만 고정 가능
            raise CommentForbidden(str(comment_id))
        comment = await self.repo.set_pinned(comment, pinned)
        await self.session.commit()
        await self.session.refresh(comment, ["user"])
        # 메타 재주입 (응답용)
        comment.is_mine = comment.user_id == user_id
        comment.can_pin = True
        comment.reply_count = 0
        comment.like_count = await self.like_repo.count(comment_id=comment.id)
        comment.liked_by_me = await self.like_repo.exists(
            user_id=user_id, comment_id=comment.id
        )
        logger.info(
            "comment_pin_set",
            comment_id=str(comment_id),
            pinned=pinned,
        )
        return comment
