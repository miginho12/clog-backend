"""CommentLikes 도메인 스키마."""

from pydantic import BaseModel


class CommentLikeToggleResponse(BaseModel):
    liked: bool
    like_count: int
