"""Comments 도메인 모델.

게시물(ClimbingLog) 댓글 — 대댓글(parent_id self-ref) + soft delete.

설계 근거 (댓글 상세 설계 문서):
- climbing_log_id 참조 (게시물 단위).
- parent_id self-ref: NULL=최상위, 값 있으면 대댓글. 인스타 방식으로
  대댓글의 대댓글도 최상위 parent 에 1depth 로 붙임 (호출측 정규화).
- is_pinned: 게시물 작성자가 고정 (여러 개 가능, Phase 3d).
- soft delete (deleted_at): 삭제해도 대댓글 트리 유지 위해.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.users.models import User
from app.infra.db.base import Base


class Comment(Base):
    """게시물 댓글."""

    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="댓글 작성자",
    )
    climbing_log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("climbing_logs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="대상 게시물",
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="대댓글 부모 (NULL=최상위)",
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_pinned: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="고정 여부 (게시물 작성자만 설정)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 작성자 relationship (author 표시용 — selectinload eager load)
    user: Mapped[User] = relationship(lazy="raise")
