"""User 도메인 Pydantic schemas."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    """공통 필드."""

    model_config = ConfigDict(from_attributes=True)


class UserResponse(UserBase):
    """User 공개 응답 (전체 정보).

    인증된 본인 또는 admin 에게만 노출.
    """

    id: UUID
    email: EmailStr
    nickname: str
    profile_image_url: str | None = None
    bio: str | None = None
    auth_provider: str
    is_public: bool  # ⭐ Day 14
    is_admin: bool  # ⭐ admin (프론트 가드/버튼 노출용)
    created_at: datetime
    updated_at: datetime


class UserPublicResponse(UserBase):
    """User 공개 응답 (다른 사용자가 볼 때).

    민감 정보 제외 (email, auth_provider 등).
    is_public=True 인 사용자만 이 형태로 노출.
    """

    id: UUID
    nickname: str
    profile_image_url: str | None = None
    bio: str | None = None
    is_banned: bool = False  # admin 차단 UI 용
    created_at: datetime

class UserCreate(BaseModel):
    """회원 생성 요청 (Day 10 - OAuth 통합 전 시뮬레이션용)."""

    email: EmailStr = Field(..., description="이메일")
    nickname: str = Field(..., min_length=2, max_length=50)
    auth_provider: Literal["kakao", "google", "apple"] = Field(default="kakao")
    auth_provider_id: str = Field(..., min_length=1, max_length=255)
    profile_image_url: str | None = Field(default=None, max_length=500)
    bio: str | None = Field(default=None, max_length=500)

class UserUpdate(UserBase):
    """User 수정 요청 (PATCH /users/me).

    Day 14: is_public 추가, email 수정은 비활성 (OAuth 가입자라).
    """

    nickname: str | None = Field(default=None, min_length=2, max_length=50)
    bio: str | None = Field(default=None, max_length=500)
    profile_image_url: str | None = Field(default=None, max_length=2048)
    is_public: bool | None = None  # ⭐ Day 14


class AdminBanResponse(BaseModel):
    """admin 차단/해제 응답."""

    model_config = ConfigDict(from_attributes=True)

    user_id: UUID = Field(validation_alias="id")
    is_banned: bool
