"""Notification 스키마 (조회 응답)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NotificationActor(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    nickname: str
    profile_image_url: str | None = None


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    type: str
    climbing_log_id: UUID
    comment_id: UUID | None = None
    is_read: bool
    created_at: datetime
    actor: NotificationActor | None = None


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    unread_count: int


class UnreadCountResponse(BaseModel):
    unread_count: int
