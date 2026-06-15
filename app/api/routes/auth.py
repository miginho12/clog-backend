"""Auth 엔드포인트.

기존 (Day 11):
- POST /auth/login         시뮬레이션 (deprecated 예정)
- POST /auth/refresh
- POST /auth/logout
- POST /auth/logout-all

추가 (Day 12 ⭐):
- GET  /auth/kakao/login     카카오 로그인 시작 (302 Redirect)
- GET  /auth/kakao/callback  카카오 콜백 처리 → JWT 응답
"""

from fastapi import APIRouter, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.api.dependencies import CurrentUserDep
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
#  기존 엔드포인트 (Day 11)
# ─────────────────────────────────────────


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="로그인 (시뮬레이션 - Day 12 이후 deprecated)",
    description="user_id 직접 받기 — 개발/테스트용. 운영에선 /auth/kakao/* 사용.",
)
async def login(payload: LoginRequest, service: AuthServiceDep) -> TokenResponse:
    pair = await service.login(payload.user_id)
    return TokenResponse.from_pair(pair)


@router.post(
    "/refresh",
    response_model=AccessTokenResponse,
    summary="Access token 갱신",
)
async def refresh_token(
    payload: RefreshRequest, service: AuthServiceDep
) -> AccessTokenResponse:
    from app.core.config import get_settings

    new_access = await service.refresh_access_token(payload.refresh_token)
    settings = get_settings()
    return AccessTokenResponse(
        access_token=new_access,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="로그아웃",
)
async def logout(payload: LogoutRequest, service: AuthServiceDep) -> None:
    await service.logout(payload.refresh_token)


@router.post(
    "/logout-all",
    status_code=status.HTTP_200_OK,
    summary="모든 디바이스 로그아웃",
)
async def logout_all(user: CurrentUserDep, service: AuthServiceDep) -> dict[str, int]:
    count = await service.logout_all(user.id)
    return {"revoked_count": count}


# ─────────────────────────────────────────
#  Kakao OAuth (⭐ Day 12)
# ─────────────────────────────────────────


@router.get(
    "/kakao/login",
    summary="카카오 로그인 시작",
    description=(
        "카카오 로그인 페이지로 302 Redirect 합니다. "
        "사용자는 카카오에서 로그인 후 /auth/kakao/callback 으로 돌아옵니다."
    ),
    responses={
        302: {"description": "카카오 로그인 페이지로 리다이렉트"},
    },
)
async def kakao_login_initiate(service: KakaoOAuthServiceDep) -> RedirectResponse:
    """카카오 로그인 시작.

    1. State 생성 (CSRF 방어, Redis 5분 저장)
    2. 카카오 authorize URL 생성
    3. 302 Redirect → 사용자 브라우저가 카카오로 이동
    """
    authorize_url, _state = await service.initiate_login()
    return RedirectResponse(url=authorize_url, status_code=302)


class KakaoCallbackResponse(BaseModel):
    """카카오 콜백 응답 (Q6 의 옵션 a: JSON 응답)."""

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
    description=(
        "카카오에서 인증 완료 후 호출되는 콜백. "
        "code 와 state 를 받아 우리 시스템 JWT 발급."
    ),
    responses={
        200: {"description": "로그인 성공 — 우리 시스템 JWT 발급"},
        400: {"description": "잘못된 code 또는 state"},
        401: {"description": "state 검증 실패 (CSRF 의심)"},
        502: {"description": "카카오 API 통신 실패"},
    },
)
async def kakao_login_callback(
    service: KakaoOAuthServiceDep,
    code: str = Query(..., description="카카오 인증 코드"),
    state: str = Query(..., description="CSRF 방어 state"),
) -> KakaoCallbackResponse:
    """카카오 콜백 처리.

    흐름:
    1. State 검증 (CSRF)
    2. Code → 카카오 access_token 교환
    3. 카카오 사용자 정보 조회
    4. User 자동 생성/조회
    5. 우리 시스템 JWT 발급
    """
    pair, user, is_new_user = await service.handle_callback(code=code, state=state)

    return KakaoCallbackResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        token_type=pair.token_type,
        expires_in=pair.expires_in,
        user=UserResponse.model_validate(user),
        is_new_user=is_new_user,
    )
