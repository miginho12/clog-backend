"""짐 색체계(GymGradeSystem) 라우트 (구현 6).

엔드포인트:
- POST   /gym-grade-systems        등록 (인증 필수, 중복 gym_name → 409)
- GET    /gym-grade-systems        목록 (공개)
- GET    /gym-grade-systems/{id}   단건 (공개)
- PATCH  /gym-grade-systems/{id}   color_order 수정 (본인 비공식 등록분만)
- DELETE /gym-grade-systems/{id}   삭제 (본인 비공식 등록분만)

색순서는 객관적 공개 정보(ADR-041)라 조회는 비로그인 허용.
gym_name 은 climbing_logs 매칭 키라 불변 — 수정은 color_order 만.
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.dependencies import CurrentUserDep
from app.domain.grade.dependencies import GradeServiceDep
from app.domain.grade.schemas import (
    GymGradeSystemCreate,
    GymGradeSystemResponse,
    GymGradeSystemUpdate,
)

router = APIRouter(prefix="/gym-grade-systems", tags=["gym-grade-systems"])


@router.post(
    "",
    response_model=GymGradeSystemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="짐 색체계 등록",
)
async def create_gym_system(
    payload: GymGradeSystemCreate,
    user: CurrentUserDep,
    service: GradeServiceDep,
) -> GymGradeSystemResponse:
    system = await service.create_gym_system(
        gym_name=payload.gym_name,
        color_order=payload.color_order,
        user_id=user.id,
    )
    return GymGradeSystemResponse.model_validate(system)


@router.get(
    "",
    response_model=list[GymGradeSystemResponse],
    summary="짐 색체계 목록 (공개)",
)
async def list_gym_systems(
    service: GradeServiceDep,
) -> list[GymGradeSystemResponse]:
    systems = await service.list_gym_systems()
    return [GymGradeSystemResponse.model_validate(s) for s in systems]


@router.get(
    "/{system_id}",
    response_model=GymGradeSystemResponse,
    summary="짐 색체계 단건 (공개)",
)
async def get_gym_system(
    system_id: UUID,
    service: GradeServiceDep,
) -> GymGradeSystemResponse:
    system = await service.get_gym_system(system_id)
    return GymGradeSystemResponse.model_validate(system)


@router.patch(
    "/{system_id}",
    response_model=GymGradeSystemResponse,
    summary="짐 색체계 수정 (본인 등록분, color_order 만)",
)
async def update_gym_system(
    system_id: UUID,
    payload: GymGradeSystemUpdate,
    user: CurrentUserDep,
    service: GradeServiceDep,
) -> GymGradeSystemResponse:
    system = await service.update_gym_system(
        system_id=system_id,
        color_order=payload.color_order,
        user_id=user.id,
    )
    return GymGradeSystemResponse.model_validate(system)


@router.delete(
    "/{system_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="짐 색체계 삭제 (본인 등록분)",
)
async def delete_gym_system(
    system_id: UUID,
    user: CurrentUserDep,
    service: GradeServiceDep,
) -> None:
    await service.delete_gym_system(system_id=system_id, user_id=user.id)
