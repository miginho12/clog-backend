"""Follows 라우트.

엔드포인트:
- POST   /users/{user_id}/follow      팔로우 (인증 필수)
- DELETE /users/{user_id}/follow      언팔로우 (인증 필수)
- GET    /users/{user_id}/followers   팔로워 목록 (인증 선택)
- GET    /users/{user_id}/following   팔로잉 목록 (인증 선택)

팔로워/팔로잉 카운트, 팔로우 여부는 프로필 응답에도 포함(별도 통합).
"""
from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.dependencies import CurrentUserDep, OptionalUserId
from app.domain.follows.dependencies import FollowServiceDep
from app.domain.follows.exceptions import FollowRequestNotFound  # noqa: F401 (핸들러 등록용)
from app.domain.follows.schemas import (
    FollowListResponse,
    FollowToggleResponse,
    FollowUserItem,
)

router = APIRouter(prefix="/users", tags=["follows"])


@router.post(
    "/{user_id}/follow",
    response_model=FollowToggleResponse,
    status_code=status.HTTP_200_OK,
    summary="팔로우",
)
async def follow_user(
    user_id: UUID,
    user: CurrentUserDep,
    service: FollowServiceDep,
) -> FollowToggleResponse:
    status_str = await service.follow(follower_id=user.id, following_id=user_id)
    count = await service.repo.count_followers(user_id=user_id)
    return FollowToggleResponse(
        following=status_str == "accepted",
        follow_status=status_str,
        follower_count=count,
    )


@router.delete(
    "/{user_id}/follow",
    response_model=FollowToggleResponse,
    status_code=status.HTTP_200_OK,
    summary="언팔로우",
)
async def unfollow_user(
    user_id: UUID,
    user: CurrentUserDep,
    service: FollowServiceDep,
) -> FollowToggleResponse:
    await service.unfollow(follower_id=user.id, following_id=user_id)
    count = await service.repo.count_followers(user_id=user_id)
    return FollowToggleResponse(
        following=False, follow_status="none", follower_count=count
    )


@router.get(
    "/me/follow-requests/count",
    summary="나에게 온 팔로우 요청 수",
)
async def count_my_follow_requests(
    user: CurrentUserDep,
    service: FollowServiceDep,
) -> dict:
    n = await service.repo.count_pending_requests(user_id=user.id)
    return {"count": n}


@router.delete(
    "/{follower_id}/follower",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="내 팔로워 삭제 (끊어내기)",
)
async def remove_my_follower(
    follower_id: UUID,
    user: CurrentUserDep,
    service: FollowServiceDep,
) -> Response:
    await service.remove_follower(owner_id=user.id, follower_id=follower_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me/follow-requests",
    response_model=FollowListResponse,
    summary="나에게 온 팔로우 요청 목록",
)
async def list_my_follow_requests(
    user: CurrentUserDep,
    service: FollowServiceDep,
) -> FollowListResponse:
    users = await service.repo.list_pending_requests(user_id=user.id)
    items = [
        FollowUserItem(
            id=str(u.id),
            nickname=u.nickname,
            profile_image_url=u.profile_image_url,
            is_following=False,
        )
        for u in users
    ]
    return FollowListResponse(users=items, total=len(items))


@router.post(
    "/{requester_id}/follow-request/accept",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="팔로우 요청 수락",
)
async def accept_follow_request(
    requester_id: UUID,
    user: CurrentUserDep,
    service: FollowServiceDep,
) -> Response:
    await service.accept_request(owner_id=user.id, requester_id=requester_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{requester_id}/follow-request/reject",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="팔로우 요청 거절",
)
async def reject_follow_request(
    requester_id: UUID,
    user: CurrentUserDep,
    service: FollowServiceDep,
) -> Response:
    await service.reject_request(owner_id=user.id, requester_id=requester_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{user_id}/followers",
    response_model=FollowListResponse,
    summary="팔로워 목록",
)
async def list_followers(
    user_id: UUID,
    viewer_id: OptionalUserId,
    service: FollowServiceDep,
) -> FollowListResponse:
    users = await service.repo.list_followers(user_id=user_id)
    return await _build_list(service, viewer_id, users)


@router.get(
    "/{user_id}/following",
    response_model=FollowListResponse,
    summary="팔로잉 목록",
)
async def list_following(
    user_id: UUID,
    viewer_id: OptionalUserId,
    service: FollowServiceDep,
) -> FollowListResponse:
    users = await service.repo.list_following(user_id=user_id)
    return await _build_list(service, viewer_id, users)


async def _build_list(service, viewer_id, users) -> FollowListResponse:
    """사용자 목록 → 응답. viewer 의 팔로우 여부를 한 번에 채움."""
    viewer_following: set = set()
    if viewer_id is not None and users:
        viewer_following = await service.repo.following_ids(
            follower_id=viewer_id,
            user_ids=[u.id for u in users],
        )
    items = [
        FollowUserItem(
            id=str(u.id),
            nickname=u.nickname,
            profile_image_url=u.profile_image_url,
            is_following=u.id in viewer_following,
        )
        for u in users
    ]
    return FollowListResponse(users=items, total=len(items))
