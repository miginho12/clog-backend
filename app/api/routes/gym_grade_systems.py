"""짐 색체계(GymGradeSystem) 라우트 (구현 6).

엔드포인트:
- POST   /gym-grade-systems              등록 (인증 필수, 중복 gym_name → 409)
- GET    /gym-grade-systems              목록 (공개, ?brand_name= 으로 브랜드 필터)
- GET    /gym-grade-systems/representatives  암장 간 난이도 환산 탭 대상 (공개, 공식+브랜드 대표/단독만)
- GET    /gym-grade-systems/ranking      암장 랭킹 (공개, ?gym_name= 필수, ?period=all|month|week — 공개 계정 공개 컬러 기록만)
- GET    /gym-grade-systems/{id}         단건 (공개)
- PATCH  /gym-grade-systems/{id}         color_order + brand_name 수정 (본인 비공식 등록분만),
                                          gym_name 개명은 admin 전용
- DELETE /gym-grade-systems/{id}         삭제 (본인 비공식 등록분만)

색순서는 객관적 공개 정보(ADR-041)라 조회는 비로그인 허용.
gym_name 은 climbing_logs 매칭 키(문자열, FK 아님) — brand_name 은 같은 브랜드
지점(예: "피커스")을 묶어보는 선택적 메타데이터로 color_order 와 함께 수정
가능. gym_name 자체를 바꾸는 개명은 admin 만 가능하고, 개명 시 과거
climbing_logs.gym_name 도 같은 트랜잭션에서 일괄 갱신되어 기존 기록의
그레이드 산정이 깨지지 않는다.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.dependencies import AdminUserDep, CurrentUserDep
from app.domain.grade.dependencies import GradeServiceDep
from app.domain.grade.schemas import (
    GymGradeSystemChangeResponse,
    GymGradeSystemCreate,
    GymGradeSystemResponse,
    GymGradeSystemUpdate,
    GymRankingResponse,
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
        is_admin=user.is_admin,
        is_official=payload.is_official,
        brand_name=payload.brand_name,
        difficulty_offset=payload.difficulty_offset,
        reason=payload.reason,
    )
    return GymGradeSystemResponse.model_validate(system)


@router.get(
    "",
    response_model=list[GymGradeSystemResponse],
    summary="짐 색체계 목록 (공개)",
)
async def list_gym_systems(
    service: GradeServiceDep,
    brand_name: str | None = Query(
        None, description="지정하면 같은 브랜드 지점만 필터"
    ),
) -> list[GymGradeSystemResponse]:
    systems = await service.list_gym_systems(brand_name=brand_name)
    return [GymGradeSystemResponse.model_validate(s) for s in systems]


# 고정 경로("/ranking")는 /{system_id}(UUID) 보다 반드시 먼저 등록한다.
# 둘 다 세그먼트 1개라 뒤에 두면 "ranking" 이 system_id 로 매칭돼
# UUID 파싱 에러(422)가 난다 (CLAUDE.md 라우트 순서 규칙).
@router.get(
    "/ranking",
    response_model=GymRankingResponse,
    summary="암장 랭킹 (공개 계정의 공개 컬러 등급 기록만)",
)
async def get_gym_ranking(
    service: GradeServiceDep,
    gym_name: str = Query(..., description="랭킹을 볼 암장(지점) 이름"),
    period: Literal["all", "month", "week"] = Query(
        "all", description="all(전체 누적) | month | week(ISO 주차)"
    ),
    year: int | None = Query(None, description="period=month|week 일 때 필수"),
    month: int | None = Query(None, ge=1, le=12, description="period=month 일 때 필수"),
    week: int | None = Query(
        None, ge=1, le=53, description="period=week 일 때 필수 (ISO 주차)"
    ),
) -> GymRankingResponse:
    return await service.compute_gym_ranking(
        gym_name, period=period, year=year, month=month, week=week
    )


@router.get(
    "/representatives",
    response_model=list[GymGradeSystemResponse],
    summary="암장 간 난이도 환산 탭 대상 (공식 + 브랜드 대표/단독만, 공개)",
)
async def list_gym_system_representatives(
    service: GradeServiceDep,
) -> list[GymGradeSystemResponse]:
    systems = await service.list_gym_system_representatives()
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
    summary="짐 색체계 수정 (본인 등록분, color_order + brand_name, gym_name 개명은 admin 전용)",
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
        is_admin=user.is_admin,
        brand_name=payload.brand_name,
        difficulty_offset=payload.difficulty_offset,
        gym_name=payload.gym_name,
        reason=payload.reason,
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
    reason: Annotated[
        str | None, Query(max_length=200, description="삭제 사유 (선택, 이력에 남음)")
    ] = None,
) -> None:
    await service.delete_gym_system(
        system_id=system_id, user_id=user.id, is_admin=user.is_admin, reason=reason
    )


@router.get(
    "/{system_id}/history",
    response_model=list[GymGradeSystemChangeResponse],
    summary="짐 색체계 변경 이력 (admin)",
    description=(
        "색 순서·보정치 등 변경 이력 최신순 50건 (ADR-069). "
        "보정치는 개인 종합점수를 직접 바꾸므로 원인 추적용. 삭제된 암장도 조회 가능."
    ),
)
async def get_gym_system_history(
    system_id: UUID,
    admin: AdminUserDep,
    service: GradeServiceDep,
) -> list[GymGradeSystemChangeResponse]:
    changes = await service.list_gym_system_changes(system_id)
    return [GymGradeSystemChangeResponse.model_validate(c) for c in changes]
