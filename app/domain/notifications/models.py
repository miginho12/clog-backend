"""Notification 도메인 모델.

알림(Notification) — 소셜 이벤트를 받는 사람(recipient)에게 쌓임.

발생 이벤트 (type):
- post_like: 내 게시물에 좋아요
- post_comment: 내 게시물에 댓글
- comment_reply: 내 댓글에 대댓글
- follow: 나를 팔로우

설계:
- recipient_id: 알림 받는 사람 (내 게시물/댓글 주인)
- actor_id: 행동한 사람 (좋아요/댓글 단 사람)
- 자기 행동 제외는 service 에서 (recipient == actor 면 생성 안 함)
- climbing_log_id: 클릭 시 이동할 게시물 (게시물 관련 알림만, nullable)
- comment_id: 관련 댓글 (댓글/대댓글 알림만, nullable)
- is_read: 읽음 여부
"""

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.users.models import User
from app.infra.db.base import Base

NotificationType = Literal[
    "post_like",
    "post_comment",
    "comment_reply",
    "media_ready",
    "media_failed",
    "follow",
]


class Notification(Base):
    """소셜 알림."""

    __tablename__ = "notifications"
    __table_args__ = (
        # 받는 사람의 알림 목록 조회 (최신순) + 안읽음 필터
        Index("ix_notifications_recipient_created", "recipient_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="알림 받는 사람 user id",
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="행동한 사람 user id (좋아요/댓글 단 사람)",
    )
    type: Mapped[NotificationType] = mapped_column(
        String(30),
        nullable=False,
        comment="알림 유형 (post_like | post_comment | comment_reply)",
    )
    climbing_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("climbing_logs.id", ondelete="CASCADE"),
        nullable=True,
        comment="관련 게시물 (클릭 시 이동 대상)",
    )
    comment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=True,
        comment="관련 댓글 (댓글/대댓글 알림만)",
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="읽음 여부",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # actor 정보 (알림 목록에서 "누가" 표시용)
    actor: Mapped[User] = relationship(lazy="raise", foreign_keys=[actor_id])

    def __repr__(self) -> str:
        return (
            f"<Notification id={self.id!r} type={self.type!r} "
            f"recipient={self.recipient_id!r}>"
        )
