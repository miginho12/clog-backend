"""Auth 도메인 예외."""


class AuthDomainError(Exception):
    """Auth 공통 베이스."""

    pass


class UserNotFoundForAuth(AuthDomainError):
    """로그인 시도한 user 가 존재 X.

    POST /auth/login 의 user_id 가 잘못된 경우.
    Day 12 OAuth 통합 후엔 거의 발생 X.
    """

    pass


class RefreshTokenNotFound(AuthDomainError):
    """Repository 에 없는 refresh token.

    - 이미 사용됨
    - 로그아웃됨
    - 변조됨
    """

    pass


class RefreshTokenRevoked(AuthDomainError):
    """무효화된 refresh token."""

    pass


class InvalidCredentials(AuthDomainError):
    """잘못된 인증 정보 (일반)."""

    pass
