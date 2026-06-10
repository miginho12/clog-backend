"""Auth Service.

비즈니스 로직:
- 로그인 → 토큰 페어 발급 + refresh 저장
- 토큰 갱신 → refresh 검증 + 새 access 발급
- 로그아웃 → refresh 무효화

Day 11A: in-memory Repository 사용
Day 11B: Redis Repository 사용 (타입만 변경, 로직 동일) ⭐
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import (
    InvalidToken,
    TokenExpired,
    TokenPair,
    WrongTokenType,
    create_access_token,
    create_token_pair,
    decode_refresh_token,
)
from app.domain.auth.exceptions import (
    InvalidCredentials,
    RefreshTokenNotFound,
    UserNotFoundForAuth,
)
from app.domain.auth.repository import RedisRefreshTokenRepository
from app.domain.users.repository import UserRepository

logger = get_logger(__name__)


class AuthService:
    """인증 도메인 서비스."""

    def __init__(
        self,
        refresh_repo: RedisRefreshTokenRepository,
        user_repo: UserRepository,
    ):
        self.refresh_repo = refresh_repo
        self.user_repo = user_repo

    # ── 로그인 ──

    async def login(self, user_id: UUID) -> TokenPair:
        user = await self.user_repo.get_by_id_active(user_id)
        if user is None:
            logger.warning("login_failed_user_not_found", user_id=str(user_id))
            raise UserNotFoundForAuth(f"user not found: {user_id}")

        pair, refresh_jti = create_token_pair(user.id)

        settings = get_settings()
        expires_at = datetime.now(UTC) + timedelta(
            days=settings.jwt_refresh_token_expire_days
        )
        await self.refresh_repo.save(
            jti=refresh_jti,
            user_id=str(user.id),
            expires_at=expires_at,
        )

        logger.info("user_login_success", user_id=str(user.id))
        return pair

    # ── 토큰 갱신 ──

    async def refresh_access_token(self, refresh_token: str) -> str:
        try:
            payload = decode_refresh_token(refresh_token)
        except TokenExpired as e:
            logger.info("refresh_token_expired")
            raise InvalidCredentials("refresh token expired") from e
        except (InvalidToken, WrongTokenType) as e:
            logger.warning("refresh_token_invalid", error=str(e))
            raise InvalidCredentials("invalid refresh token") from e

        entry = await self.refresh_repo.get(payload.jti)
        if entry is None:
            logger.warning("refresh_token_not_in_repo", jti=payload.jti)
            raise RefreshTokenNotFound("refresh token not found or revoked")

        if entry.user_id != payload.sub:
            logger.error(
                "refresh_token_user_mismatch",
                payload_sub=payload.sub,
                stored_user=entry.user_id,
            )
            raise InvalidCredentials("token user mismatch")

        new_access = create_access_token(payload.sub)
        logger.info("access_token_refreshed", user_id=payload.sub)
        return new_access

    # ── 로그아웃 ──

    async def logout(self, refresh_token: str) -> None:
        try:
            payload = decode_refresh_token(refresh_token)
        except (TokenExpired, InvalidToken, WrongTokenType) as e:
            logger.info("logout_with_invalid_token", error=str(e))
            return

        revoked = await self.refresh_repo.revoke(payload.jti)
        if revoked:
            logger.info("user_logout", user_id=payload.sub)

    # ── 전체 로그아웃 ──

    async def logout_all(self, user_id: UUID) -> int:
        count = await self.refresh_repo.revoke_all_for_user(str(user_id))
        logger.info("user_logout_all_devices", user_id=str(user_id), count=count)
        return count
