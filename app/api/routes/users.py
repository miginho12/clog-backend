"""User 엔드포인트."""

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.dependencies import CurrentUserDep
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
)
async def create_user(payload: UserCreate, service: UserServiceDep) -> UserResponse:
    user = await service.create_user(payload)
    return UserResponse.model_validate(user)


# ⭐ /users/me — 인증된 사용자만 (Day 11A 의 핵심 검증)
# 주의: /me 는 /{user_id} 보다 먼저 정의 (UUID 파싱 충돌 방지)
@router.get(
    "/me",
    response_model=UserResponse,
    summary="내 정보 조회",
    description="현재 인증된 사용자의 정보. Bearer 토큰 필요.",
    responses={
        200: {"description": "조회 성공"},
        401: {"description": "인증 필요"},
    },
)
async def get_my_profile(user: CurrentUserDep) -> UserResponse:
    """⭐ 보호된 엔드포인트.

    Bearer 토큰 없으면 401. JWT 검증 통과한 user 객체가 자동 주입.
    """
    return UserResponse.model_validate(user)


@router.patch(
    "/me",
    response_model=UserResponse,
    summary="내 정보 수정",
    description="현재 인증된 사용자의 정보 수정.",
)
async def update_my_profile(
    payload: UserUpdate, user: CurrentUserDep, service: UserServiceDep
) -> UserResponse:
    """본인 정보 수정 (다른 사람 정보 수정 X)."""
    updated = await service.update_user(user.id, payload)
    return UserResponse.model_validate(updated)


@router.get(
    "",
    response_model=UserListResponse,
    summary="회원 목록 조회",
)
async def list_users(
    service: UserServiceDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
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
)
async def get_user(user_id: UUID, service: UserServiceDep) -> UserResponse:
    user = await service.get_user(user_id)
    return UserResponse.model_validate(user)


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="회원 정보 부분 수정",
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
)
async def delete_user(user_id: UUID, service: UserServiceDep) -> None:
    await service.delete_user(user_id)
