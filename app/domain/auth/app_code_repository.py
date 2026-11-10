"""앱 로그인 일회용 코드 Redis Repository (2026-11-10, 감사 M14).

[왜 필요한가]
앱은 카카오 로그인을 시스템 브라우저(Custom Tabs)에서 하고, 백엔드 콜백이
커스텀 스킴(com.climblog.app://auth/callback)으로 앱에 돌려보낸다.
커스텀 스킴은 다른 앱이 같은 스킴을 등록해 가로챌 수 있으므로 URL 에 토큰을
싣지 않고, 짧게 살아 있는 일회용 코드만 싣는다. 코드는 PKCE(S256) 로 로그인을
시작한 앱에 묶여 있어 code_verifier 없이는 토큰으로 바꿀 수 없다 (RFC 7636·8252).

[키 설계]
oauth_app_code:{code}
  value: JSON {"user_id", "is_new", "code_challenge"}
  TTL: oauth_app_code_ttl_seconds (기본 60초)
토큰은 교환 시점에 발급한다 — 교환되지 않은 코드가 refresh 토큰을 남기지 않게.
"""

import json
import secrets
from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _code_key(code: str) -> str:
    return f"oauth_app_code:{code}"


@dataclass(frozen=True)
class AppLoginCode:
    user_id: str
    is_new: bool
    code_challenge: str


class AppLoginCodeRepository:
    """앱 로그인 일회용 코드의 Redis 저장소."""

    def __init__(self, redis: Redis):
        self.redis = redis
        self.settings = get_settings()

    async def create(self, user_id: str, is_new: bool, code_challenge: str) -> str:
        code = secrets.token_urlsafe(32)
        ttl = self.settings.oauth_app_code_ttl_seconds
        value = json.dumps(
            {"user_id": user_id, "is_new": is_new, "code_challenge": code_challenge}
        )
        await self.redis.set(_code_key(code), value, ex=ttl)
        logger.debug("oauth_app_code_created", code_prefix=code[:6], ttl=ttl)
        return code

    async def consume(self, code: str) -> AppLoginCode | None:
        """코드 조회 + 즉시 삭제 (GETDEL, 원자적). 없거나 만료면 None.

        verifier 검증 전에 지운다 — 코드를 가로챈 쪽이 verifier 를 맞혀 볼
        기회를 한 번으로 제한한다 (틀리면 코드는 이미 사라짐).
        """
        if not code:
            return None
        raw = await self.redis.getdel(_code_key(code))
        if raw is None:
            return None
        data = json.loads(raw)
        return AppLoginCode(
            user_id=data["user_id"],
            is_new=bool(data["is_new"]),
            code_challenge=data["code_challenge"],
        )
