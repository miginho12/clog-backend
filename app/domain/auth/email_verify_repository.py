"""이메일 인증 토큰 Repository (Redis).

자체 회원가입 시 인증 토큰을 Redis 에 저장.
- 가입 → 랜덤 토큰 생성 → Redis 저장 (24h TTL) → 메일 발송
- 인증 링크 클릭 → 토큰으로 user_id 조회 + 즉시 삭제 (일회용)

[키 설계]
email_verify:{token}
  value: user_id (문자열)
  TTL: 24시간 (config.email_verify_ttl_seconds)

state_repository 패턴을 따름 (자동 만료, 원자적 소비).
"""
import secrets
from uuid import UUID

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _verify_key(token: str) -> str:
    return f"email_verify:{token}"


class EmailVerifyRepository:
    """이메일 인증 토큰의 Redis 저장소."""

    def __init__(self, redis: Redis):
        self.redis = redis
        self.settings = get_settings()

    async def create(self, user_id: UUID) -> str:
        """인증 토큰 생성 + Redis 저장 (user_id 매핑).

        Returns:
            생성된 토큰 (URL-safe)
        """
        token = secrets.token_urlsafe(32)
        ttl = self.settings.email_verify_ttl_seconds
        await self.redis.set(_verify_key(token), str(user_id), ex=ttl)
        logger.debug(
            "email_verify_token_created",
            user_id=str(user_id),
            token_prefix=token[:10],
            ttl=ttl,
        )
        return token

    async def consume(self, token: str) -> UUID | None:
        """토큰 검증 + user_id 반환 + 즉시 삭제 (일회용).

        Returns:
            user_id: 유효한 토큰이면 매핑된 user_id
            None: 없거나 만료된 토큰
        """
        if not token:
            return None
        key = _verify_key(token)
        # GET + DEL 을 원자적으로 (race condition 방지)
        user_id_str = await self.redis.get(key)
        if user_id_str is None:
            logger.warning("email_verify_token_invalid", token_prefix=token[:10])
            return None
        await self.redis.delete(key)
        if isinstance(user_id_str, bytes):
            user_id_str = user_id_str.decode()
        logger.debug("email_verify_token_consumed", token_prefix=token[:10])
        return UUID(user_id_str)
