"""Grade 라우트 (구현 5).

엔드포인트:
- GET /me/grade   본인 그레이드 (v_scale + color 두 트랙). 인증 필수.

쿼리:
- base_gym (선택): color 탑레이팅 색을 어느 짐 기준으로 표시할지.
  미지정이면 최다기록 짐 자동. 미등록 짐이면 400(gym_grade_system_not_found).
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUserDep
from app.domain.grade.dependencies import GradeServiceDep
from app.domain.grade.schemas import MeGradeResponse

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
