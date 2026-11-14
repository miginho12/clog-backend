"""토큰 페어 발급 — 로컬·카카오(웹/앱) 로그인과 refresh 회전이 함께 쓰는 단일 경로.

발급 규칙(refresh 기록 필드, 만료 시각 계산)이 바뀌면 여기만 고친다.
예전엔 AuthService._issue_token_pair 와 KakaoOAuthService._issue_tokens 에 같은 코드가 따로 있었다.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.config import get_settings
from app.core.security import TokenPair, create_token_pair
from app.domain.auth.repository import RedisRefreshTokenRepository


async def issue_token_pair(
    refresh_repo: RedisRefreshTokenRepository, user_id: UUID
) -> TokenPair:
    """access/refresh 페어 생성 + refresh 를 Redis 에 저장(만료 = 지금 + refresh 유효일)."""
    pair, refresh_jti = create_token_pair(user_id)
    expires_at = datetime.now(UTC) + timedelta(
        days=get_settings().jwt_refresh_token_expire_days
    )
    await refresh_repo.save(
        jti=refresh_jti,
        user_id=str(user_id),
        expires_at=expires_at,
    )
    return pair
