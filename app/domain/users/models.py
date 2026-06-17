"""User 도메인 모델.

ERD 의 users 테이블 SQLAlchemy 2.0 ORM 매핑.

특징:
- UUID PK (pgcrypto.gen_random_uuid())
- 카카오 OAuth 우선 (auth_provider, auth_provider_id)
- soft delete (deleted_at)
- created_at / updated_at 자동 관리

Spring/JPA 와 비교:
- @Entity → Base 상속
- @Id → primary_key=True
- @Column → mapped_column
- @CreatedDate → server_default=NOW()
- @LastModifiedDate → onupdate=NOW()
"""

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class User(Base):
    """사용자 모델.

    카카오 OAuth 가입 사용자.
    """

    __tablename__ = "users"

    __table_args__ = (
        # 같은 OAuth 프로바이더의 같은 ID 는 한 명만
        UniqueConstraint("auth_provider", "auth_provider_id", name="uq_users_oauth"),
    )

    # ── 식별자 ──
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        # PostgreSQL 의 pgcrypto.gen_random_uuid() 사용
        # Day 7 에 pgcrypto extension 활성화함
        server_default=text("gen_random_uuid()"),
    )

    # ── 식별 정보 ──
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        comment="이메일 (카카오에서 받아옴)",
    )

    nickname: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        comment="닉네임 (사용자가 직접 설정)",
    )

    # ── 프로필 ──
    profile_image_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="프로필 사진 URL",
    )

    bio: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="자기소개",
    )

    # ── OAuth ──
    # 현재는 kakao 만, 나중에 google 등 추가 가능
    auth_provider: Mapped[Literal["kakao", "google", "apple"]] = mapped_column(
        String(20),
        nullable=False,
        default="kakao",
        comment="OAuth 프로바이더",
    )

    auth_provider_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="프로바이더의 사용자 ID",
    )

    is_public: Mapped[bool] = mapped_column(
        "is_public",
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="프로필 공개 여부 (Day 14)",
    )

    # ── 메타 (자동 관리) ──
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="가입 시각",
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="마지막 수정 시각",
    )

    # ── Soft Delete ──
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="삭제 시각 (soft delete)",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} nickname={self.nickname!r}>"
