"""전역 예외 핸들러."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.domain.auth.exceptions import (
    InvalidCredentials,
    KakaoAPIError,
    KakaoEmailNotAvailable,
    KakaoTokenExchangeFailed,
    KakaoUserInfoFailed,
    OAuthStateInvalid,
    RefreshTokenNotFound,
    RefreshTokenRevoked,
    UserNotFoundForAuth,
)
from app.domain.users.exceptions import (
    EmailAlreadyExists,
    NicknameAlreadyExists,
    OAuthIdentityAlreadyExists,
    UserAlreadyDeleted,
    UserNotFound,
)

logger = get_logger(__name__)


def _error_response(status_code: int, code: str, message: str, **extra) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    if extra:
        body["error"]["details"] = extra
    return JSONResponse(status_code=status_code, content=body)


# ─────────────────────────────────────────
#  User 도메인 (Day 10)
# ─────────────────────────────────────────


async def email_already_exists_handler(
    request: Request, exc: EmailAlreadyExists
) -> JSONResponse:
    logger.info("email_already_exists", email=exc.email, path=request.url.path)
    return _error_response(409, "EMAIL_ALREADY_EXISTS", "이미 사용 중인 이메일입니다", email=exc.email)


async def nickname_already_exists_handler(
    request: Request, exc: NicknameAlreadyExists
) -> JSONResponse:
    logger.info("nickname_already_exists", nickname=exc.nickname, path=request.url.path)
    return _error_response(409, "NICKNAME_ALREADY_EXISTS", "이미 사용 중인 닉네임입니다", nickname=exc.nickname)


async def oauth_identity_already_exists_handler(
    request: Request, exc: OAuthIdentityAlreadyExists
) -> JSONResponse:
    logger.info("oauth_identity_exists", provider=exc.provider, path=request.url.path)
    return _error_response(409, "OAUTH_IDENTITY_ALREADY_EXISTS", "이미 가입된 OAuth 계정입니다", provider=exc.provider)


async def user_not_found_handler(
    request: Request, exc: UserNotFound
) -> JSONResponse:
    logger.info("user_not_found", user_id=exc.user_id, path=request.url.path)
    return _error_response(404, "USER_NOT_FOUND", "사용자를 찾을 수 없습니다")


async def user_already_deleted_handler(
    request: Request, exc: UserAlreadyDeleted
) -> JSONResponse:
    logger.info("user_already_deleted", user_id=exc.user_id, path=request.url.path)
    return _error_response(410, "USER_ALREADY_DELETED", "이미 삭제된 사용자입니다")


# ─────────────────────────────────────────
#  Auth 도메인 (Day 11)
# ─────────────────────────────────────────


async def user_not_found_for_auth_handler(
    request: Request, exc: UserNotFoundForAuth
) -> JSONResponse:
    logger.info("login_user_not_found", path=request.url.path)
    return _error_response(404, "USER_NOT_FOUND", "사용자를 찾을 수 없습니다")


async def refresh_token_not_found_handler(
    request: Request, exc: RefreshTokenNotFound
) -> JSONResponse:
    logger.info("refresh_token_not_found", path=request.url.path)
    return _error_response(401, "INVALID_REFRESH_TOKEN", "유효하지 않은 refresh token 입니다")


async def refresh_token_revoked_handler(
    request: Request, exc: RefreshTokenRevoked
) -> JSONResponse:
    logger.info("refresh_token_revoked", path=request.url.path)
    return _error_response(401, "REFRESH_TOKEN_REVOKED", "무효화된 refresh token 입니다")


async def invalid_credentials_handler(
    request: Request, exc: InvalidCredentials
) -> JSONResponse:
    logger.info("invalid_credentials", path=request.url.path)
    return _error_response(401, "INVALID_CREDENTIALS", "인증 정보가 올바르지 않습니다")


# ─────────────────────────────────────────
#  Kakao OAuth (Day 12 ⭐)
# ─────────────────────────────────────────


async def oauth_state_invalid_handler(
    request: Request, exc: OAuthStateInvalid
) -> JSONResponse:
    """State 검증 실패 → 401 (CSRF 의심)."""
    logger.warning("oauth_state_invalid", path=request.url.path)
    return _error_response(
        401,
        "OAUTH_STATE_INVALID",
        "OAuth 인증 세션이 만료되었거나 잘못된 요청입니다. 다시 로그인해주세요.",
    )


async def kakao_token_exchange_failed_handler(
    request: Request, exc: KakaoTokenExchangeFailed
) -> JSONResponse:
    """카카오 토큰 교환 실패 → 400 (잘못된 code).

    원인 노출 X (보안). 상세는 서버 로그에만.
    """
    logger.warning(
        "kakao_token_exchange_failed",
        kakao_error=exc.error,
        path=request.url.path,
    )
    return _error_response(
        400,
        "KAKAO_TOKEN_EXCHANGE_FAILED",
        "카카오 인증에 실패했습니다. 다시 시도해주세요.",
    )


async def kakao_user_info_failed_handler(
    request: Request, exc: KakaoUserInfoFailed
) -> JSONResponse:
    """카카오 사용자 정보 조회 실패 → 502 (외부 API 장애)."""
    logger.warning(
        "kakao_user_info_failed",
        kakao_error=exc.error,
        kakao_code=exc.code,
        path=request.url.path,
    )
    return _error_response(
        502,
        "KAKAO_USER_INFO_FAILED",
        "카카오 사용자 정보를 가져오지 못했습니다.",
    )


async def kakao_email_not_available_handler(
    request: Request, exc: KakaoEmailNotAvailable
) -> JSONResponse:
    """이메일 동의 안 함 → 400.

    사용자에게 이메일 동의가 필요함을 안내.
    """
    logger.info("kakao_email_not_agreed", kakao_id=exc.kakao_id, path=request.url.path)
    return _error_response(
        400,
        "KAKAO_EMAIL_REQUIRED",
        "회원가입에는 카카오 이메일 동의가 필요합니다. 카카오 로그인 시 이메일 제공에 동의해주세요.",
    )


async def kakao_api_error_handler(
    request: Request, exc: KakaoAPIError
) -> JSONResponse:
    """카카오 API 통신 실패 → 502."""
    logger.error("kakao_api_error", error=str(exc), path=request.url.path)
    return _error_response(
        502,
        "KAKAO_API_ERROR",
        "카카오 서비스와의 통신에 문제가 발생했습니다. 잠시 후 다시 시도해주세요.",
    )


# ─────────────────────────────────────────
#  등록 헬퍼
# ─────────────────────────────────────────


def register_exception_handlers(app: FastAPI) -> None:
    # User 도메인
    app.add_exception_handler(EmailAlreadyExists, email_already_exists_handler)
    app.add_exception_handler(NicknameAlreadyExists, nickname_already_exists_handler)
    app.add_exception_handler(
        OAuthIdentityAlreadyExists, oauth_identity_already_exists_handler
    )
    app.add_exception_handler(UserNotFound, user_not_found_handler)
    app.add_exception_handler(UserAlreadyDeleted, user_already_deleted_handler)

    # Auth 도메인 (Day 11)
    app.add_exception_handler(UserNotFoundForAuth, user_not_found_for_auth_handler)
    app.add_exception_handler(RefreshTokenNotFound, refresh_token_not_found_handler)
    app.add_exception_handler(RefreshTokenRevoked, refresh_token_revoked_handler)
    app.add_exception_handler(InvalidCredentials, invalid_credentials_handler)

    # Kakao OAuth (Day 12 ⭐)
    app.add_exception_handler(OAuthStateInvalid, oauth_state_invalid_handler)
    app.add_exception_handler(KakaoTokenExchangeFailed, kakao_token_exchange_failed_handler)
    app.add_exception_handler(KakaoUserInfoFailed, kakao_user_info_failed_handler)
    app.add_exception_handler(KakaoEmailNotAvailable, kakao_email_not_available_handler)
    app.add_exception_handler(KakaoAPIError, kakao_api_error_handler)
