"""Notification service — 알림 생성 규칙 + 조회.

핵심 규칙:
- 자기 행동 제외: recipient == actor 면 알림 생성 안 함
  (내 게시물에 내가 좋아요/댓글, 내 댓글에 내가 대댓글)
- 좋아요는 토글이라 취소 시 알림도 삭제
"""

from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notifications.models import Notification
from app.domain.notifications.repository import NotificationRepository

logger = structlog.get_logger()


class NotificationService:
    def __init__(
        self,
        session: AsyncSession,
        repository: NotificationRepository,
    ):
        self.session = session
        self.repo = repository

    # ── 이벤트별 생성 (다른 서비스에서 호출) ──

    async def notify_post_like(
        self, *, recipient_id: UUID, actor_id: UUID, climbing_log_id: UUID
    ) -> None:
        """게시물 좋아요 알림. 자기 게시물 자기 좋아요는 제외."""
        if recipient_id == actor_id:
            return
        await self.repo.create(
            recipient_id=recipient_id,
            actor_id=actor_id,
            type="post_like",
            climbing_log_id=climbing_log_id,
        )
        logger.info(
            "notification_created",
            type="post_like",
            recipient=str(recipient_id),
            actor=str(actor_id),
        )

    async def notify_follow(
        self, *, recipient_id: UUID, actor_id: UUID
    ) -> None:
        """팔로우 알림. actor 가 recipient 를 팔로우. 자기 팔로우는 제외."""
        if recipient_id == actor_id:
            return
        await self.repo.create(
            recipient_id=recipient_id,
            actor_id=actor_id,
            type="follow",
        )
        logger.info(
            "notification_created",
            type="follow",
            recipient=str(recipient_id),
            actor=str(actor_id),
        )

    async def remove_post_like(
        self, *, actor_id: UUID, climbing_log_id: UUID
    ) -> None:
        """게시물 좋아요 취소 시 알림 삭제."""
        await self.repo.delete_by_target(
            actor_id=actor_id,
            type="post_like",
            climbing_log_id=climbing_log_id,
        )

    async def remove_follow(
        self, *, actor_id: UUID, recipient_id: UUID
    ) -> None:
        """언팔로우 시 팔로우 알림 삭제 (좋아요 취소와 대칭)."""
        await self.repo.delete_follow(
            actor_id=actor_id, recipient_id=recipient_id
        )

    async def notify_post_comment(
        self,
        *,
        recipient_id: UUID,
        actor_id: UUID,
        climbing_log_id: UUID,
        comment_id: UUID,
    ) -> None:
        """게시물 댓글 알림 (최상위 댓글). 자기 게시물 자기 댓글은 제외."""
        if recipient_id == actor_id:
            return
        await self.repo.create(
            recipient_id=recipient_id,
            actor_id=actor_id,
            type="post_comment",
            climbing_log_id=climbing_log_id,
            comment_id=comment_id,
        )
        logger.info(
            "notification_created",
            type="post_comment",
            recipient=str(recipient_id),
            actor=str(actor_id),
        )

    async def notify_comment_reply(
        self,
        *,
        recipient_id: UUID,
        actor_id: UUID,
        climbing_log_id: UUID,
        comment_id: UUID,
    ) -> None:
        """대댓글 알림 (부모 댓글 작성자에게). 자기 댓글 자기 대댓글은 제외."""
        if recipient_id == actor_id:
            return
        await self.repo.create(
            recipient_id=recipient_id,
            actor_id=actor_id,
            type="comment_reply",
            climbing_log_id=climbing_log_id,
            comment_id=comment_id,
        )
        logger.info(
            "notification_created",
            type="comment_reply",
            recipient=str(recipient_id),
            actor=str(actor_id),
        )

    async def notify_media_ready(
        self, *, recipient_id: UUID, climbing_log_id: UUID
    ) -> None:
        """영상 압축 완료 알림 (시스템 → 작성자 본인).

        시스템 알림이라 actor=recipient(본인)여도 생성 (자기 행동 제외 안 함).
        """
        await self.repo.create(
            recipient_id=recipient_id,
            actor_id=recipient_id,  # 시스템 알림 (본인)
            type="media_ready",
            climbing_log_id=climbing_log_id,
        )
        logger.info(
            "notification_created",
            type="media_ready",
            recipient=str(recipient_id),
        )

    async def notify_media_failed(
        self, *, recipient_id: UUID, climbing_log_id: UUID
    ) -> None:
        """영상 압축 실패 알림 (시스템 → 작성자 본인)."""
        await self.repo.create(
            recipient_id=recipient_id,
            actor_id=recipient_id,
            type="media_failed",
            climbing_log_id=climbing_log_id,
        )
        logger.info(
            "notification_created",
            type="media_failed",
            recipient=str(recipient_id),
        )

    # ── 조회 (API 용) ──

    async def list_notifications(
        self, *, user_id: UUID, limit: int = 30, offset: int = 0
    ) -> list[Notification]:
        return await self.repo.list_by_recipient(
            user_id, limit=limit, offset=offset
        )

    async def count_unread(self, *, user_id: UUID) -> int:
        return await self.repo.count_unread(user_id)

    async def mark_all_read(self, *, user_id: UUID) -> None:
        await self.repo.mark_all_read(user_id)
        await self.session.commit()
        logger.info("notifications_marked_read", user=str(user_id))
