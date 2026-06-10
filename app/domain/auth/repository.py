"""Refresh Token Repository (Redis 구현).

⚠️ Day 11A: in-memory dict 임시 구현 (단일 Pod 제약)
⚠️ Day 11B: Redis 구현으로 교체 ⭐ 현재

[Redis 키 설계]
1. refresh_token:{jti}
   - value: JSON {"user_id": "...", "revoked": false}
   - TTL: refresh token 수명 (7일)
   - 만료 자동 정리 (Redis TTL)

2. user_tokens:{user_id}
   - value: Set([jti1, jti2, ...])
   - TTL: 8일 (refresh 보다 살짝 길게)
   - 멀티 디바이스 로그아웃 시 사용

[멀티 Pod 동기화]
모든 clog-backend Pod 가 같은 Redis 봄
→ 어느 Pod 가 받든 일관된 토큰 상태
→ 진호님이 겪은 *"멀티세션 문제"* 해결
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis

from app.core.logging import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────
#  키 패턴 (한 곳에서 관리)
# ─────────────────────────────────────────


def _refresh_token_key(jti: str) -> str:
    """Refresh token 의 Redis key."""
    return f"refresh_token:{jti}"


def _user_tokens_key(user_id: str) -> str:
    """특정 사용자의 모든 refresh token jti 집합."""
    return f"user_tokens:{user_id}"


# ─────────────────────────────────────────
#  데이터 클래스 (in-memory 와 호환)
# ─────────────────────────────────────────


@dataclass
class RefreshTokenEntry:
    """저장된 refresh token 정보 (Day 11A 와 동일 인터페이스)."""

    user_id: str
    expires_at: datetime
    revoked: bool = False


# ─────────────────────────────────────────
#  Redis Repository
# ─────────────────────────────────────────


class RedisRefreshTokenRepository:
    """Refresh token 의 Redis 저장소.

    인터페이스는 Day 11A 의 InMemoryRefreshTokenRepository 와 동일.
    Service 코드 변경 X.
    """

    def __init__(self, redis: Redis):
        self.redis = redis

    async def save(
        self, *, jti: str, user_id: str, expires_at: datetime
    ) -> None:
        """Refresh token 저장.

        - JSON 으로 직렬화
        - TTL 자동 설정 (만료 자동 정리)
        - user_tokens:{user_id} 에도 jti 추가 (멀티 디바이스 추적)
        """
        token_key = _refresh_token_key(jti)
        user_key = _user_tokens_key(user_id)

        # TTL 계산 (초)
        now = datetime.now(UTC)
        ttl_seconds = int((expires_at - now).total_seconds())
        if ttl_seconds <= 0:
            logger.warning("save_attempt_already_expired", jti=jti)
            return

        # JSON 데이터
        data = json.dumps(
            {
                "user_id": user_id,
                "revoked": False,
                "expires_at": expires_at.isoformat(),
            }
        )

        # Pipeline 으로 두 명령 atomically
        async with self.redis.pipeline(transaction=True) as pipe:
            # 1. refresh token 저장 (TTL 포함)
            await pipe.set(token_key, data, ex=ttl_seconds)
            # 2. 사용자의 토큰 집합에 추가 (TTL 살짝 길게)
            await pipe.sadd(user_key, jti)
            await pipe.expire(user_key, ttl_seconds + 86400)  # +1일
            await pipe.execute()

        logger.debug("refresh_token_saved", jti=jti, user_id=user_id, ttl=ttl_seconds)

    async def get(self, jti: str) -> RefreshTokenEntry | None:
        """Refresh token 조회.

        Redis 의 TTL 로 만료된 키는 이미 없음.
        revoked 상태 확인.
        """
        token_key = _refresh_token_key(jti)
        raw = await self.redis.get(token_key)
        if raw is None:
            # 만료됐거나 (Redis TTL), 존재한 적 없음, 또는 delete 됨
            return None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("refresh_token_json_error", jti=jti, error=str(e))
            return None

        # revoked 상태면 None 반환 (없는 셈)
        if data.get("revoked", False):
            return None

        return RefreshTokenEntry(
            user_id=data["user_id"],
            expires_at=datetime.fromisoformat(data["expires_at"]),
            revoked=False,
        )

    async def revoke(self, jti: str) -> bool:
        """Refresh token 무효화.

        Delete 대신 revoked=True 로 표시.
        이유: 같은 jti 가 재사용되는 경우 감지 가능 (보안).
        """
        token_key = _refresh_token_key(jti)
        raw = await self.redis.get(token_key)
        if raw is None:
            return False

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return False

        if data.get("revoked", False):
            # 이미 무효화됨
            return True

        # revoked 표시 (TTL 유지)
        ttl = await self.redis.ttl(token_key)
        data["revoked"] = True
        new_data = json.dumps(data)

        if ttl > 0:
            await self.redis.set(token_key, new_data, ex=ttl)
        else:
            await self.redis.set(token_key, new_data)

        logger.debug("refresh_token_revoked", jti=jti)
        return True

    async def revoke_all_for_user(self, user_id: str) -> int:
        """특정 사용자의 모든 refresh token 무효화.

        멀티 디바이스 로그아웃 / 비밀번호 변경 시 사용.
        ⭐ 진호님이 겪은 멀티세션 문제의 해법.
        """
        user_key = _user_tokens_key(user_id)
        # 사용자의 모든 jti 가져오기
        jtis = await self.redis.smembers(user_key)
        if not jtis:
            logger.info("revoke_all_no_tokens", user_id=user_id)
            return 0

        count = 0
        for jti in jtis:
            if await self.revoke(jti):
                count += 1

        logger.info("all_refresh_tokens_revoked", user_id=user_id, count=count)
        return count

    async def delete(self, jti: str) -> None:
        """완전 삭제 (revoke 와 달리 흔적 X)."""
        token_key = _refresh_token_key(jti)
        await self.redis.delete(token_key)

    async def count_active(self) -> int:
        """디버깅: 현재 활성 refresh token 수 (대략).

        SCAN 사용 (운영 안전).
        """
        count = 0
        async for _ in self.redis.scan_iter("refresh_token:*"):
            count += 1
        return count
