"""Auth 의 FastAPI 의존성 주입.

Day 11B + Day 12:
- RedisRefreshTokenRepository
- OAuthStateRepository (⭐ Day 12)
- KakaoOAuthClient (⭐ Day 12)
- AuthService (기존 로그인/리프레시)
- KakaoOAuthService (⭐ Day 12)
"""

from typing import Annotated

import httpx
from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.auth.email_verify_repository import EmailVerifyRepository
from app.domain.auth.kakao_service import KakaoOAuthService
from app.domain.auth.password_reset_repository import PasswordResetRepository
from app.domain.auth.repository import RedisRefreshTokenRepository
from app.domain.auth.service import AuthService
from app.domain.auth.state_repository import OAuthStateRepository
from app.domain.users.dependencies import get_user_repository
from app.domain.users.repository import UserRepository
from app.infra.db import get_session
from app.infra.kakao import KakaoOAuthClient
from app.infra.redis import get_redis

# ─────────────────────────────────────────
#  기본 클라이언트
# ─────────────────────────────────────────


def get_redis_client() -> Redis:
    return get_redis()


def get_http_client(request: Request) -> httpx.AsyncClient:
    """앱 lifespan 에서 만든 httpx 클라이언트 반환.

    main.py 의 lifespan 에서 app.state.http_client 에 저장됨.
    """
    client = getattr(request.app.state, "http_client", None)
    if client is None:
        raise RuntimeError(
            "HTTP client not initialized. Make sure lifespan sets app.state.http_client."
        )
    return client  # type: ignore[no-any-return]


# ─────────────────────────────────────────
#  Repository
# ─────────────────────────────────────────


def get_refresh_token_repository(
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> RedisRefreshTokenRepository:
    return RedisRefreshTokenRepository(redis)


def get_oauth_state_repository(
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> OAuthStateRepository:
    return OAuthStateRepository(redis)


def get_email_verify_repository(
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> EmailVerifyRepository:
    return EmailVerifyRepository(redis)


def get_password_reset_repository(
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> PasswordResetRepository:
    return PasswordResetRepository(redis)


# ─────────────────────────────────────────
#  Kakao 클라이언트
# ─────────────────────────────────────────


def get_kakao_client(
    http: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> KakaoOAuthClient:
    return KakaoOAuthClient(http)


# ─────────────────────────────────────────
#  Services
# ─────────────────────────────────────────


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    refresh_repo: Annotated[
        RedisRefreshTokenRepository, Depends(get_refresh_token_repository)
    ],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    email_verify_repo: Annotated[
        EmailVerifyRepository, Depends(get_email_verify_repository)
    ],
    password_reset_repo: Annotated[
        PasswordResetRepository, Depends(get_password_reset_repository)
    ],
) -> AuthService:
    return AuthService(
        session=session,
        refresh_repo=refresh_repo,
        user_repo=user_repo,
        email_verify_repo=email_verify_repo,
        password_reset_repo=password_reset_repo,
    )


def get_kakao_oauth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    kakao_client: Annotated[KakaoOAuthClient, Depends(get_kakao_client)],
    state_repo: Annotated[OAuthStateRepository, Depends(get_oauth_state_repository)],
    refresh_repo: Annotated[
        RedisRefreshTokenRepository, Depends(get_refresh_token_repository)
    ],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
) -> KakaoOAuthService:
    return KakaoOAuthService(
        session=session,
        kakao_client=kakao_client,
        state_repo=state_repo,
        refresh_repo=refresh_repo,
        user_repo=user_repo,
    )


# ─────────────────────────────────────────
#  타입 alias
# ─────────────────────────────────────────


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
KakaoOAuthServiceDep = Annotated[KakaoOAuthService, Depends(get_kakao_oauth_service)]
