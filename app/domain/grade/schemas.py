"""Grade 도메인 스키마.

산정 결과 DTO. v_scale 트랙은 구현 2, color 트랙은 구현 3.
GET /me/grade 응답은 두 트랙 중첩 (구현 5).
"""

from pydantic import BaseModel


class VScaleGrade(BaseModel):
    """v_scale 트랙 산정 결과 (ADR-042)."""

    comprehensive_score: float  # 상위 N개 contribution 단순 산술평균
    top_rating: int | None  # 완등 기록 중 최고 V 숫자 (없으면 None)
    top_rating_label: str | None  # "V{n}" 표기 (없으면 None)
    counted_logs: int  # 종합점수에 반영된 기록 수 (top N)


class ColorGrade(BaseModel):
    """color 트랙 산정 결과 (ADR-041, ADR-042).

    difficulty 는 각 기록의 '자기 짐 내 비율(ratio)' 로 정해지므로
    종합점수는 기준짐과 무관. base_gym 은 탑레이팅 색 라벨 투영에만 사용.
    """

    comprehensive_score: float  # 상위 N개 contribution 단순 산술평균
    base_gym: str | None  # 기준짐 (탑레이팅 색 투영 기준, 없으면 None)
    top_rating_label: str | None  # 완등 최고 ratio → base_gym 투영 색
    counted_logs: int  # 종합점수에 반영된 기록 수 (top N)


class MeGradeResponse(BaseModel):
    """GET /me/grade 응답 — v_scale + color 두 트랙 중첩 (구현 5)."""

    v_scale: VScaleGrade
    color: ColorGrade
