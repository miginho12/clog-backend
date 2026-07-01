"""CommentLikes 라우트.

- POST   /comments/{comment_id}/like   댓글 좋아요 (인증)
- DELETE /comments/{comment_id}/like   취소 (인증)
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.dependencies import CurrentUserDep
from app.domain.comment_likes.dependencies import CommentLikeServiceDep
from app.domain.comment_likes.schemas import CommentLikeToggleResponse

router = APIRouter(prefix="/comments", tags=["comment_likes"])


@router.post(
    "/{comment_id}/like",
    response_model=CommentLikeToggleResponse,
    status_code=status.HTTP_200_OK,
    summary="댓글 좋아요",
)
async def like_comment(
    comment_id: UUID,
    user: CurrentUserDep,
    service: CommentLikeServiceDep,
) -> CommentLikeToggleResponse:
    count = await service.like(user_id=user.id, comment_id=comment_id)
    return CommentLikeToggleResponse(liked=True, like_count=count)


@router.delete(
    "/{comment_id}/like",
    response_model=CommentLikeToggleResponse,
    status_code=status.HTTP_200_OK,
    summary="댓글 좋아요 취소",
)
async def unlike_comment(
    comment_id: UUID,
    user: CurrentUserDep,
    service: CommentLikeServiceDep,
) -> CommentLikeToggleResponse:
    count = await service.unlike(user_id=user.id, comment_id=comment_id)
    return CommentLikeToggleResponse(liked=False, like_count=count)
