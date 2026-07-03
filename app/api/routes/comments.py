"""Comments 라우트.

- GET    /climbing-logs/{log_id}/comments   목록 (비로그인 허용, 공개글)
- POST   /climbing-logs/{log_id}/comments   작성 (인증)
- PATCH  /comments/{comment_id}             수정 (본인)
- DELETE /comments/{comment_id}             삭제 (본인, soft)
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.dependencies import CurrentUserDep
from app.api.routes.climbing import OptionalUserId
from app.domain.comments.dependencies import CommentServiceDep
from app.domain.comments.schemas import (
    CommentCreate,
    CommentListResponse,
    CommentPinRequest,
    CommentResponse,
    CommentThread,
    CommentUpdate,
)

router = APIRouter(tags=["comments"])


@router.get(
    "/climbing-logs/{log_id}/comments",
    response_model=CommentListResponse,
    summary="댓글 목록 (대댓글 중첩)",
)
async def list_comments(
    log_id: UUID,
    service: CommentServiceDep,
    viewer_id: OptionalUserId,
) -> CommentListResponse:
    tops, replies, total, _ = await service.list_comments(
        log_id=log_id, viewer_id=viewer_id
    )
    # 대댓글을 parent_id 로 묶어 스레드 구성
    replies_by_parent: dict[UUID, list[CommentResponse]] = {}
    for r in replies:
        replies_by_parent.setdefault(r.parent_id, []).append(
            CommentResponse.model_validate(r)
        )
    items = [
        CommentThread(
            comment=CommentResponse.model_validate(t),
            replies=replies_by_parent.get(t.id, []),
        )
        for t in tops
    ]
    return CommentListResponse(items=items, total=total)


@router.post(
    "/climbing-logs/{log_id}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="댓글 작성",
)
async def create_comment(
    log_id: UUID,
    payload: CommentCreate,
    user: CurrentUserDep,
    service: CommentServiceDep,
) -> CommentResponse:
    comment = await service.create_comment(
        user_id=user.id,
        log_id=log_id,
        content=payload.content,
        parent_id=payload.parent_id,
    )
    return CommentResponse.model_validate(comment)


@router.patch(
    "/comments/{comment_id}",
    response_model=CommentResponse,
    summary="댓글 수정 (본인)",
)
async def update_comment(
    comment_id: UUID,
    payload: CommentUpdate,
    user: CurrentUserDep,
    service: CommentServiceDep,
) -> CommentResponse:
    comment = await service.update_comment(
        comment_id=comment_id, user_id=user.id, content=payload.content
    )
    return CommentResponse.model_validate(comment)


@router.patch(
    "/comments/{comment_id}/pin",
    response_model=CommentResponse,
    summary="댓글 고정/해제 (게시물 작성자)",
)
async def set_comment_pin(
    comment_id: UUID,
    payload: CommentPinRequest,
    user: CurrentUserDep,
    service: CommentServiceDep,
) -> CommentResponse:
    comment = await service.set_pin(
        comment_id=comment_id, user_id=user.id, pinned=payload.pinned
    )
    return CommentResponse.model_validate(comment)


@router.delete(
    "/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="댓글 삭제 (본인, soft)",
)
async def delete_comment(
    comment_id: UUID,
    user: CurrentUserDep,
    service: CommentServiceDep,
) -> None:
    await service.delete_comment(comment_id=comment_id, user_id=user.id)
