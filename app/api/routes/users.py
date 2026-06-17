"""User 엔드포인트 (Day 14 보안 강화)."""

from uuid import UUID

from fastapi import APIRouter, Request

from app.api.dependencies import CurrentUserDep
from app.core.rate_limit import RateLimits, limiter
from app.domain.users.dependencies import UserServiceDep
from app.domain.users.exceptions import UserUpdateForbidden
from app.domain.users.schemas import UserPublicResponse, UserResponse, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


# ── /users/me ──

@router.get("/me", response_model=UserResponse, summary="내 정보 조회")
@limiter.limit(RateLimits.USERS_ME)
async def get_me(request: Request, user: CurrentUserDep) -> UserResponse:
    return UserResponse.model_validate(user)


@router.patch("/me", response_model=UserResponse, summary="내 정보 수정")
@limiter.limit(RateLimits.USERS_UPDATE)
async def update_me(
    request: Request,
    payload: UserUpdate,
    user: CurrentUserDep,
    service: UserServiceDep,
) -> UserResponse:
    updated = await service.update_user(user_id=user.id, payload=payload)
    return UserResponse.model_validate(updated)


# ── /users/{user_id} ──

@router.get(
    "/{user_id}",
    response_model=UserPublicResponse,
    summary="사용자 공개 정보 조회",
)
@limiter.limit(RateLimits.USERS_DETAIL)
async def get_user(
    request: Request,
    user_id: UUID,
    current_user: CurrentUserDep,
    service: UserServiceDep,
) -> UserPublicResponse:
    target = await service.get_user_for_viewer(
        target_user_id=user_id,
        viewer_user_id=current_user.id,
    )
    return UserPublicResponse.model_validate(target)


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="사용자 정보 수정 (본인만)",
)
@limiter.limit(RateLimits.USERS_UPDATE)
async def update_user(
    request: Request,
    user_id: UUID,
    payload: UserUpdate,
    current_user: CurrentUserDep,
    service: UserServiceDep,
) -> UserResponse:
    # 권한 체크 - 본인만
    if user_id != current_user.id:
        raise UserUpdateForbidden(
            user_id=str(user_id),
            current_user_id=str(current_user.id),
        )
    updated = await service.update_user(user_id=user_id, payload=payload)
    return UserResponse.model_validate(updated)