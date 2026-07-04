"""Likes Service — 좋아요 토글 (add/remove) + 대상 게시물 접근 검증."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.climbing.repository import ClimbingRepository
from app.domain.notifications.service import NotificationService
from app.domain.likes.exceptions import LikeTargetNotFound
from app.domain.likes.repository import LikeRepository

logger = get_logger(__name__)


class LikeService:
    def __init__(
        self,
        session: AsyncSession,
        repository: LikeRepository,
        climbing_repo: ClimbingRepository,
        notification_service: NotificationService,
    ):
        self.session = session
        self.repo = repository
        self.climbing_repo = climbing_repo
        self.notification_service = notification_service

    async def _assert_visible_target(
        self, *, log_id: UUID, viewer_id: UUID
    ):
        """좋아요 대상 게시물이 존재하고 볼 수 있는지 검증 후 log 반환.

        - 없거나 soft-deleted → NotFound
        - private + 본인 아님 → NotFound (존재 숨김)
        """
        log = await self.climbing_repo.get_by_id(log_id)
        if log is None:
            raise LikeTargetNotFound(str(log_id))
        if log.visibility == "private" and log.user_id != viewer_id:
            raise LikeTargetNotFound(str(log_id))
        return log

    async def like(self, *, user_id: UUID, log_id: UUID) -> int:
        """좋아요 추가 (idempotent). 최종 좋아요 수 반환."""
        log = await self._assert_visible_target(
            log_id=log_id, viewer_id=user_id
        )
        added = await self.repo.add(user_id=user_id, log_id=log_id)
        # 새로 추가된 경우에만 알림 (중복 좋아요는 알림 안 만듦)
        if added:
            await self.notification_service.notify_post_like(
                recipient_id=log.user_id,
                actor_id=user_id,
                climbing_log_id=log_id,
            )
        await self.session.commit()
        count = await self.repo.count(log_id=log_id)
        logger.info("like_added", log_id=str(log_id), user_id=str(user_id))
        return count

    async def unlike(self, *, user_id: UUID, log_id: UUID) -> int:
        """좋아요 취소 (idempotent). 최종 좋아요 수 반환."""
        await self._assert_visible_target(log_id=log_id, viewer_id=user_id)
        await self.repo.remove(user_id=user_id, log_id=log_id)
        # 좋아요 취소 시 관련 알림도 삭제
        await self.notification_service.remove_post_like(
            actor_id=user_id,
            climbing_log_id=log_id,
        )
        await self.session.commit()
        count = await self.repo.count(log_id=log_id)
        logger.info("like_removed", log_id=str(log_id), user_id=str(user_id))
        return count
