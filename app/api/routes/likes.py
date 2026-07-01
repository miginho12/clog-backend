"""Likes 라우트.

엔드포인트:
- POST   /climbing-logs/{log_id}/like   좋아요 (인증 필수)
- DELETE /climbing-logs/{log_id}/like   좋아요 취소 (인증 필수)

게시물의 하위 리소스로 배치 (climbing-logs prefix 공유).
좋아요 수/liked_by_me 는 피드/상세 응답(ClimbingLogResponse)에 집계로 포함.
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.dependencies import CurrentUserDep
from app.domain.likes.dependencies import LikeServiceDep
from app.domain.likes.schemas import LikeToggleResponse

router = APIRouter(prefix="/climbing-logs", tags=["likes"])


@router.post(
    "/{log_id}/like",
    response_model=LikeToggleResponse,
    status_code=status.HTTP_200_OK,
    summary="좋아요",
)
async def like_log(
    log_id: UUID,
    user: CurrentUserDep,
    service: LikeServiceDep,
) -> LikeToggleResponse:
    count = await service.like(user_id=user.id, log_id=log_id)
    return LikeToggleResponse(liked=True, like_count=count)


@router.delete(
    "/{log_id}/like",
    response_model=LikeToggleResponse,
    status_code=status.HTTP_200_OK,
    summary="좋아요 취소",
)
async def unlike_log(
    log_id: UUID,
    user: CurrentUserDep,
    service: LikeServiceDep,
) -> LikeToggleResponse:
    count = await service.unlike(user_id=user.id, log_id=log_id)
    return LikeToggleResponse(liked=False, like_count=count)
