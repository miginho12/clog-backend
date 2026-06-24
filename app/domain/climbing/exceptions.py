"""Climbing 도메인 예외."""


class ClimbingDomainError(Exception):
    """Climbing 도메인 기본 예외."""

    pass


class ClimbingLogNotFound(ClimbingDomainError):
    """기록을 찾을 수 없음 (또는 비공개라 접근 불가)."""

    def __init__(self, log_id: str):
        self.log_id = log_id
        super().__init__(f"climbing log not found: {log_id}")


class ClimbingLogForbidden(ClimbingDomainError):
    """본인 소유가 아닌 기록을 수정/삭제하려 함."""

    def __init__(self, log_id: str):
        self.log_id = log_id
        super().__init__(f"not the owner of climbing log: {log_id}")
