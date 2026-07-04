"""Notifications 라우트.

- GET  /notifications              내 알림 목록 + 안읽은 개수 (인증)
- GET  /notifications/unread-count 안읽은 개수만 (뱃지 폴링, 인증)
- POST /notifications/read-all     전체 읽음 처리 (인증)
"""

from fastapi import APIRouter, Query, status

from app.api.dependencies import CurrentUserDep
from app.domain.notifications.dependencies import NotificationServiceDep
from app.domain.notifications.schemas import (
    NotificationListResponse,
    NotificationResponse,
    UnreadCountResponse,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get(
    "",
    response_model=NotificationListResponse,
    summary="내 알림 목록 + 안읽은 개수",
)
async def list_notifications(
    user: CurrentUserDep,
    service: NotificationServiceDep,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> NotificationListResponse:
    items = await service.list_notifications(
        user_id=user.id, limit=limit, offset=offset
    )
    unread = await service.count_unread(user_id=user.id)
    return NotificationListResponse(
        items=[NotificationResponse.model_validate(n) for n in items],
        unread_count=unread,
    )


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="안읽은 알림 개수 (뱃지용)",
)
async def unread_count(
    user: CurrentUserDep,
    service: NotificationServiceDep,
) -> UnreadCountResponse:
    unread = await service.count_unread(user_id=user.id)
    return UnreadCountResponse(unread_count=unread)


@router.post(
    "/read-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="전체 읽음 처리",
)
async def read_all(
    user: CurrentUserDep,
    service: NotificationServiceDep,
) -> None:
    await service.mark_all_read(user_id=user.id)
