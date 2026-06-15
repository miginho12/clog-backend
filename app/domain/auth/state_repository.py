"""OAuth state Repository.

CSRF 방어용 state 파라미터를 Redis 에 임시 저장.

[흐름]
1. /auth/kakao/login → 랜덤 state 생성 → Redis 저장 (5분 TTL)
2. 사용자가 카카오 로그인
3. /auth/kakao/callback?code=X&state=Y → state 검증 → 즉시 삭제

[키 설계]
oauth_state:{state}
  value: "1" (단순 마커, TTL 만으로 충분)
  TTL: 5분 (카카오 인증 flow 충분히 완료할 시간)

[Redis 의 이점]
- 자동 만료 (5분 후 자동 삭제)
- 멀티 Pod 환경에서 일관된 검증
- 사용한 state 즉시 제거 (재사용 방지)
"""

import secrets

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _state_key(state: str) -> str:
    """Redis 키 패턴."""
    return f"oauth_state:{state}"


class OAuthStateRepository:
    """OAuth state 의 Redis 저장소."""

    def __init__(self, redis: Redis):
        self.redis = redis
        self.settings = get_settings()

    async def create(self) -> str:
        """랜덤 state 생성 + Redis 저장.

        Returns:
            생성된 state (URL-safe 문자열)
        """
        # 32 bytes → URL-safe base64 (약 43자)
        state = secrets.token_urlsafe(32)
        ttl = self.settings.oauth_state_ttl_seconds

        await self.redis.set(_state_key(state), "1", ex=ttl)
        logger.debug("oauth_state_created", state_prefix=state[:10], ttl=ttl)
        return state

    async def consume(self, state: str) -> bool:
        """state 검증 + 즉시 삭제 (원자적).

        Args:
            state: 콜백에서 받은 state 값

        Returns:
            True: 유효함 (방금 삭제됨)
            False: 없거나 만료됨 (의심스러움)
        """
        if not state:
            return False

        key = _state_key(state)
        # DEL 이 삭제 개수 반환 (있으면 1, 없으면 0)
        # 원자적 — race condition X
        deleted = await self.redis.delete(key)
        is_valid = deleted > 0

        if is_valid:
            logger.debug("oauth_state_consumed", state_prefix=state[:10])
        else:
            logger.warning(
                "oauth_state_invalid",
                state_prefix=state[:10] if state else "(empty)",
            )

        return is_valid
