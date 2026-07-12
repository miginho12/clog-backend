"""Follows 도메인 모델.

사용자 간 팔로우 관계 (follower → following).
- follower_id: 팔로우 하는 사람 (구독자)
- following_id: 팔로우 받는 사람 (대상)
- UNIQUE(follower_id, following_id): 중복 팔로우 방지
- 자기 자신 팔로우 방지는 서비스 레이어에서 체크
- hard delete (언팔로우 = row 삭제). soft delete 불필요.
- likes 패턴을 따르되, 양쪽 모두 users 를 참조하는 self-referential M:N.
"""
import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class Follow(Base):
    """사용자 팔로우 관계."""

    __tablename__ = "follows"
    __table_args__ = (
        UniqueConstraint(
            "follower_id", "following_id", name="uq_follows_follower_following"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    follower_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="팔로우 하는 사용자 (구독자)",
    )
    following_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="팔로우 받는 사용자 (대상)",
    )
    # 팔로우 상태 (Day 25): pending(비공개 계정 승인 대기) / accepted(수락됨)
    # 공개 계정 팔로우는 즉시 accepted, 비공개 계정은 pending 요청.
    status: Mapped[Literal["pending", "accepted"]] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'accepted'"),
        index=True,
        comment="팔로우 상태 (pending | accepted)",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
