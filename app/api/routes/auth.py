"""Auth 엔드포인트.

POST /auth/login        로그인 → 토큰 페어
POST /auth/refresh      access 갱신
POST /auth/logout       로그아웃 (refresh 무효화)
POST /auth/logout-all   모든 디바이스 로그아웃
"""

from fastapi import APIRouter, status

from app.api.dependencies import CurrentUserDep
from app.domain.auth.dependencies import AuthServiceDep
from app.domain.auth.schemas import (
    AccessTokenResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="로그인 (시뮬레이션)",
    description=(
        "현재는 user_id 직접 받아 토큰 발급. "
        "Day 12 부터 카카오 OAuth 콜백으로 변경 예정."
    ),
    responses={
        200: {"description": "토큰 발급 성공"},
        404: {"description": "사용자 없음"},
    },
)
async def login(payload: LoginRequest, service: AuthServiceDep) -> TokenResponse:
    pair = await service.login(payload.user_id)
    return TokenResponse.from_pair(pair)


@router.post(
    "/refresh",
    response_model=AccessTokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Access token 갱신",
    description=(
        "Refresh token 으로 새 access token 발급. "
        "Refresh token 은 그대로 유지 (rotation 은 Day 11B 검토)."
    ),
    responses={
        200: {"description": "갱신 성공"},
        401: {"description": "잘못된 / 만료된 refresh token"},
    },
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
    description="Refresh token 무효화. 잘못된 토큰이어도 204 응답 (보안).",
)
async def logout(payload: LogoutRequest, service: AuthServiceDep) -> None:
    await service.logout(payload.refresh_token)


@router.post(
    "/logout-all",
    status_code=status.HTTP_200_OK,
    summary="모든 디바이스 로그아웃",
    description="현재 인증된 사용자의 모든 refresh token 무효화.",
)
async def logout_all(user: CurrentUserDep, service: AuthServiceDep) -> dict[str, int]:
    """⭐ 보호된 엔드포인트 — Bearer 토큰 필요."""
    count = await service.logout_all(user.id)
    return {"revoked_count": count}
