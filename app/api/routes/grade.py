"""Grade 라우트 (구현 5).

엔드포인트:
- GET /me/grade   본인 그레이드 (v_scale + color 두 트랙). 인증 필수.

쿼리:
- base_gym (선택): color 탑레이팅 색을 어느 짐 기준으로 표시할지.
  미지정이면 최다기록 짐 자동. 미등록 짐이면 400(gym_grade_system_not_found).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUserDep
from app.domain.grade.dependencies import GradeServiceDep
from app.domain.grade.schemas import (
    MeGradeResponse,
    ProfileStats,
    TimelinePoint,
)

router = APIRouter(prefix="/me", tags=["grade"])


@router.get(
    "/grade",
    response_model=MeGradeResponse,
    summary="내 그레이드 (v_scale + color)",
    description=(
        "본인 클라이밍 기록 기반 종합점수/탑레이팅. "
        "v_scale·color 두 트랙을 각각 산정해 반환. "
        "color 의 base_gym 쿼리로 탑레이팅 색 기준짐 지정 가능."
    ),
)
async def get_my_grade(
    user: CurrentUserDep,
    service: GradeServiceDep,
    base_gym: Annotated[
        str | None,
        Query(description="color 탑레이팅 색 기준짐 (미지정 시 최다기록 짐)"),
    ] = None,
) -> MeGradeResponse:
    v_scale = await service.compute_v_scale_grade(user.id)
    color = await service.compute_color_grade(user.id, base_gym=base_gym)
    return MeGradeResponse(v_scale=v_scale, color=color)


@router.get(
    "/grade/timeline",
    response_model=list[TimelinePoint],
    summary="내 그레이드 추이 (주별)",
    description=(
        "최근 N주간 주별 종합점수 추이 (v_scale). "
        "각 주말 시점의 실력 점수를 반감기 반영해 계산한 성장 곡선."
    ),
)
async def get_my_grade_timeline(
    user: CurrentUserDep,
    service: GradeServiceDep,
    weeks: Annotated[
        int, Query(ge=4, le=52, description="조회 주 수 (기본 12)")
    ] = 12,
) -> list[TimelinePoint]:
    points = await service.compute_grade_timeline(user.id, weeks=weeks)
    return [TimelinePoint(**p) for p in points]


# 프로필 통계는 /users prefix (타인 조회 가능, 인증 불필요)
stats_router = APIRouter(prefix="/users", tags=["grade"])


@stats_router.get(
    "/{user_id}/stats",
    response_model=ProfileStats,
    summary="클라이머 프로필 통계",
    description="완등 수, 현재 실력 지수, 최고 등급(짐 명시). 프로필 표시용.",
)
async def get_user_stats(
    user_id: UUID,
    service: GradeServiceDep,
) -> ProfileStats:
    stats = await service.compute_profile_stats(user_id)
    return ProfileStats(**stats)
