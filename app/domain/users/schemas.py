"""User 도메인의 Pydantic 스키마.

Request/Response 분리 원칙:
- ORM 모델 (User) 을 직접 API 에 노출 X
- 클라이언트 입력 ≠ DB 저장 형태 ≠ API 응답

Spring 의 DTO 와 같은 개념.

예: deleted_at 같은 내부 필드는 응답에서 제외,
    auth_provider_id 는 응답에 노출하지 않음 (보안).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ─────────────────────────────────────────
#  Request (클라이언트 → 서버)
# ─────────────────────────────────────────


class UserCreate(BaseModel):
    """회원 생성 요청.

    실제 카카오 OAuth 통합 전 단계 (Day 10).
    auth_provider_id 는 OAuth 시뮬레이션을 위해 일단 받음.
    Day 11+ 에서 OAuth 통합 후 이 부분 변경 예정.
    """

    email: EmailStr = Field(
        ...,
        description="이메일 (Pydantic 이 형식 자동 검증)",
        examples=["climber@example.com"],
    )
    nickname: str = Field(
        ...,
        min_length=2,
        max_length=50,
        description="닉네임 (2~50자)",
        examples=["진호클라이머"],
    )
    auth_provider: Literal["kakao", "google", "apple"] = Field(
        default="kakao",
        description="OAuth 프로바이더",
    )
    auth_provider_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="OAuth 프로바이더의 사용자 ID",
        examples=["kakao_12345678"],
    )
    profile_image_url: str | None = Field(
        default=None,
        max_length=500,
        description="프로필 이미지 URL (선택)",
    )
    bio: str | None = Field(
        default=None,
        max_length=500,
        description="자기소개 (선택)",
    )


class UserUpdate(BaseModel):
    """회원 정보 수정 요청.

    모든 필드 선택 (부분 업데이트 PATCH 패턴).
    """

    nickname: str | None = Field(
        default=None,
        min_length=2,
        max_length=50,
    )
    profile_image_url: str | None = Field(default=None, max_length=500)
    bio: str | None = Field(default=None, max_length=500)


# ─────────────────────────────────────────
#  Response (서버 → 클라이언트)
# ─────────────────────────────────────────


class UserResponse(BaseModel):
    """회원 정보 응답.

    민감한 내부 필드 (deleted_at, auth_provider_id) 는 제외.
    """

    # SQLAlchemy 모델 → Pydantic 자동 변환 허용
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    nickname: str
    profile_image_url: str | None = None
    bio: str | None = None
    auth_provider: str
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    """회원 목록 응답 (페이지네이션 포함)."""

    items: list[UserResponse]
    total: int
    page: int = 1
    page_size: int = 20
