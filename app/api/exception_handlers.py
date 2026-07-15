"""전역 예외 핸들러."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.core.logging import get_logger
from app.core.password import PasswordPolicyError
from app.domain.auth.exceptions import (
    AccountBanned,
    EmailAlreadyRegistered,
    EmailNotVerified,
    InvalidCredentials,
    KakaoAPIError,
    KakaoEmailNotAvailable,
    KakaoTokenExchangeFailed,
    KakaoUserInfoFailed,
    LocalLoginNotAvailable,
    NicknameAlreadyTaken,
    OAuthStateInvalid,
    RefreshTokenNotFound,
    RefreshTokenRevoked,
    UserNotFoundForAuth,
)
from app.domain.climbing.exceptions import (
    ClimbingLogForbidden,
    ClimbingLogNotFound,
)
from app.domain.comment_likes.exceptions import CommentLikeTargetNotFound
from app.domain.comments.exceptions import (
    CommentForbidden,
    CommentNotFound,
    CommentParentInvalid,
    CommentTargetNotFound,
)
from app.domain.follows.exceptions import (
    CannotFollowSelf,
    FollowRequestNotFound,
    FollowTargetNotFound,
)
from app.domain.grade.exceptions import (
    GymGradeSystemAlreadyExists,
    GymGradeSystemForbidden,
    GymGradeSystemNotFound,
    GymGradeSystemNotFoundById,
    InvalidRankingPeriod,
)
from app.domain.likes.exceptions import LikeTargetNotFound
from app.domain.media.service import MediaError
from app.domain.users.exceptions import (
    CannotBanSelf,
    CurrentPasswordMismatch,
    EmailAlreadyExists,
    NicknameAlreadyExists,
    OAuthIdentityAlreadyExists,
    PasswordChangeNotAllowed,
    UserAlreadyDeleted,
    UserNotFound,
    UserProfilePrivate,  # ⭐ Day 14
    UserUpdateForbidden,  # ⭐ Day 14
)

logger = get_logger(__name__)


def _error_response(status_code: int, code: str, message: str, **extra) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    if extra:
        body["error"]["details"] = extra
    return JSONResponse(status_code=status_code, content=body)


# ─────────────────────────────────────────
#  User 도메인 (Day 10 + Day 14)
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


# ⭐ Day 14 - 권한 예외


async def user_profile_private_handler(
    request: Request, exc: UserProfilePrivate
) -> JSONResponse:
    """비공개 프로필 조회 시도 → 403."""
    logger.info("user_profile_private", user_id=exc.user_id, path=request.url.path)
    return _error_response(
        403,
        "USER_PROFILE_PRIVATE",
        "비공개 프로필입니다. 접근 권한이 없습니다.",
    )


async def user_update_forbidden_handler(
    request: Request, exc: UserUpdateForbidden
) -> JSONResponse:
    """본인 아닌 사용자 수정 시도 → 403."""
    logger.warning(
        "user_update_forbidden",
        target_user_id=exc.user_id,
        current_user_id=exc.current_user_id,
        path=request.url.path,
    )
    return _error_response(
        403,
        "USER_UPDATE_FORBIDDEN",
        "본인 정보만 수정할 수 있습니다.",
    )


# ─────────────────────────────────────────
#  Auth 도메인 (Day 11 - 변경 없음)
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
#  Kakao OAuth (Day 12 - 변경 없음)
# ─────────────────────────────────────────


async def oauth_state_invalid_handler(
    request: Request, exc: OAuthStateInvalid
) -> JSONResponse:
    logger.warning("oauth_state_invalid", path=request.url.path)
    return _error_response(
        401,
        "OAUTH_STATE_INVALID",
        "OAuth 인증 세션이 만료되었거나 잘못된 요청입니다. 다시 로그인해주세요.",
    )


async def kakao_token_exchange_failed_handler(
    request: Request, exc: KakaoTokenExchangeFailed
) -> JSONResponse:
    logger.warning("kakao_token_exchange_failed", kakao_error=exc.error, path=request.url.path)
    return _error_response(400, "KAKAO_TOKEN_EXCHANGE_FAILED", "카카오 인증에 실패했습니다. 다시 시도해주세요.")


async def email_not_verified_handler(
    request: Request, exc: EmailNotVerified
) -> JSONResponse:
    return _error_response(
        403,
        "EMAIL_NOT_VERIFIED",
        "이메일 인증이 필요해요. 메일함에서 인증을 완료해 주세요.",
    )


async def kakao_user_info_failed_handler(
    request: Request, exc: KakaoUserInfoFailed
) -> JSONResponse:
    logger.warning("kakao_user_info_failed", kakao_error=exc.error, path=request.url.path)
    return _error_response(502, "KAKAO_USER_INFO_FAILED", "카카오 사용자 정보를 가져오지 못했습니다.")


async def kakao_email_not_available_handler(
    request: Request, exc: KakaoEmailNotAvailable
) -> JSONResponse:
    logger.info("kakao_email_not_agreed", kakao_id=exc.kakao_id, path=request.url.path)
    return _error_response(
        400,
        "KAKAO_EMAIL_REQUIRED",
        "회원가입에는 카카오 이메일 동의가 필요합니다. 카카오 로그인 시 이메일 제공에 동의해주세요.",
    )


async def kakao_api_error_handler(
    request: Request, exc: KakaoAPIError
) -> JSONResponse:
    logger.error("kakao_api_error", error=str(exc), path=request.url.path)
    return _error_response(502, "KAKAO_API_ERROR", "카카오 서비스와의 통신에 문제가 발생했습니다.")


# ─────────────────────────────────────────
#  Rate Limit (Day 14 ⭐ 추가)
# ─────────────────────────────────────────


async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Rate limit 초과 → 429.

    slowapi 의 기본 핸들러를 우리 응답 형식에 맞춤.
    Retry-After 헤더 자동 (slowapi).
    """
    logger.warning(
        "rate_limit_exceeded",
        path=request.url.path,
        limit=str(exc.detail),
    )
    response = _error_response(
        429,
        "RATE_LIMIT_EXCEEDED",
        f"요청이 너무 많습니다. 잠시 후 다시 시도해주세요. (limit: {exc.detail})",
    )
    # Retry-After 헤더 (slowapi 가 자동 추가하지만 명시)
    response.headers["Retry-After"] = "60"
    return response


# ─────────────────────────────────────────
#  등록 헬퍼
# ─────────────────────────────────────────


async def email_already_registered_handler(
    request: Request, exc: EmailAlreadyRegistered
) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_409_CONFLICT,
        code="email_already_registered",
        message="이미 가입된 이메일입니다",
    )


async def nickname_already_taken_handler(
    request: Request, exc: NicknameAlreadyTaken
) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_409_CONFLICT,
        code="nickname_already_taken",
        message="이미 사용 중인 닉네임입니다",
    )


async def local_login_not_available_handler(
    request: Request, exc: LocalLoginNotAvailable
) -> JSONResponse:
    # 계정 열거 방어: 구체적 사유를 노출하지 않음
    return _error_response(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="invalid_credentials",
        message="이메일 또는 비밀번호가 올바르지 않습니다",
    )


async def account_banned_handler(
    request: Request, exc: AccountBanned
) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code="account_banned",
        message="차단된 계정입니다. 관리자에게 문의하세요",
    )


async def cannot_ban_self_handler(
    request: Request, exc: CannotBanSelf
) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="cannot_ban_self",
        message="자기 자신은 차단할 수 없습니다",
    )


async def climbing_log_not_found_handler(
    request: Request, exc: ClimbingLogNotFound
) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code="climbing_log_not_found",
        message="기록을 찾을 수 없습니다",
    )


async def climbing_log_forbidden_handler(
    request: Request, exc: ClimbingLogForbidden
) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code="climbing_log_forbidden",
        message="본인의 기록만 수정/삭제할 수 있습니다",
    )


async def gym_grade_system_not_found_handler(
    request: Request, exc: GymGradeSystemNotFound
) -> JSONResponse:
    # 미등록 짐 → 프론트가 details.gym_name 으로 '색체계 등록' 유도 (구현 6)
    return _error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="gym_grade_system_not_found",
        message="등록되지 않은 암장입니다",
        gym_name=exc.gym_name,
    )


async def gym_grade_system_already_exists_handler(
    request: Request, exc: GymGradeSystemAlreadyExists
) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_409_CONFLICT,
        code="gym_grade_system_already_exists",
        message="이미 등록된 암장입니다",
        gym_name=exc.gym_name,
    )


async def gym_grade_system_not_found_by_id_handler(
    request: Request, exc: GymGradeSystemNotFoundById
) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code="gym_grade_system_not_found",
        message="색체계를 찾을 수 없습니다",
        system_id=exc.system_id,
    )


async def gym_grade_system_forbidden_handler(
    request: Request, exc: GymGradeSystemForbidden
) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code="gym_grade_system_forbidden",
        message="본인이 등록한 색체계만 수정/삭제할 수 있습니다",
        system_id=exc.system_id,
    )


async def invalid_ranking_period_handler(
    request: Request, exc: InvalidRankingPeriod
) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="invalid_ranking_period",
        message="랭킹 기간 파라미터가 올바르지 않습니다",
        reason=exc.reason,
    )


async def media_error_handler(request: Request, exc: MediaError) -> JSONResponse:
    return _error_response(400, exc.code, exc.message, **exc.details)


async def like_target_not_found_handler(
    request: Request, exc: LikeTargetNotFound
) -> JSONResponse:
    return _error_response(404, "LIKE_TARGET_NOT_FOUND", "좋아요 대상 게시물을 찾을 수 없습니다")


async def comment_target_not_found_handler(request: Request, exc: CommentTargetNotFound) -> JSONResponse:
    return _error_response(404, "COMMENT_TARGET_NOT_FOUND", "댓글 대상 게시물을 찾을 수 없습니다")


async def comment_not_found_handler(request: Request, exc: CommentNotFound) -> JSONResponse:
    return _error_response(404, "COMMENT_NOT_FOUND", "댓글을 찾을 수 없습니다")


async def comment_forbidden_handler(request: Request, exc: CommentForbidden) -> JSONResponse:
    return _error_response(403, "COMMENT_FORBIDDEN", "본인의 댓글만 수정/삭제할 수 있습니다")


async def comment_parent_invalid_handler(request: Request, exc: CommentParentInvalid) -> JSONResponse:
    return _error_response(400, "COMMENT_PARENT_INVALID", "유효하지 않은 부모 댓글입니다")


async def comment_like_target_not_found_handler(request: Request, exc: CommentLikeTargetNotFound) -> JSONResponse:
    return _error_response(404, "COMMENT_LIKE_TARGET_NOT_FOUND", "좋아요 대상 댓글을 찾을 수 없습니다")


async def cannot_follow_self_handler(
    request: Request, exc: CannotFollowSelf
) -> JSONResponse:
    return _error_response(400, "CANNOT_FOLLOW_SELF", "자기 자신은 팔로우할 수 없습니다")


async def follow_target_not_found_handler(
    request: Request, exc: FollowTargetNotFound
) -> JSONResponse:
    return _error_response(404, "FOLLOW_TARGET_NOT_FOUND", "팔로우 대상 사용자를 찾을 수 없습니다")


async def follow_request_not_found_handler(
    request: Request, exc: FollowRequestNotFound
) -> JSONResponse:
    return _error_response(404, "FOLLOW_REQUEST_NOT_FOUND", "팔로우 요청을 찾을 수 없습니다")


async def password_change_not_allowed_handler(
    request: Request, exc: PasswordChangeNotAllowed
) -> JSONResponse:
    logger.info("password_change_not_allowed", user_id=exc.user_id, path=request.url.path)
    return _error_response(
        400,
        "PASSWORD_CHANGE_NOT_ALLOWED",
        "소셜 로그인 계정은 비밀번호를 변경할 수 없습니다.",
    )


async def current_password_mismatch_handler(
    request: Request, exc: CurrentPasswordMismatch
) -> JSONResponse:
    logger.info("current_password_mismatch", user_id=exc.user_id, path=request.url.path)
    return _error_response(
        400,
        "CURRENT_PASSWORD_MISMATCH",
        "현재 비밀번호가 일치하지 않습니다.",
    )


async def password_policy_error_handler(
    request: Request, exc: PasswordPolicyError
) -> JSONResponse:
    logger.info("password_policy_error", path=request.url.path)
    return _error_response(
        400,
        "PASSWORD_POLICY_VIOLATION",
        "; ".join(exc.reasons),
        reasons=exc.reasons,
    )


def register_exception_handlers(app: FastAPI) -> None:
    # User 도메인
    app.add_exception_handler(EmailAlreadyExists, email_already_exists_handler)
    app.add_exception_handler(NicknameAlreadyExists, nickname_already_exists_handler)
    app.add_exception_handler(OAuthIdentityAlreadyExists, oauth_identity_already_exists_handler)
    app.add_exception_handler(UserNotFound, user_not_found_handler)
    app.add_exception_handler(UserAlreadyDeleted, user_already_deleted_handler)
    # ⭐ Day 14
    app.add_exception_handler(UserProfilePrivate, user_profile_private_handler)
    app.add_exception_handler(UserUpdateForbidden, user_update_forbidden_handler)
    app.add_exception_handler(PasswordChangeNotAllowed, password_change_not_allowed_handler)
    app.add_exception_handler(CurrentPasswordMismatch, current_password_mismatch_handler)
    app.add_exception_handler(PasswordPolicyError, password_policy_error_handler)

    # Auth 도메인 (Day 11)
    app.add_exception_handler(UserNotFoundForAuth, user_not_found_for_auth_handler)
    app.add_exception_handler(RefreshTokenNotFound, refresh_token_not_found_handler)
    app.add_exception_handler(RefreshTokenRevoked, refresh_token_revoked_handler)
    app.add_exception_handler(InvalidCredentials, invalid_credentials_handler)
    app.add_exception_handler(EmailAlreadyRegistered, email_already_registered_handler)
    app.add_exception_handler(NicknameAlreadyTaken, nickname_already_taken_handler)
    app.add_exception_handler(LocalLoginNotAvailable, local_login_not_available_handler)
    app.add_exception_handler(EmailNotVerified, email_not_verified_handler)
    app.add_exception_handler(AccountBanned, account_banned_handler)
    app.add_exception_handler(CannotBanSelf, cannot_ban_self_handler)
    app.add_exception_handler(ClimbingLogNotFound, climbing_log_not_found_handler)
    app.add_exception_handler(ClimbingLogForbidden, climbing_log_forbidden_handler)
    app.add_exception_handler(LikeTargetNotFound, like_target_not_found_handler)
    app.add_exception_handler(CommentTargetNotFound, comment_target_not_found_handler)
    app.add_exception_handler(CommentNotFound, comment_not_found_handler)
    app.add_exception_handler(CommentForbidden, comment_forbidden_handler)
    app.add_exception_handler(CommentParentInvalid, comment_parent_invalid_handler)
    app.add_exception_handler(CommentLikeTargetNotFound, comment_like_target_not_found_handler)
    app.add_exception_handler(CannotFollowSelf, cannot_follow_self_handler)
    app.add_exception_handler(FollowTargetNotFound, follow_target_not_found_handler)
    app.add_exception_handler(FollowRequestNotFound, follow_request_not_found_handler)
    app.add_exception_handler(MediaError, media_error_handler)

    # Grade 도메인 (구현 5)
    app.add_exception_handler(GymGradeSystemNotFound, gym_grade_system_not_found_handler)
    app.add_exception_handler(GymGradeSystemAlreadyExists, gym_grade_system_already_exists_handler)
    app.add_exception_handler(GymGradeSystemNotFoundById, gym_grade_system_not_found_by_id_handler)
    app.add_exception_handler(GymGradeSystemForbidden, gym_grade_system_forbidden_handler)
    app.add_exception_handler(InvalidRankingPeriod, invalid_ranking_period_handler)

    # Kakao OAuth (Day 12)
    app.add_exception_handler(OAuthStateInvalid, oauth_state_invalid_handler)
    app.add_exception_handler(KakaoTokenExchangeFailed, kakao_token_exchange_failed_handler)
    app.add_exception_handler(KakaoUserInfoFailed, kakao_user_info_failed_handler)
    app.add_exception_handler(KakaoEmailNotAvailable, kakao_email_not_available_handler)
    app.add_exception_handler(KakaoAPIError, kakao_api_error_handler)

    # Rate Limit (Day 14 ⭐)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
