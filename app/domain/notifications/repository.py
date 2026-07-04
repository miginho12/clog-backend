"""Notification repository — 알림 CRUD + 조회."""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.notifications.models import Notification, NotificationType


class NotificationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        recipient_id: UUID,
        actor_id: UUID,
        type: NotificationType,
        climbing_log_id: UUID,
        comment_id: UUID | None = None,
    ) -> Notification:
        notification = Notification(
            recipient_id=recipient_id,
            actor_id=actor_id,
            type=type,
            climbing_log_id=climbing_log_id,
            comment_id=comment_id,
        )
        self.session.add(notification)
        await self.session.flush()
        return notification

    async def list_by_recipient(
        self, recipient_id: UUID, *, limit: int = 30, offset: int = 0
    ) -> list[Notification]:
        """받는 사람의 알림 목록 (최신순, actor eager load)."""
        result = await self.session.execute(
            select(Notification)
            .options(selectinload(Notification.actor))
            .where(Notification.recipient_id == recipient_id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count_unread(self, recipient_id: UUID) -> int:
        """안 읽은 알림 개수 (뱃지용)."""
        result = await self.session.execute(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.recipient_id == recipient_id,
                Notification.is_read.is_(False),
            )
        )
        return int(result.scalar_one())

    async def mark_all_read(self, recipient_id: UUID) -> None:
        """받는 사람의 모든 알림 읽음 처리."""
        await self.session.execute(
            update(Notification)
            .where(
                Notification.recipient_id == recipient_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True)
        )

    async def delete_by_target(
        self,
        *,
        actor_id: UUID,
        type: NotificationType,
        climbing_log_id: UUID,
        comment_id: UUID | None = None,
    ) -> None:
        """특정 이벤트의 알림 제거 (좋아요 취소 시 알림 삭제용).

        같은 actor + type + target 조합의 알림을 지운다.
        """
        conditions = [
            Notification.actor_id == actor_id,
            Notification.type == type,
            Notification.climbing_log_id == climbing_log_id,
        ]
        if comment_id is not None:
            conditions.append(Notification.comment_id == comment_id)
        stmt = select(Notification).where(*conditions)
        result = await self.session.execute(stmt)
        for n in result.scalars().all():
            await self.session.delete(n)
