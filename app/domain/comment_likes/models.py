"""CommentLikes 도메인 모델.

댓글(Comment) 좋아요 — 사용자당 댓글당 1개 (UNIQUE).

설계 근거 (댓글 상세 설계 문서):
- likes(게시물 좋아요)와 동일 패턴, 대상만 comment_id.
- [DECISION] likes 테이블 재사용 대신 별도 테이블 — 관심사 분리, likes 오염 없음.
- UNIQUE(user_id, comment_id): 중복 방지.
- hard delete (취소 = row 삭제).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class CommentLike(Base):
    """댓글 좋아요."""

    __tablename__ = "comment_likes"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "comment_id", name="uq_comment_likes_user_comment"
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
    comment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="좋아요 대상 댓글",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
