"""Likes 도메인 스키마."""

from pydantic import BaseModel


class LikeToggleResponse(BaseModel):
    """좋아요 토글 결과."""

    liked: bool  # 현재 상태 (좋아요됨 여부)
    like_count: int  # 갱신된 총 좋아요 수
