"""비밀번호 찾기(재설정) Redis Repository.

email_verify_repository.py 와 같은 원칙 — Redis 를 일회용 상태 저장소로 사용.
2단계 흐름:
1. 이메일 요청 → 6자리 코드 생성 → Redis 저장(3분 TTL) → 메일 발송
2. 코드 확인 → 맞으면 코드 소비(삭제) + 재설정 토큰 발급(10분 TTL)
3. 재설정 토큰 + 새 비밀번호 → 토큰 소비(삭제) + 비밀번호 변경

[키 설계]
pwreset_code:{email}    value: 6자리 코드 문자열     TTL: password_reset_code_ttl_seconds
pwreset_token:{token}   value: email                TTL: password_reset_token_ttl_seconds
"""
import secrets

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _code_key(email: str) -> str:
    return f"pwreset_code:{email}"


def _token_key(token: str) -> str:
    return f"pwreset_token:{token}"


class PasswordResetRepository:
    """비밀번호 재설정 코드/토큰의 Redis 저장소."""

    def __init__(self, redis: Redis):
        self.redis = redis
        self.settings = get_settings()

    async def create_code(self, email: str) -> str:
        """6자리 코드 생성 + Redis 저장 (이메일당 1개, 재요청 시 덮어씀)."""
        code = f"{secrets.randbelow(1_000_000):06d}"
        ttl = self.settings.password_reset_code_ttl_seconds
        await self.redis.set(_code_key(email), code, ex=ttl)
        logger.debug("password_reset_code_created", email=email, ttl=ttl)
        return code

    async def consume_code(self, email: str, code: str) -> bool:
        """코드 일치 여부 확인.

        일치하면 즉시 삭제(일회용, 재사용 방지). 불일치면 만료 전까지
        재시도할 수 있도록 그대로 둔다(입력 실수 허용).
        """
        stored = await self.redis.get(_code_key(email))
        if stored is None or stored != code:
            logger.warning("password_reset_code_mismatch", email=email)
            return False
        await self.redis.delete(_code_key(email))
        logger.debug("password_reset_code_consumed", email=email)
        return True

    async def create_reset_token(self, email: str) -> str:
        """코드 확인 완료 후, 새 비밀번호 설정 단계에서 쓸 1회용 토큰 발급."""
        token = secrets.token_urlsafe(32)
        ttl = self.settings.password_reset_token_ttl_seconds
        await self.redis.set(_token_key(token), email, ex=ttl)
        logger.debug(
            "password_reset_token_created",
            email=email,
            token_prefix=token[:10],
            ttl=ttl,
        )
        return token

    async def consume_reset_token(self, token: str) -> str | None:
        """토큰 검증 + email 반환 + 즉시 삭제 (일회용).

        Returns:
            email: 유효한 토큰이면 매핑된 이메일
            None: 없거나 만료된 토큰
        """
        if not token:
            return None
        key = _token_key(token)
        email = await self.redis.get(key)
        if email is None:
            logger.warning("password_reset_token_invalid", token_prefix=token[:10])
            return None
        await self.redis.delete(key)
        logger.debug("password_reset_token_consumed", token_prefix=token[:10])
        return email
