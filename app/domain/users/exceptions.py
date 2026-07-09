"""User 도메인 예외."""


class UserDomainError(Exception):
    """User 도메인 공통 베이스."""

    pass


# ─────────────────────────────────────────
#  기존 (Day 10)
# ─────────────────────────────────────────


class UserNotFound(UserDomainError):
    def __init__(self, user_id: str):
        self.user_id = user_id
        super().__init__(f"user not found: {user_id}")


class UserAlreadyDeleted(UserDomainError):
    def __init__(self, user_id: str):
        self.user_id = user_id
        super().__init__(f"user already deleted: {user_id}")


class EmailAlreadyExists(UserDomainError):
    def __init__(self, email: str):
        self.email = email
        super().__init__(f"email already exists: {email}")


class NicknameAlreadyExists(UserDomainError):
    def __init__(self, nickname: str):
        self.nickname = nickname
        super().__init__(f"nickname already exists: {nickname}")


class OAuthIdentityAlreadyExists(UserDomainError):
    def __init__(self, provider: str, provider_id: str):
        self.provider = provider
        self.provider_id = provider_id
        super().__init__(f"oauth identity already exists: {provider}/{provider_id}")


# ─────────────────────────────────────────
#  Day 14 ⭐ 추가: 권한
# ─────────────────────────────────────────


class UserProfilePrivate(UserDomainError):
    """비공개 프로필 조회 시도.

    GET /users/{id} 에서 대상 사용자의 is_public=False 일 때.
    본인 자신이 아니면 발생.
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        super().__init__(f"user profile is private: {user_id}")


class UserUpdateForbidden(UserDomainError):
    """다른 사용자 수정 시도.

    PATCH /users/{id} 에서 본인 아닌 사용자 수정 시도.
    """

    def __init__(self, user_id: str, current_user_id: str):
        self.user_id = user_id
        self.current_user_id = current_user_id
        super().__init__(
            f"cannot update user {user_id} as {current_user_id}"
        )


# ─────────────────────────────────────────
#  admin Step 3 ⭐ 추가: 차단
# ─────────────────────────────────────────


class CannotBanSelf(UserDomainError):
    """admin 이 자기 자신을 차단 시도 (락아웃 방지)."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        super().__init__(f"cannot ban yourself: {user_id}")
