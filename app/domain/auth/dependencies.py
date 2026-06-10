"""Auth 의 FastAPI 의존성 주입.

Day 11B: in-memory → Redis 로 변경.
- get_refresh_token_repository 를 Redis 기반으로
- get_auth_service 는 변경 없음 (Service 가 Repository 인터페이스에 의존)
"""

from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis

from app.domain.auth.repository import RedisRefreshTokenRepository
from app.domain.auth.service import AuthService
from app.domain.users.dependencies import get_user_repository
from app.domain.users.repository import UserRepository
from app.infra.redis import get_redis


def get_redis_client() -> Redis:
    """Redis 클라이언트 의존성."""
    return get_redis()


def get_refresh_token_repository(
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> RedisRefreshTokenRepository:
    """Redis 기반 refresh token Repository."""
    return RedisRefreshTokenRepository(redis)


def get_auth_service(
    refresh_repo: Annotated[
        RedisRefreshTokenRepository, Depends(get_refresh_token_repository)
    ],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
) -> AuthService:
    return AuthService(refresh_repo=refresh_repo, user_repo=user_repo)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
