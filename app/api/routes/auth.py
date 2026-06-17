"""Auth 엔드포인트 (Day 14 보안 강화).

[Day 14 변경]
- POST /auth/login: local 환경에서만 활성화 (시뮬레이션, dev/prod 비활성)
- 모든 엔드포인트에 rate limit 적용
- /auth/kakao/login: 10/min (CSRF + 부하)
- /auth/refresh: 30/min
- /auth/logout: 60/min
- /auth/logout-all: 10/min
"""

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.api.dependencies import CurrentUserDep
from app.core.config import get_settings
from app.core.rate_limit import RateLimits, limiter
from app.domain.auth.dependencies import AuthServiceDep, KakaoOAuthServiceDep
from app.domain.auth.schemas import (
    AccessTokenResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
)
from app.domain.users.schemas import UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


# ─────────────────────────────────────────
#  /auth/login (Day 14 ⭐ 환경별 비활성화)
# ─────────────────────────────────────────


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="로그인 (local 환경 전용, 시뮬레이션)",
    description=(
        "⚠️ 시뮬레이션 엔드포인트 — local 환경에서만 활성. "
        "운영(dev/prod)에서는 404 반환. "
        "운영에선 카카오 OAuth (/auth/kakao/login) 사용."
    ),
)
@limiter.limit(RateLimits.REFRESH)  # 시뮬레이션이라 적당히
async def login(
    request: Request, payload: LoginRequest, service: AuthServiceDep
) -> TokenResponse:
    """시뮬레이션 로그인 (local 환경 전용).

    Raises:
        404: dev/prod 환경에서 호출 시
    """
    settings = get_settings()
    if not settings.is_simulation_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Endpoint not available in this environment",
        )

    pair = await service.login(payload.user_id)
    return TokenResponse.from_pair(pair)


# ─────────────────────────────────────────
#  Refresh & Logout (rate limit 추가)
# ─────────────────────────────────────────


@router.post(
    "/refresh",
    response_model=AccessTokenResponse,
    summary="Access token 갱신",
)
@limiter.limit(RateLimits.REFRESH)
async def refresh_token(
    request: Request, payload: RefreshRequest, service: AuthServiceDep
) -> AccessTokenResponse:
    settings = get_settings()
    new_access = await service.refresh_access_token(payload.refresh_token)
    return AccessTokenResponse(
        access_token=new_access,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="로그아웃",
)
@limiter.limit(RateLimits.LOGOUT)
async def logout(
    request: Request, payload: LogoutRequest, service: AuthServiceDep
) -> None:
    await service.logout(payload.refresh_token)


@router.post(
    "/logout-all",
    status_code=status.HTTP_200_OK,
    summary="모든 디바이스 로그아웃",
)
@limiter.limit(RateLimits.LOGOUT_ALL)
async def logout_all(
    request: Request, user: CurrentUserDep, service: AuthServiceDep
) -> dict[str, int]:
    count = await service.logout_all(user.id)
    return {"revoked_count": count}


# ─────────────────────────────────────────
#  Kakao OAuth (rate limit 추가)
# ─────────────────────────────────────────


@router.get(
    "/kakao/login",
    summary="카카오 로그인 시작",
    description="카카오 로그인 페이지로 302 Redirect.",
    responses={
        302: {"description": "카카오 로그인 페이지로 리다이렉트"},
        429: {"description": "Too many requests"},
    },
)
@limiter.limit(RateLimits.KAKAO_LOGIN)
async def kakao_login_initiate(
    request: Request, service: KakaoOAuthServiceDep
) -> RedirectResponse:
    authorize_url, _state = await service.initiate_login()
    return RedirectResponse(url=authorize_url, status_code=302)


class KakaoCallbackResponse(BaseModel):
    """카카오 콜백 응답."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: UserResponse
    is_new_user: bool


@router.get(
    "/kakao/callback",
    response_model=KakaoCallbackResponse,
    summary="카카오 로그인 콜백",
)
@limiter.limit(RateLimits.KAKAO_CALLBACK)
async def kakao_login_callback(
    request: Request,
    service: KakaoOAuthServiceDep,
    code: str = Query(..., description="카카오 인증 코드"),
    state: str = Query(..., description="CSRF 방어 state"),
) -> KakaoCallbackResponse:
    pair, user, is_new_user = await service.handle_callback(code=code, state=state)
    return KakaoCallbackResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        token_type=pair.token_type,
        expires_in=pair.expires_in,
        user=UserResponse.model_validate(user),
        is_new_user=is_new_user,
    )
