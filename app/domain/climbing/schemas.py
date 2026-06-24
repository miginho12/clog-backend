"""Climbing 도메인 Pydantic schemas."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─────────────────────────────────────────
#  상수 — 추천 카테고리 태그 (자유 입력도 허용)
# ─────────────────────────────────────────
# 프론트 선택 UI 추천 목록. 백엔드는 자유 입력을 막지 않음(검증만 길이).
SUGGESTED_CATEGORIES = [
    "다이나믹",
    "스태틱",
    "슬랩",
    "오버행",
    "크림프",
    "슬로퍼",
    "밸런스",
    "파워",
    "맨틀링",
    "코디네이션",
    "런지",
    "힐훅",
    "토훅",
    "캠퍼스",
    "다이히드럴",
    "피지컬",
]

GradeSystem = Literal["v_scale", "color"]
Visibility = Literal["public", "private"]
MediaType = Literal["video", "image"]


class ClimbingLogBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ─────────────────────────────────────────
#  요청 — 작성
# ─────────────────────────────────────────
class ClimbingLogCreate(BaseModel):
    """클라이밍 기록 작성 요청."""

    grade_raw: str = Field(..., min_length=1, max_length=50, examples=["V4"])
    grade_system: GradeSystem = "v_scale"
    gym_name: str | None = Field(None, max_length=100, examples=["더클라임 강남"])
    categories: list[str] = Field(
        default_factory=list,
        examples=[["오버행", "다이나믹"]],
    )
    comment: str | None = Field(None, max_length=2000)
    attempts: int = Field(1, ge=1, le=9999)
    is_success: bool = False
    climbed_at: date | None = None  # None 이면 DB default(오늘)
    media_type: MediaType | None = None
    media_url: str | None = Field(None, max_length=500)
    visibility: Visibility = "public"

    @field_validator("categories")
    @classmethod
    def validate_categories(cls, v: list[str]) -> list[str]:
        # 각 태그 길이 제한 + 중복 제거 + 최대 개수
        cleaned = []
        seen = set()
        for tag in v:
            tag = tag.strip()
            if not tag or tag in seen:
                continue
            if len(tag) > 30:
                raise ValueError(f"카테고리 태그는 30자 이하: {tag}")
            seen.add(tag)
            cleaned.append(tag)
        if len(cleaned) > 10:
            raise ValueError("카테고리는 최대 10개까지")
        return cleaned


# ─────────────────────────────────────────
#  요청 — 수정 (부분 업데이트)
# ─────────────────────────────────────────
class ClimbingLogUpdate(BaseModel):
    """클라이밍 기록 수정 요청 (모든 필드 선택)."""

    grade_raw: str | None = Field(None, min_length=1, max_length=50)
    grade_system: GradeSystem | None = None
    gym_name: str | None = Field(None, max_length=100)
    categories: list[str] | None = None
    comment: str | None = Field(None, max_length=2000)
    attempts: int | None = Field(None, ge=1, le=9999)
    is_success: bool | None = None
    climbed_at: date | None = None
    media_type: MediaType | None = None
    media_url: str | None = Field(None, max_length=500)
    visibility: Visibility | None = None

    @field_validator("categories")
    @classmethod
    def validate_categories(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        cleaned, seen = [], set()
        for tag in v:
            tag = tag.strip()
            if not tag or tag in seen:
                continue
            if len(tag) > 30:
                raise ValueError(f"카테고리 태그는 30자 이하: {tag}")
            seen.add(tag)
            cleaned.append(tag)
        if len(cleaned) > 10:
            raise ValueError("카테고리는 최대 10개까지")
        return cleaned


# ─────────────────────────────────────────
#  응답
# ─────────────────────────────────────────
class ClimbingLogAuthor(ClimbingLogBase):
    """기록 작성자 요약 (피드/상세에 같이 노출)."""

    id: UUID
    nickname: str
    profile_image_url: str | None = None


class ClimbingLogResponse(ClimbingLogBase):
    """클라이밍 기록 응답."""

    id: UUID
    user_id: UUID
    grade_raw: str
    grade_system: str
    gym_name: str | None
    categories: list[str]
    comment: str | None
    attempts: int
    is_success: bool
    climbed_at: date
    media_type: str | None
    media_url: str | None
    visibility: str
    created_at: datetime
    updated_at: datetime


class ClimbingLogListResponse(BaseModel):
    """피드 응답 (페이지네이션)."""

    items: list[ClimbingLogResponse]
    page: int
    page_size: int
    has_next: bool
