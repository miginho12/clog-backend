"""Likes 도메인 예외."""


class LikeDomainError(Exception):
    """Likes 도메인 기본 예외."""

    pass


class LikeTargetNotFound(LikeDomainError):
    """좋아요 대상 게시물이 없음 (또는 비공개라 접근 불가)."""

    def __init__(self, log_id: str):
        self.log_id = log_id
        super().__init__(f"like target not found: {log_id}")
