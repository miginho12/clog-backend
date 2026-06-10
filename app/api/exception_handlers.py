"""전역 예외 핸들러.

도메인 예외 → HTTP 응답 변환.
Service 가 raise EmailAlreadyExists() 하면 자동으로 409 응답.

Spring 의 @ControllerAdvice + @ExceptionHandler 와 같은 역할.

main.py 에서 app.add_exception_handler() 로 등록.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
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
    """일관된 에러 응답 포맷.

    {
        "error": {
            "code": "EMAIL_ALREADY_EXISTS",
            "message": "...",
            "details": {...}
        }
    }
    """
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
#  핸들러
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
#  등록 헬퍼
# ─────────────────────────────────────────


def register_exception_handlers(app: FastAPI) -> None:
    """main.py 에서 호출."""
    app.add_exception_handler(EmailAlreadyExists, email_already_exists_handler)
    app.add_exception_handler(NicknameAlreadyExists, nickname_already_exists_handler)
    app.add_exception_handler(
        OAuthIdentityAlreadyExists, oauth_identity_already_exists_handler
    )
    app.add_exception_handler(UserNotFound, user_not_found_handler)
    app.add_exception_handler(UserAlreadyDeleted, user_already_deleted_handler)
