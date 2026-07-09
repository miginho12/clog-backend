"""Grade 도메인 스키마.

- 산정 결과 DTO: VScaleGrade, ColorGrade, MeGradeResponse (구현 2~5)
- 짐 색체계 등록/수정/응답: GymGradeSystem* (구현 6)
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ─────────────────────────────────────────
#  산정 결과 (구현 2~5)
# ─────────────────────────────────────────
class VScaleGrade(BaseModel):
    """v_scale 트랙 산정 결과 (ADR-042)."""

    comprehensive_score: float
    top_rating: int | None
    top_rating_label: str | None
    counted_logs: int


class ColorGrade(BaseModel):
    """color 트랙 산정 결과 (ADR-041, ADR-042).

    difficulty 는 각 기록의 '자기 짐 내 비율(ratio)' 로 정해지므로
    종합점수는 기준짐과 무관. base_gym 은 탑레이팅 색 라벨 투영에만 사용.
    """

    comprehensive_score: float
    base_gym: str | None
    top_rating_label: str | None
    counted_logs: int


class MeGradeResponse(BaseModel):
    """GET /me/grade 응답 — v_scale + color 두 트랙 중첩."""

    v_scale: VScaleGrade
    color: ColorGrade


# ─────────────────────────────────────────
#  짐 색체계 등록/수정/응답 (구현 6)
# ─────────────────────────────────────────
def _validate_color_order(v: list[str]) -> list[str]:
    """색 배열 검증: 각 색 strip, 빈 문자열/중복 불가, 최소 2단계."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for color in v:
        c = color.strip()
        if not c:
            raise ValueError("빈 색 이름은 허용되지 않습니다")
        if len(c) > 20:
            raise ValueError(f"색 이름은 20자 이하: {c}")
        if c in seen:
            raise ValueError(f"중복된 색: {c}")
        seen.add(c)
        cleaned.append(c)
    if len(cleaned) < 2:
        raise ValueError("color_order 는 최소 2단계 이상이어야 합니다")
    return cleaned


class GymGradeSystemCreate(BaseModel):
    """짐 색체계 등록 요청. color_order 는 쉬운→어려운 순."""

    gym_name: str = Field(..., min_length=1, max_length=100, examples=["클라이밍파크 신촌"])
    color_order: list[str] = Field(
        ...,
        examples=[["흰", "노", "주", "초", "파", "빨", "보", "검"]],
        description="쉬운→어려운 순 색 이름 배열 (인덱스 = rank)",
    )
    is_official: bool = Field(
        default=False,
        description="공식 암장 여부 — admin 만 True 로 등록 가능 (일반 사용자는 무시)",
    )

    @field_validator("gym_name")
    @classmethod
    def _strip_gym_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("gym_name 은 비어 있을 수 없습니다")
        return v

    @field_validator("color_order")
    @classmethod
    def _validate_colors(cls, v: list[str]) -> list[str]:
        return _validate_color_order(v)


class GymGradeSystemUpdate(BaseModel):
    """짐 색체계 수정 요청. color_order 만 수정 가능 (gym_name 불변)."""

    color_order: list[str] = Field(
        ..., examples=[["흰", "노", "주", "초", "파", "빨", "보", "회", "검"]]
    )

    @field_validator("color_order")
    @classmethod
    def _validate_colors(cls, v: list[str]) -> list[str]:
        return _validate_color_order(v)


class GymGradeSystemResponse(BaseModel):
    """짐 색체계 응답."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    gym_name: str
    color_order: list[str]
    is_official: bool
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class TimelinePoint(BaseModel):
    """그레이드 추이 한 점 (주별 스냅샷)."""

    date: str  # YYYY-MM-DD
    score: float
    count: int  # 그 시점 점수에 반영된 기록 수


class ProfileStats(BaseModel):
    """프로필 클라이머 통계."""

    success_count: int  # 총 완등 수
    total_count: int  # 전체 기록 수
    current_score: float  # 현재 실력 지수
    top_grade: str | None = None  # 최고 등급 라벨 (예: "V5" or "보")
    top_grade_gym: str | None = None  # color 최고등급 기준 짐 (color 일 때만)
    top_grade_system: str = "v_scale"  # "v_scale" or "color"
