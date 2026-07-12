"""Follows 스키마."""
from pydantic import BaseModel, ConfigDict


class FollowToggleResponse(BaseModel):
    """팔로우/언팔로우 결과."""

    following: bool  # 현재 팔로우(accepted) 상태
    follow_status: str  # "none" | "pending" | "accepted"
    follower_count: int  # 대상의 팔로워 수 (갱신 후)


class FollowUserItem(BaseModel):
    """팔로워/팔로잉 목록의 사용자 항목."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    nickname: str | None = None
    profile_image_url: str | None = None
    is_following: bool = False  # viewer 가 이 사용자를 팔로우 중인지


class FollowListResponse(BaseModel):
    """팔로워/팔로잉 목록."""

    users: list[FollowUserItem]
    total: int
