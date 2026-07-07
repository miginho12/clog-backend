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


# ─────────────────────────────────────────
#  자체 회원가입 (Day 17 ⭐ 추가)
# ─────────────────────────────────────────


class EmailNotVerified(Exception):
    """이메일 미인증 상태로 로그인 시도.

    비밀번호는 맞았으나 이메일 인증 전 → 로그인 차단.
    (계정 열거 방어 대상 아님: 이미 본인 확인됨)
    """

    def __init__(self, email: str):
        self.email = email
        super().__init__(f"email not verified: {email}")


class EmailAlreadyRegistered(AuthDomainError):
    """이미 가입된 이메일로 회원가입 시도.

    OAuth 가입자와 동일 이메일인 경우도 포함.
    보안상 "이미 가입된 이메일" 정도만 노출 (어떤 provider 인지는 숨김).
    """

    def __init__(self, email: str):
        self.email = email
        super().__init__(f"email already registered: {email}")


class NicknameAlreadyTaken(AuthDomainError):
    """이미 사용 중인 닉네임으로 회원가입 시도."""

    def __init__(self, nickname: str):
        self.nickname = nickname
        super().__init__(f"nickname already taken: {nickname}")


class LocalLoginNotAvailable(AuthDomainError):
    """local 로그인 불가.

    원인:
    - 해당 이메일이 OAuth(kakao 등)로만 가입됨 → password_hash 가 NULL
    - 이메일/비밀번호 불일치

    보안상 둘을 구분하지 않고 동일 메시지로 응답 (계정 존재 여부 숨김).
    """

    pass
