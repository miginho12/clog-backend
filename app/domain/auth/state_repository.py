"""OAuth state Repository.

CSRF 방어용 state 파라미터를 Redis 에 임시 저장.

[흐름]
1. /auth/kakao/login → 랜덤 state 생성 → Redis 저장 (5분 TTL)
2. 사용자가 카카오 로그인
3. /auth/kakao/callback?code=X&state=Y → state 검증 → 즉시 삭제

[키 설계]
oauth_state:{state}
  value: JSON {"client": "web"|"app", "code_challenge": str|null}
         (옛 값 "1" 은 web 으로 해석 — 배포 중 발급된 state 호환)
  TTL: 5분 (카카오 인증 flow 충분히 완료할 시간)

[앱 로그인 (2026-11-10, 감사 M14)]
앱은 시스템 브라우저로 로그인하고 커스텀 스킴으로 돌아온다. 이때 토큰 대신
일회용 코드를 넘기고(app_code_repository), PKCE code_challenge 를 state 에 묶어 둔다.

[Redis 의 이점]
- 자동 만료 (5분 후 자동 삭제)
- 멀티 Pod 환경에서 일관된 검증
- 사용한 state 즉시 제거 (재사용 방지)
"""

import json
import secrets
from dataclasses import dataclass
from typing import Literal

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _state_key(state: str) -> str:
    """Redis 키 패턴."""
    return f"oauth_state:{state}"


OAuthClient = Literal["web", "app"]


@dataclass(frozen=True)
class OAuthState:
    """state 에 묶인 로그인 요청 정보."""

    client: OAuthClient = "web"
    code_challenge: str | None = None  # client=app 일 때만 (PKCE S256)


class OAuthStateRepository:
    """OAuth state 의 Redis 저장소."""

    def __init__(self, redis: Redis):
        self.redis = redis
        self.settings = get_settings()

    async def create(self, meta: OAuthState | None = None) -> str:
        """랜덤 state 생성 + Redis 저장.

        Args:
            meta: 콜백에서 다시 꺼낼 로그인 요청 정보 (없으면 web)

        Returns:
            생성된 state (URL-safe 문자열)
        """
        # 32 bytes → URL-safe base64 (약 43자)
        state = secrets.token_urlsafe(32)
        ttl = self.settings.oauth_state_ttl_seconds
        meta = meta or OAuthState()
        value = json.dumps({"client": meta.client, "code_challenge": meta.code_challenge})

        await self.redis.set(_state_key(state), value, ex=ttl)
        logger.debug(
            "oauth_state_created", state_prefix=state[:10], ttl=ttl, client=meta.client
        )
        return state

    async def consume(self, state: str) -> OAuthState | None:
        """state 검증 + 즉시 삭제 (원자적).

        Args:
            state: 콜백에서 받은 state 값

        Returns:
            OAuthState: 유효함 (방금 삭제됨)
            None: 없거나 만료됨 (의심스러움)
        """
        if not state:
            return None

        # GETDEL: 읽기와 삭제를 한 번에 — 같은 state 로 두 콜백이 동시에 와도 하나만 성공
        raw = await self.redis.getdel(_state_key(state))
        if raw is None:
            logger.warning("oauth_state_invalid", state_prefix=state[:10])
            return None

        logger.debug("oauth_state_consumed", state_prefix=state[:10])
        return _parse_state(raw)


def _parse_state(raw: str | bytes) -> OAuthState:
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        data = json.loads(raw)
    except ValueError:
        return OAuthState()  # 옛 형식 "1"
    if not isinstance(data, dict) or data.get("client") != "app":
        return OAuthState()
    return OAuthState(client="app", code_challenge=data.get("code_challenge"))
