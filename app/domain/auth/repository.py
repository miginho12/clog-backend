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


@dataclass
class RefreshTokenRecord:
    """폐기 여부·사유까지 포함한 저장 상태 (회전 재사용 감지용, 감사 M2).

    revoked_reason: rotated(회전으로 교체) | logout | reuse_detected | None(옛 데이터)
    """

    user_id: str
    expires_at: datetime
    revoked: bool
    revoked_reason: str | None = None
    revoked_at: datetime | None = None


# 아직 폐기되지 않은 토큰만 rotated 로 표시하고 1 을, 이미 폐기/없음이면 0 을 돌려준다.
# GET→판정→SET 을 한 번에 해야 같은 refresh 로 동시에 온 두 요청이 둘 다 회전에 성공해
# 토큰 계열이 둘로 갈라지는 일을 막을 수 있다 (Redis 는 스크립트를 원자적으로 실행).
_MARK_ROTATED_LUA = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local d = cjson.decode(raw)
if d['revoked'] then return 0 end
d['revoked'] = true
d['revoked_reason'] = 'rotated'
d['revoked_at'] = ARGV[1]
local ttl = redis.call('TTL', KEYS[1])
if ttl > 0 then
  redis.call('SET', KEYS[1], cjson.encode(d), 'EX', ttl)
else
  redis.call('SET', KEYS[1], cjson.encode(d))
end
return 1
"""


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
            await pipe.sadd(user_key, jti)  # type: ignore[misc]  # redis-py 파이프라인 스텁 미비
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

    async def get_record(self, jti: str) -> RefreshTokenRecord | None:
        """폐기된 토큰도 사유와 함께 돌려준다 (없거나 TTL 만료면 None).

        get() 은 폐기 토큰을 "없음"으로 숨기지만, 회전 재사용 감지는 "회전으로 폐기된
        토큰이 다시 왔다"를 구분해야 한다.
        """
        raw = await self.redis.get(_refresh_token_key(jti))
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("refresh_token_json_error", jti=jti, error=str(e))
            return None
        revoked_at = data.get("revoked_at")
        return RefreshTokenRecord(
            user_id=data["user_id"],
            expires_at=datetime.fromisoformat(data["expires_at"]),
            revoked=bool(data.get("revoked", False)),
            revoked_reason=data.get("revoked_reason"),
            revoked_at=datetime.fromisoformat(revoked_at) if revoked_at else None,
        )

    async def mark_rotated(self, jti: str) -> bool:
        """회전으로 교체됨 표시 — 아직 유효했던 경우에만 True (원자적, _MARK_ROTATED_LUA)."""
        result = await self.redis.eval(  # type: ignore[misc]  # redis-py 스텁 미비
            _MARK_ROTATED_LUA, 1, _refresh_token_key(jti), datetime.now(UTC).isoformat()
        )
        return bool(result)

    async def revoke(self, jti: str, reason: str = "logout") -> bool:
        """Refresh token 무효화.

        Delete 대신 revoked=True 로 표시.
        이유: 같은 jti 가 재사용되는 경우 감지 가능 (보안).
        reason·revoked_at 을 남겨 회전 폐기(rotated)와 구분한다 (감사 M2).
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
        data["revoked_reason"] = reason
        data["revoked_at"] = datetime.now(UTC).isoformat()
        new_data = json.dumps(data)

        if ttl > 0:
            await self.redis.set(token_key, new_data, ex=ttl)
        else:
            await self.redis.set(token_key, new_data)

        logger.debug("refresh_token_revoked", jti=jti)
        return True

    async def revoke_all_for_user(self, user_id: str, reason: str = "logout") -> int:
        """특정 사용자의 모든 refresh token 무효화.

        멀티 디바이스 로그아웃 / 비밀번호 변경 시 사용.
        ⭐ 진호님이 겪은 멀티세션 문제의 해법.
        회전 재사용 감지 시 reason="reuse_detected" (감사 M2).
        """
        user_key = _user_tokens_key(user_id)
        # 사용자의 모든 jti 가져오기
        jtis = await self.redis.smembers(user_key)  # type: ignore[misc]  # redis-py 스텁 미비
        if not jtis:
            logger.info("revoke_all_no_tokens", user_id=user_id)
            return 0

        count = 0
        for jti in jtis:
            if await self.revoke(jti, reason=reason):
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
