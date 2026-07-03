"""Comments 도메인 스키마."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CommentAuthor(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    nickname: str
    profile_image_url: str | None = None


class CommentCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    parent_id: UUID | None = None


class CommentUpdate(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


class CommentPinRequest(BaseModel):
    pinned: bool


class CommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    content: str
    created_at: datetime
    is_pinned: bool
    parent_id: UUID | None
    author: CommentAuthor | None = None
    reply_count: int = 0
    is_mine: bool = False
    can_pin: bool = False
    like_count: int = 0
    liked_by_me: bool = False

    @model_validator(mode="before")
    @classmethod
    def _map_user_to_author(cls, data):
        if isinstance(data, dict):
            return data
        user = getattr(data, "user", None)
        if user is not None and getattr(data, "author", None) is None:
            try:
                data.author = CommentAuthor.model_validate(user)
            except Exception:
                pass
        return data


class CommentListResponse(BaseModel):
    """댓글 목록 (최상위 + 대댓글 중첩)."""

    items: list["CommentThread"]
    total: int


class CommentThread(BaseModel):
    """최상위 댓글 + 대댓글 리스트."""

    comment: CommentResponse
    replies: list[CommentResponse]
