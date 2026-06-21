"""User 도메인 모델.

ERD 의 users 테이블 SQLAlchemy 2.0 ORM 매핑.

특징:
- UUID PK (pgcrypto.gen_random_uuid())
- 카카오 OAuth + 자체 회원가입(local) 모두 지원 (auth_provider)
- soft delete (deleted_at)
- created_at / updated_at 자동 관리

Day 17 변경:
- auth_provider 에 "local" 추가 (자체 회원가입)
- password_hash 추가 (local 가입자만 값 존재, OAuth 가입자는 NULL)
- auth_provider_id nullable 화 (local 가입자는 OAuth ID 가 없음)
- is_admin 추가 (admin 가드용, 기본 False)

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

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class User(Base):
    """사용자 모델.

    카카오 OAuth 가입 + 자체 회원가입(local) 사용자.
    """

    __tablename__ = "users"

    __table_args__ = (
        # 같은 OAuth 프로바이더의 같은 ID 는 한 명만
        # local 가입자는 auth_provider_id 가 NULL 이므로 이 제약에 안 걸림
        # (PostgreSQL 은 NULL 을 서로 다른 값으로 취급하여 중복 NULL 허용)
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
        comment="이메일 (로그인 ID / 카카오에서 받아옴)",
    )

    nickname: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        comment="닉네임 (사용자가 직접 설정)",
    )

    # ── 인증 (Day 17 ⭐) ──
    # local 가입자만 password_hash 보유. OAuth 가입자는 NULL.
    password_hash: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="bcrypt 해시 (local 가입자만, OAuth 가입자는 NULL)",
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

    # ── OAuth / 인증 프로바이더 ──
    # Day 17: "local" 추가 (자체 회원가입)
    auth_provider: Mapped[Literal["local", "kakao", "google", "apple"]] = (
        mapped_column(
            String(20),
            nullable=False,
            default="kakao",
            comment="인증 프로바이더 (local | kakao | google | apple)",
        )
    )

    # Day 17: nullable 화 (local 가입자는 OAuth provider ID 가 없음)
    auth_provider_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="프로바이더의 사용자 ID (local 가입자는 NULL)",
    )

    is_public: Mapped[bool] = mapped_column(
        "is_public",
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="프로필 공개 여부 (Day 14)",
    )

    # ── 권한 (Day 17 ⭐) ──
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="관리자 여부 (admin 가드용)",
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
