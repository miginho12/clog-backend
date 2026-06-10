"""User 도메인 예외.

비즈니스 로직 에러를 HTTP 와 분리.
Service 레이어는 HTTP 를 모르고 도메인 예외만 발생.
API 레이어 (또는 exception_handlers) 에서 HTTP 로 변환.

Spring 의 @ControllerAdvice 와 비슷한 패턴.
"""


class UserDomainError(Exception):
    """User 도메인 공통 베이스."""

    pass


class EmailAlreadyExists(UserDomainError):
    """이메일 중복."""

    def __init__(self, email: str):
        self.email = email
        super().__init__(f"email already exists: {email}")


class NicknameAlreadyExists(UserDomainError):
    """닉네임 중복."""

    def __init__(self, nickname: str):
        self.nickname = nickname
        super().__init__(f"nickname already exists: {nickname}")


class OAuthIdentityAlreadyExists(UserDomainError):
    """같은 OAuth 계정으로 이미 가입.

    auth_provider + auth_provider_id 의 조합이 중복.
    """

    def __init__(self, provider: str, provider_id: str):
        self.provider = provider
        self.provider_id = provider_id
        super().__init__(f"oauth identity already registered: {provider}:{provider_id}")


class UserNotFound(UserDomainError):
    """사용자 못 찾음."""

    def __init__(self, user_id: str | None = None, identifier: str | None = None):
        self.user_id = user_id
        self.identifier = identifier
        msg = f"user not found"
        if user_id:
            msg += f": id={user_id}"
        elif identifier:
            msg += f": {identifier}"
        super().__init__(msg)


class UserAlreadyDeleted(UserDomainError):
    """이미 soft-delete 된 사용자."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        super().__init__(f"user already deleted: {user_id}")
