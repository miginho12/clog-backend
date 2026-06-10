"""User 엔드포인트.

REST 표준:
- POST   /users           회원 생성 (201)
- GET    /users           목록 조회 (200, 페이지네이션)
- GET    /users/{user_id} 단일 조회 (200)
- PATCH  /users/{user_id} 부분 수정 (200)
- DELETE /users/{user_id} 삭제 (204, soft delete)

라우터는 *얇게* — 입력 검증/응답 포맷만.
실제 로직은 Service.
도메인 예외는 exception_handlers 가 자동 처리.
"""

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.domain.users.dependencies import UserServiceDep
from app.domain.users.schemas import (
    UserCreate,
    UserListResponse,
    UserResponse,
    UserUpdate,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="회원 생성",
    description=(
        "새 회원을 생성합니다. "
        "현재는 카카오 OAuth 시뮬레이션 단계로 auth_provider_id 를 직접 받습니다. "
        "Day 11+ 에 진짜 OAuth 통합 예정."
    ),
    responses={
        201: {"description": "생성 성공"},
        409: {"description": "이메일/닉네임/OAuth 중복"},
        422: {"description": "입력 형식 오류"},
    },
)
async def create_user(payload: UserCreate, service: UserServiceDep) -> UserResponse:
    user = await service.create_user(payload)
    return UserResponse.model_validate(user)


@router.get(
    "",
    response_model=UserListResponse,
    summary="회원 목록 조회",
)
async def list_users(
    service: UserServiceDep,
    page: int = Query(default=1, ge=1, description="페이지 번호 (1부터)"),
    page_size: int = Query(default=20, ge=1, le=100, description="페이지 크기"),
) -> UserListResponse:
    users, total = await service.list_users(page=page, page_size=page_size)
    return UserListResponse(
        items=[UserResponse.model_validate(u) for u in users],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="회원 단일 조회",
    responses={404: {"description": "회원 없음"}},
)
async def get_user(user_id: UUID, service: UserServiceDep) -> UserResponse:
    user = await service.get_user(user_id)
    return UserResponse.model_validate(user)


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="회원 정보 부분 수정",
    responses={
        404: {"description": "회원 없음"},
        409: {"description": "닉네임 중복"},
    },
)
async def update_user(
    user_id: UUID, payload: UserUpdate, service: UserServiceDep
) -> UserResponse:
    user = await service.update_user(user_id, payload)
    return UserResponse.model_validate(user)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="회원 삭제 (soft delete)",
    responses={
        404: {"description": "회원 없음"},
        410: {"description": "이미 삭제됨"},
    },
)
async def delete_user(user_id: UUID, service: UserServiceDep) -> None:
    await service.delete_user(user_id)
    # 204 = No Content (응답 본문 없음)
