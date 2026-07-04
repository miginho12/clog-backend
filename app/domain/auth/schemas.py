"""Auth 도메인 Pydantic 스키마."""

from uuid import UUID

from pydantic import BaseModel, Field

from app.core.security import TokenPair

# ─────────────────────────────────────────
#  Request
# ─────────────────────────────────────────


class LoginRequest(BaseModel):
    """로그인 요청 (시뮬레이션 - Day 12 OAuth 전 단계).

    Day 12 부터는 이거 안 쓰고 카카오 OAuth 콜백 사용.
    """

    user_id: UUID = Field(
        ...,
        description="로그인할 사용자의 ID (실제 운영에선 OAuth 가 결정)",
    )


class RefreshRequest(BaseModel):
    """Refresh token 으로 access 재발급 요청."""

    refresh_token: str = Field(
        ..., min_length=10, description="이전에 발급받은 refresh token"
    )


class LogoutRequest(BaseModel):
    """로그아웃 요청 (refresh token 무효화)."""

    refresh_token: str = Field(..., min_length=10)


# ─────────────────────────────────────────
#  Response
# ─────────────────────────────────────────


class TokenResponse(BaseModel):
    """토큰 발급 응답.

    OAuth 2.0 표준 형식 따름.
    """

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int  # 초 단위

    @classmethod
    def from_pair(cls, pair: TokenPair) -> "TokenResponse":
        return cls(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            token_type=pair.token_type,
            expires_in=pair.expires_in,
        )


class AccessTokenResponse(BaseModel):
    """Access token 만 갱신할 때 응답.

    refresh_token 는 재발급 안 함 (보안 - rotation 은 Day 11B 에 고려).
    """

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
