"""User 엔드포인트 (Day 14 보안 강화)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.dependencies import AdminUserDep, CurrentUserDep, OptionalUserId
from app.core.rate_limit import RateLimits, limiter
from app.domain.auth.dependencies import get_refresh_token_repository
from app.domain.auth.repository import RedisRefreshTokenRepository
from app.domain.follows.dependencies import FollowServiceDep
from app.domain.users.dependencies import UserServiceDep
from app.domain.users.exceptions import UserUpdateForbidden
from app.domain.users.schemas import (
    AdminBanResponse,
    PasswordChangeRequest,
    UserPublicResponse,
    UserResponse,
    UserSearchItem,
    UserSearchResponse,
    UserUpdate,
)

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


@router.patch(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="비밀번호 변경 (local 계정)",
)
@limiter.limit(RateLimits.USERS_UPDATE)
async def change_my_password(
    request: Request,
    payload: PasswordChangeRequest,
    user: CurrentUserDep,
    service: UserServiceDep,
    refresh_repo: Annotated[
        RedisRefreshTokenRepository, Depends(get_refresh_token_repository)
    ],
) -> Response:
    await service.change_password(
        user_id=user.id,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    # 보안: 비밀번호 변경 시 전체 refresh 토큰 무효화 (다른 기기 강제 로그아웃)
    await refresh_repo.revoke_all_for_user(str(user.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="회원 탈퇴 (soft delete)",
)
@limiter.limit(RateLimits.USERS_UPDATE)
async def delete_me(
    request: Request,
    user: CurrentUserDep,
    service: UserServiceDep,
    refresh_repo: Annotated[
        RedisRefreshTokenRepository, Depends(get_refresh_token_repository)
    ],
) -> Response:
    await service.deactivate_account(user_id=user.id)
    # 탈퇴 즉시 전 기기 로그아웃 (refresh 무효화)
    await refresh_repo.revoke_all_for_user(str(user.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── /users/search (반드시 /{user_id} 보다 위) ──

@router.get(
    "/search",
    response_model=UserSearchResponse,
    summary="닉네임으로 사용자 검색 (검색어 없으면 전체 브라우즈)",
)
@limiter.limit(RateLimits.USERS_SEARCH)
async def search_users(
    request: Request,
    service: UserServiceDep,
    viewer_id: OptionalUserId,
    q: str = Query("", max_length=50, description="닉네임 검색어 (없으면 전체 목록)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> UserSearchResponse:
    users, has_next = await service.search_users(
        query=q.strip(),
        viewer_id=viewer_id,
        page=page,
        page_size=page_size,
    )
    return UserSearchResponse(
        items=[UserSearchItem.model_validate(u) for u in users],
        page=page,
        page_size=page_size,
        has_next=has_next,
    )


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
    follow_service: FollowServiceDep,
) -> UserPublicResponse:
    target = await service.get_user_for_viewer(
        target_user_id=user_id,
        viewer_user_id=current_user.id,
    )
    resp = UserPublicResponse.model_validate(target)
    if target.id != current_user.id:
        resp.follow_status = await follow_service.get_follow_status(
            follower_id=current_user.id, following_id=target.id
        ) or "none"
    return resp


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


# ── admin: 사용자 차단 (Step 3) ──

@router.post(
    "/{user_id}/ban",
    response_model=AdminBanResponse,
    summary="사용자 차단 (admin)",
)
async def ban_user(
    user_id: UUID,
    admin: AdminUserDep,
    service: UserServiceDep,
) -> AdminBanResponse:
    user = await service.set_ban(
        user_id=user_id, banned=True, actor_id=admin.id
    )
    return AdminBanResponse.model_validate(user)


@router.delete(
    "/{user_id}/ban",
    response_model=AdminBanResponse,
    summary="사용자 차단 해제 (admin)",
)
async def unban_user(
    user_id: UUID,
    admin: AdminUserDep,
    service: UserServiceDep,
) -> AdminBanResponse:
    user = await service.set_ban(
        user_id=user_id, banned=False, actor_id=admin.id
    )
    return AdminBanResponse.model_validate(user)
