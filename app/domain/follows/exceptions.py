"""Follows 도메인 예외."""


class FollowError(Exception):
    """팔로우 관련 기본 예외."""


class CannotFollowSelf(FollowError):
    """자기 자신은 팔로우할 수 없음."""

    def __init__(self) -> None:
        super().__init__("자기 자신은 팔로우할 수 없습니다")


class FollowTargetNotFound(FollowError):
    """팔로우 대상 사용자가 존재하지 않음."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        super().__init__(f"사용자를 찾을 수 없습니다: {user_id}")
