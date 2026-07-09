"""전역 API 의존성.

가장 중요한 것: get_current_user
HTTP 요청 → Authorization 헤더 → JWT 검증 → User 객체 반환

사용:
    @router.get("/me")
    async def my_profile(user: CurrentUserDep):
        return user
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.logging import get_logger
from app.core.security import (
    InvalidToken,
    TokenExpired,
    WrongTokenType,
    decode_access_token,
)
from app.domain.users.dependencies import get_user_repository
from app.domain.users.models import User
from app.domain.users.repository import UserRepository

logger = get_logger(__name__)

# Bearer 스킴 (자동으로 Authorization 헤더 파싱)
# auto_error=False: 토큰 없을 때 FastAPI 가 자동 401 안 보냄 (우리가 직접)
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
) -> User:
    """인증된 사용자 반환.

    검증 순서:
    1. Authorization 헤더 존재 → 401 if 없음
    2. JWT 서명 + 만료 → 401 if 실패
    3. 토큰 종류 = access → 401 if refresh
    4. user_id → User 조회 → 401 if 없음 (삭제됨)

    Raises:
        HTTPException(401): 위 어떤 단계든 실패 시
    """
    # 1. 헤더 존재 확인
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # 2-3. JWT 디코드 (서명 + 만료 + 토큰 종류 검증)
    try:
        payload = decode_access_token(token)
    except TokenExpired:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token expired",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from None
    except (InvalidToken, WrongTokenType) as e:
        logger.warning("auth_failed_invalid_token", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    # 4. User 조회
    try:
        user_id = UUID(payload.sub)
    except ValueError:
        logger.warning("auth_failed_invalid_sub", sub=payload.sub)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token subject",
        ) from None

    user = await user_repo.get_by_id_active(user_id)
    if user is None:
        # 토큰은 유효한데 사용자가 삭제됨
        logger.warning("auth_failed_user_not_found", user_id=str(user_id))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user not found",
        )

    # 차단된 사용자 → 모든 인증 요청 거부 (활동 차단)
    if user.is_banned:
        logger.warning("auth_blocked_banned_user", user_id=str(user_id))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="account is banned",
        )

    return user


# 짧은 사용을 위한 타입 alias
CurrentUserDep = Annotated[User, Depends(get_current_user)]


# ─────────────────────────────────────────
#  선택적 인증 (비로그인 허용 조회용)
# ─────────────────────────────────────────

# auto_error=False: 토큰 없어도 통과 (None 반환)
_optional_bearer = HTTPBearer(auto_error=False)


async def get_optional_user_id(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_optional_bearer)
    ],
) -> UUID | None:
    """선택적 인증: 유효한 토큰이면 user_id, 아니면 None.

    피드/상세/댓글 조회에서 사용 — 비로그인도 허용하되, 로그인하면
    본인의 private 글까지 볼 수 있도록 viewer_id 를 넘긴다.
    """
    if credentials is None:
        return None
    try:
        payload = decode_access_token(credentials.credentials)
        return UUID(payload.sub)
    except (TokenExpired, InvalidToken, WrongTokenType, ValueError):
        # 토큰이 있지만 유효하지 않으면 비로그인 취급
        return None


# 짧은 사용을 위한 타입 alias
OptionalUserId = Annotated[UUID | None, Depends(get_optional_user_id)]


# ─────────────────────────────────────────
#  Admin 가드 (Day 17 ⭐)
# ─────────────────────────────────────────


async def require_admin(user: CurrentUserDep) -> User:
    """관리자 권한 필수.

    get_current_user 로 인증을 먼저 통과한 뒤, is_admin 을 확인.
    일반 사용자가 admin 전용 엔드포인트 접근 시 403.

    사용:
        @router.get("/admin/users")
        async def list_all(admin: AdminUserDep):
            ...

    Raises:
        HTTPException(403): is_admin=False 인 경우
    """
    if not user.is_admin:
        logger.warning("admin_access_denied", user_id=str(user.id))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin privileges required",
        )
    return user


# 짧은 사용을 위한 타입 alias
AdminUserDep = Annotated[User, Depends(require_admin)]
