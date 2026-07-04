"""카카오 OAuth 응답 Pydantic 모델.

외부 API 의 dict 를 타입 안전한 객체로 변환.

[목적]
- IDE 자동완성 + 타입 체크
- 카카오 응답 형식 변경 시 명확한 에러
- 내부 코드에선 dict 가 아닌 객체로 다룸
"""

from pydantic import BaseModel, ConfigDict, Field

# ─────────────────────────────────────────
#  카카오 토큰 응답
# ─────────────────────────────────────────


class KakaoTokenResponse(BaseModel):
    """POST /oauth/token 응답."""

    model_config = ConfigDict(extra="ignore")  # 미사용 필드 무시

    access_token: str
    token_type: str = "bearer"
    refresh_token: str | None = None
    expires_in: int  # access_token 만료 (초)
    scope: str | None = None
    refresh_token_expires_in: int | None = None


# ─────────────────────────────────────────
#  카카오 사용자 정보 (GET /v2/user/me)
# ─────────────────────────────────────────


class KakaoProfile(BaseModel):
    """카카오 프로필 정보."""

    model_config = ConfigDict(extra="ignore")

    nickname: str | None = None
    profile_image_url: str | None = None
    thumbnail_image_url: str | None = None
    is_default_image: bool | None = None


class KakaoAccount(BaseModel):
    """kakao_account 의 내용."""

    model_config = ConfigDict(extra="ignore")

    # 프로필
    profile: KakaoProfile | None = None
    profile_nickname_needs_agreement: bool = False
    profile_image_needs_agreement: bool = False

    # 이메일 (사용자 동의 시에만 받음)
    has_email: bool = False
    email_needs_agreement: bool = False
    is_email_valid: bool = False
    is_email_verified: bool = False
    email: str | None = None


class KakaoUserInfo(BaseModel):
    """GET /v2/user/me 응답.

    핵심 필드만 받음. 그 외 (생일, 성별 등) 는 ignore.
    """

    model_config = ConfigDict(extra="ignore")

    id: int = Field(..., description="카카오 사용자 ID (영구)")
    connected_at: str | None = None
    kakao_account: KakaoAccount | None = None

    # 편의 메서드 ─────

    def get_nickname(self) -> str | None:
        """닉네임 추출 (kakao_account.profile.nickname)."""
        if self.kakao_account and self.kakao_account.profile:
            return self.kakao_account.profile.nickname
        return None

    def get_profile_image(self) -> str | None:
        """프로필 이미지 URL 추출."""
        if self.kakao_account and self.kakao_account.profile:
            return self.kakao_account.profile.profile_image_url
        return None

    def get_email(self) -> str | None:
        """이메일 추출 (동의했고 유효한 경우만)."""
        if (
            self.kakao_account
            and self.kakao_account.email
            and self.kakao_account.is_email_valid
            and self.kakao_account.is_email_verified
        ):
            return self.kakao_account.email
        return None

    @property
    def auth_provider_id(self) -> str:
        """카카오 ID 를 문자열로."""
        return str(self.id)


# ─────────────────────────────────────────
#  우리 API 응답
# ─────────────────────────────────────────


class KakaoLoginInitResponse(BaseModel):
    """GET /auth/kakao/login 응답 (개발/디버깅용).

    실제 운영에선 302 Redirect 응답. JSON 응답은 API 클라이언트에서 직접 호출 시.
    """

    authorize_url: str
    state: str  # CSRF 방어용 (디버깅 확인용)
