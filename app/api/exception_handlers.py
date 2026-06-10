"""전역 예외 핸들러.

도메인 예외 → HTTP 응답 변환.
Service 가 raise EmailAlreadyExists() 하면 자동으로 409 응답.

Spring 의 @ControllerAdvice + @ExceptionHandler 와 같은 역할.

main.py 에서 app.add_exception_handler() 로 등록.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.domain.auth.exceptions import (
    InvalidCredentials,
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


# ─────────────────────────────────────────
#  공통 응답 헬퍼
# ─────────────────────────────────────────


def _error_response(status_code: int, code: str, message: str, **extra) -> JSONResponse:
    """일관된 에러 응답 포맷."""
    body = {
        "error": {
            "code": code,
            "message": message,
        }
    }
    if extra:
        body["error"]["details"] = extra
    return JSONResponse(status_code=status_code, content=body)


# ─────────────────────────────────────────
#  User 도메인 핸들러 (Day 10)
# ─────────────────────────────────────────


async def email_already_exists_handler(
    request: Request, exc: EmailAlreadyExists
) -> JSONResponse:
    logger.info("email_already_exists", email=exc.email, path=request.url.path)
    return _error_response(
        status_code=409,
        code="EMAIL_ALREADY_EXISTS",
        message="이미 사용 중인 이메일입니다",
        email=exc.email,
    )


async def nickname_already_exists_handler(
    request: Request, exc: NicknameAlreadyExists
) -> JSONResponse:
    logger.info(
        "nickname_already_exists", nickname=exc.nickname, path=request.url.path
    )
    return _error_response(
        status_code=409,
        code="NICKNAME_ALREADY_EXISTS",
        message="이미 사용 중인 닉네임입니다",
        nickname=exc.nickname,
    )


async def oauth_identity_already_exists_handler(
    request: Request, exc: OAuthIdentityAlreadyExists
) -> JSONResponse:
    logger.info(
        "oauth_identity_exists",
        provider=exc.provider,
        provider_id=exc.provider_id,
        path=request.url.path,
    )
    return _error_response(
        status_code=409,
        code="OAUTH_IDENTITY_ALREADY_EXISTS",
        message="이미 가입된 OAuth 계정입니다",
        provider=exc.provider,
    )


async def user_not_found_handler(
    request: Request, exc: UserNotFound
) -> JSONResponse:
    logger.info("user_not_found", user_id=exc.user_id, path=request.url.path)
    return _error_response(
        status_code=404,
        code="USER_NOT_FOUND",
        message="사용자를 찾을 수 없습니다",
    )


async def user_already_deleted_handler(
    request: Request, exc: UserAlreadyDeleted
) -> JSONResponse:
    logger.info("user_already_deleted", user_id=exc.user_id, path=request.url.path)
    return _error_response(
        status_code=410,
        code="USER_ALREADY_DELETED",
        message="이미 삭제된 사용자입니다",
    )


# ─────────────────────────────────────────
#  Auth 도메인 핸들러 (Day 11)
# ─────────────────────────────────────────


async def user_not_found_for_auth_handler(
    request: Request, exc: UserNotFoundForAuth
) -> JSONResponse:
    """로그인 시 사용자 없음 → 404.

    보안: '사용자가 없습니다' 같은 상세한 메시지는 노출하지 않음.
    """
    logger.info("login_user_not_found", path=request.url.path)
    return _error_response(
        status_code=404,
        code="USER_NOT_FOUND",
        message="사용자를 찾을 수 없습니다",
    )


async def refresh_token_not_found_handler(
    request: Request, exc: RefreshTokenNotFound
) -> JSONResponse:
    """Refresh token 못 찾음 → 401.

    원인 (어떤 거든 같은 응답 - 보안):
    - 무효화됨 (로그아웃)
    - 존재한 적 없음 (변조 / 위조)
    - 만료됨
    """
    logger.info("refresh_token_not_found", path=request.url.path)
    return _error_response(
        status_code=401,
        code="INVALID_REFRESH_TOKEN",
        message="유효하지 않은 refresh token 입니다",
    )


async def refresh_token_revoked_handler(
    request: Request, exc: RefreshTokenRevoked
) -> JSONResponse:
    """Refresh token 무효화됨 → 401."""
    logger.info("refresh_token_revoked", path=request.url.path)
    return _error_response(
        status_code=401,
        code="REFRESH_TOKEN_REVOKED",
        message="무효화된 refresh token 입니다",
    )


async def invalid_credentials_handler(
    request: Request, exc: InvalidCredentials
) -> JSONResponse:
    """일반 인증 정보 오류 → 401.

    구체적 원인 안 알려줌 (보안 - 토큰 만료/위조/사용자 일치 등 구분 X).
    """
    logger.info("invalid_credentials", path=request.url.path)
    return _error_response(
        status_code=401,
        code="INVALID_CREDENTIALS",
        message="인증 정보가 올바르지 않습니다",
    )


# ─────────────────────────────────────────
#  등록 헬퍼
# ─────────────────────────────────────────


def register_exception_handlers(app: FastAPI) -> None:
    """main.py 에서 호출."""
    # User 도메인
    app.add_exception_handler(EmailAlreadyExists, email_already_exists_handler)
    app.add_exception_handler(NicknameAlreadyExists, nickname_already_exists_handler)
    app.add_exception_handler(
        OAuthIdentityAlreadyExists, oauth_identity_already_exists_handler
    )
    app.add_exception_handler(UserNotFound, user_not_found_handler)
    app.add_exception_handler(UserAlreadyDeleted, user_already_deleted_handler)

    # Auth 도메인 (⭐ Day 11A 에서 추가)
    app.add_exception_handler(UserNotFoundForAuth, user_not_found_for_auth_handler)
    app.add_exception_handler(RefreshTokenNotFound, refresh_token_not_found_handler)
    app.add_exception_handler(RefreshTokenRevoked, refresh_token_revoked_handler)
    app.add_exception_handler(InvalidCredentials, invalid_credentials_handler)
