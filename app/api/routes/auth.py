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
from app.domain.auth.local_schemas import (
    LocalLoginRequest,
    PasswordResetConfirmSchema,
    PasswordResetRequestResponse,
    PasswordResetRequestSchema,
    PasswordResetVerifyResponse,
    PasswordResetVerifySchema,
    SignupRequest,
    SignupResponse,
)
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
#  자체 회원가입/로그인 응답 (Day 17 ⭐)
# ─────────────────────────────────────────


class LocalAuthResponse(BaseModel):
    """자체 회원가입/로그인 공통 응답.

    카카오 콜백 응답(KakaoCallbackResponse)과 동일 구조.
    프론트가 동일하게 처리할 수 있도록 통일.
    """

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: UserResponse


# ─────────────────────────────────────────
#  /auth/login (Day 14 ⭐ 환경별 비활성화)
# ─────────────────────────────────────────


# ─────────────────────────────────────────
#  자체 회원가입 (Day 17 ⭐)
# ─────────────────────────────────────────


@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="자체 회원가입 (이메일 + 비밀번호)",
    description=(
        "카카오 없이 이메일/비밀번호로 가입. "
        "비밀번호 정책: 최소 12자 + 영문/숫자/특수문자. "
        "가입 후 인증 메일 발송 → 이메일 인증 완료해야 로그인 가능."
    ),
)
@limiter.limit(RateLimits.KAKAO_LOGIN)
async def signup(
    request: Request, payload: SignupRequest, service: AuthServiceDep
) -> SignupResponse:
    """자체 회원가입. 인증 메일 발송 (즉시 로그인 아님).

    Raises:
        409: 이메일/닉네임 중복 (예외 핸들러에서 변환)
        422: 비밀번호 정책 위반 (스키마 검증)
    """
    user = await service.signup(
        email=payload.email,
        password=payload.password,
        nickname=payload.nickname,
        profile_image_url=payload.profile_image_url,
    )
    return SignupResponse(email=user.email)


@router.get(
    "/verify",
    summary="이메일 인증",
    description="인증 메일 토큰으로 이메일 인증. 프론트 /verify 페이지가 호출.",
)
async def verify_email(
    token: str, service: AuthServiceDep
) -> dict:
    """이메일 인증 토큰 처리 → JSON 결과 (프론트가 표시)."""
    ok = await service.verify_email(token)
    return {"verified": ok}


# ─────────────────────────────────────────
#  비밀번호 찾기
# ─────────────────────────────────────────


@router.post(
    "/password-reset/request",
    response_model=PasswordResetRequestResponse,
    summary="비밀번호 재설정 코드 요청",
    description=(
        "가입된 이메일이면 6자리 코드를 메일로 발송. "
        "계정 존재 여부를 노출하지 않기 위해 이메일이 없어도 동일하게 성공 응답."
    ),
)
@limiter.limit(RateLimits.PASSWORD_RESET_REQUEST)
async def request_password_reset(
    request: Request,
    payload: PasswordResetRequestSchema,
    service: AuthServiceDep,
) -> PasswordResetRequestResponse:
    await service.request_password_reset(payload.email)
    return PasswordResetRequestResponse()


@router.post(
    "/password-reset/verify",
    response_model=PasswordResetVerifyResponse,
    summary="비밀번호 재설정 코드 확인",
    description="코드가 맞으면 다음 단계(새 비밀번호 설정)에 쓸 reset_token 발급.",
)
@limiter.limit(RateLimits.PASSWORD_RESET_VERIFY)
async def verify_password_reset(
    request: Request,
    payload: PasswordResetVerifySchema,
    service: AuthServiceDep,
) -> PasswordResetVerifyResponse:
    reset_token = await service.verify_password_reset_code(
        email=payload.email, code=payload.code
    )
    return PasswordResetVerifyResponse(reset_token=reset_token)


@router.post(
    "/password-reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="새 비밀번호 설정",
)
@limiter.limit(RateLimits.PASSWORD_RESET_VERIFY)
async def confirm_password_reset(
    request: Request,
    payload: PasswordResetConfirmSchema,
    service: AuthServiceDep,
) -> None:
    await service.confirm_password_reset(
        reset_token=payload.reset_token, new_password=payload.new_password
    )


# ─────────────────────────────────────────
#  자체 로그인 (Day 17 ⭐)
# ─────────────────────────────────────────


@router.post(
    "/login/local",
    response_model=LocalAuthResponse,
    summary="자체 로그인 (이메일 + 비밀번호)",
    description=(
        "자체 가입자 전용 로그인. "
        "보안상 이메일 없음/비번 불일치/OAuth 전용 계정을 "
        "구분하지 않고 동일하게 401 응답."
    ),
)
@limiter.limit(RateLimits.KAKAO_LOGIN)
async def login_local(
    request: Request, payload: LocalLoginRequest, service: AuthServiceDep
) -> LocalAuthResponse:
    """자체 로그인.

    Raises:
        401: 인증 실패 (예외 핸들러에서 변환)
    """
    pair, user = await service.local_login(
        email=payload.email,
        password=payload.password,
    )
    return LocalAuthResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        token_type=pair.token_type,
        expires_in=pair.expires_in,
        user=UserResponse.model_validate(user),
    )


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
    summary="카카오 로그인 콜백",
    description=(
        "카카오가 인가 코드를 들고 돌아오는 곳. "
        "토큰을 생성한 뒤 프론트엔드로 302 리다이렉트한다. "
        "토큰은 URL fragment(#)에 담아 전달 (서버 로그/Referer 노출 방지)."
    ),
    responses={302: {"description": "프론트엔드 콜백 페이지로 리다이렉트"}},
)
@limiter.limit(RateLimits.KAKAO_CALLBACK)
async def kakao_login_callback(
    request: Request,
    service: KakaoOAuthServiceDep,
    code: str = Query(..., description="카카오 인증 코드"),
    state: str = Query(..., description="CSRF 방어 state"),
) -> RedirectResponse:
    pair, user, is_new_user = await service.handle_callback(code=code, state=state)

    settings = get_settings()
    # 토큰을 fragment(#)에 담아 프론트 콜백 페이지로 리다이렉트
    # fragment 는 서버로 전송되지 않아 로그/Referer 에 안 남음
    fragment = (
        f"access_token={pair.access_token}"
        f"&refresh_token={pair.refresh_token}"
        f"&expires_in={pair.expires_in}"
        f"&is_new={str(is_new_user).lower()}"
    )
    redirect_url = f"{settings.frontend_url}/auth/callback#{fragment}"
    return RedirectResponse(url=redirect_url, status_code=302)
