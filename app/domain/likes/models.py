"""Likes 도메인 모델.

게시물(ClimbingLog) 좋아요 — 사용자당 게시물당 1개 (UNIQUE).

설계 근거 (소셜 확장 설계 문서):
- 원설계 ERD 는 video_id 기준이었으나 현재 구현(climbing_logs 게시물 단위)에
  맞춰 climbing_log_id 참조로 조정.
- UNIQUE(user_id, climbing_log_id): 중복 좋아요 방지 (한 번만).
- hard delete (좋아요 취소 = row 삭제). soft delete 불필요.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class Like(Base):
    """게시물 좋아요."""

    __tablename__ = "likes"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "climbing_log_id", name="uq_likes_user_log"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="좋아요 누른 사용자",
    )
    climbing_log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("climbing_logs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="좋아요 대상 게시물",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
