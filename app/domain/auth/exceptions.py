"""Auth 도메인 예외."""


class AuthDomainError(Exception):
    """Auth 공통 베이스."""

    pass


# ─────────────────────────────────────────
#  기존 (Day 11)
# ─────────────────────────────────────────


class UserNotFoundForAuth(AuthDomainError):
    pass


class RefreshTokenNotFound(AuthDomainError):
    pass


class RefreshTokenRevoked(AuthDomainError):
    pass


class InvalidCredentials(AuthDomainError):
    pass


# ─────────────────────────────────────────
#  Kakao OAuth (Day 12 ⭐ 추가)
# ─────────────────────────────────────────


class KakaoAPIError(AuthDomainError):
    """카카오 API 와의 통신 실패 (네트워크 등).

    재시도 가능한 에러. 사용자에겐 *"잠시 후 다시 시도"* 안내.
    """

    pass


class KakaoTokenExchangeFailed(AuthDomainError):
    """카카오 code → token 교환 실패.

    원인:
    - code 가 만료됨 (5분 안에 사용해야)
    - 이미 사용된 code (재사용 X)
    - redirect_uri 불일치
    - 잘못된 client_id/secret
    """

    def __init__(self, error: str, description: str = ""):
        self.error = error
        self.description = description
        super().__init__(f"kakao token exchange failed: {error} - {description}")


class KakaoUserInfoFailed(AuthDomainError):
    """카카오 사용자 정보 조회 실패.

    원인:
    - access_token 만료 / 잘못됨
    - 카카오 API 임시 장애
    """

    def __init__(self, error: str, code: int = 0):
        self.error = error
        self.code = code
        super().__init__(f"kakao user info failed: {error} (code={code})")


class KakaoEmailNotAvailable(AuthDomainError):
    """카카오 사용자가 이메일 동의 안 함.

    선택 동의이므로 발생 가능.
    회원가입에 email 필수면 이 케이스 처리 필요.
    """

    def __init__(self, kakao_id: str):
        self.kakao_id = kakao_id
        super().__init__(f"kakao email not agreed: kakao_id={kakao_id}")


class OAuthStateInvalid(AuthDomainError):
    """OAuth state 불일치 (CSRF 의심).

    원인:
    - 공격자가 위조한 callback URL
    - state 만료됨 (5분 지남)
    - 이미 사용된 state
    """

    pass
