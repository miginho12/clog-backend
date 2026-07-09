"""Auth Service.

비즈니스 로직:
- 로그인 → 토큰 페어 발급 + refresh 저장
- 토큰 갱신 → refresh 검증 + 새 access 발급
- 로그아웃 → refresh 무효화
- 자체 회원가입(signup) → 비밀번호 해싱 + User 생성 + 토큰 발급 (Day 17 ⭐)
- 자체 로그인(local_login) → 이메일/비밀번호 검증 + 토큰 발급 (Day 17 ⭐)

Day 11A: in-memory Repository 사용
Day 11B: Redis Repository 사용 (타입만 변경, 로직 동일)
Day 17: 자체 회원가입/로그인 추가 ⭐
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.email import send_verification_email
from app.core.logging import get_logger
from app.core.password import hash_password, verify_password
from app.core.security import (
    InvalidToken,
    TokenExpired,
    TokenPair,
    WrongTokenType,
    create_access_token,
    create_token_pair,
    decode_refresh_token,
)
from app.domain.auth.email_verify_repository import EmailVerifyRepository
from app.domain.auth.exceptions import (
    AccountBanned,
    EmailAlreadyRegistered,
    EmailNotVerified,
    InvalidCredentials,
    LocalLoginNotAvailable,
    NicknameAlreadyTaken,
    RefreshTokenNotFound,
    UserNotFoundForAuth,
)
from app.domain.auth.repository import RedisRefreshTokenRepository
from app.domain.users.models import User
from app.domain.users.repository import UserRepository

logger = get_logger(__name__)


class AuthService:
    """인증 도메인 서비스."""

    def __init__(
        self,
        session: AsyncSession,
        refresh_repo: RedisRefreshTokenRepository,
        user_repo: UserRepository,
        email_verify_repo: EmailVerifyRepository,
    ):
        self.session = session
        self.refresh_repo = refresh_repo
        self.user_repo = user_repo
        self.email_verify_repo = email_verify_repo

    # ─────────────────────────────────────────
    #  자체 회원가입 (Day 17 ⭐)
    # ─────────────────────────────────────────

    async def signup(
        self,
        *,
        email: str,
        password: str,
        nickname: str,
        profile_image_url: str | None = None,
    ) -> User:
        """자체 회원가입 (이메일 인증 방식).

        흐름:
        1. 이메일 중복 검사 (OAuth 가입자 포함)
        2. 닉네임 중복 검사
        3. 비밀번호 해싱 (bcrypt)
        4. User 생성 (email_verified=False → 인증 전 로그인 불가)
        5. 인증 토큰 생성 (Redis) + 인증 메일 발송
        → 토큰 발급 안 함. 사용자는 메일 인증 후 로그인.

        Raises:
            EmailAlreadyRegistered: 이메일 중복
            NicknameAlreadyTaken: 닉네임 중복
        """
        # 1. 이메일 중복 검사
        existing_email = await self.user_repo.get_by_email(email)
        if existing_email is not None:
            logger.info("signup_failed_email_exists", email=email)
            raise EmailAlreadyRegistered(email)

        # 2. 닉네임 중복 검사
        existing_nick = await self.user_repo.get_by_nickname(nickname)
        if existing_nick is not None:
            logger.info("signup_failed_nickname_taken", nickname=nickname)
            raise NicknameAlreadyTaken(nickname)

        # 3. 비밀번호 해싱
        pw_hash = hash_password(password)

        # 4. User 생성 (local 가입자, email_verified=False 기본)
        user = await self.user_repo.create(
            email=email,
            nickname=nickname,
            auth_provider="local",
            auth_provider_id=None,
            password_hash=pw_hash,
            profile_image_url=profile_image_url,
        )
        await self.session.commit()
        await self.session.refresh(user)

        # 5. 인증 토큰 생성 + 메일 발송
        token = await self.email_verify_repo.create(user.id)
        settings = get_settings()
        verify_url = f"{settings.frontend_url}/verify?token={token}"
        try:
            await send_verification_email(
                to_email=email,
                nickname=nickname,
                verify_url=verify_url,
            )
        except Exception as e:
            # 메일 발송 실패해도 가입은 유지 (재발송 엔드포인트로 커버)
            logger.error(
                "verification_email_failed",
                user_id=str(user.id),
                error=str(e),
            )

        logger.info("local_signup_success", user_id=str(user.id), email=email)
        return user

    async def verify_email(self, token: str) -> bool:
        """이메일 인증 토큰 검증 → email_verified=True.

        Returns:
            True: 인증 성공
            False: 토큰 무효/만료 (또는 이미 소비됨)
        """
        user_id = await self.email_verify_repo.consume(token)
        if user_id is None:
            return False
        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            logger.warning("verify_email_user_gone", user_id=str(user_id))
            return False
        if user.email_verified:
            # 이미 인증됨 (중복 클릭 등) — 성공으로 취급
            return True
        user.email_verified = True
        await self.session.commit()
        logger.info("email_verified", user_id=str(user_id))
        return True

    # ─────────────────────────────────────────
    #  자체 로그인 (Day 17 ⭐)
    # ─────────────────────────────────────────

    async def local_login(
        self, *, email: str, password: str
    ) -> tuple[TokenPair, User]:
        """이메일 + 비밀번호 로그인.

        보안 설계 (계정 열거 방어):
        - 이메일 없음 / OAuth 전용 계정 / 비밀번호 불일치를 모두
          동일한 LocalLoginNotAvailable 로 처리 → 공격자가 계정 존재
          여부를 알 수 없음.

        Raises:
            LocalLoginNotAvailable: 위 어떤 경우든 (단일 메시지)
        """
        user = await self.user_repo.get_by_email(email)

        # 이메일 없음 → 동일 예외 (계정 존재 숨김)
        if user is None:
            logger.info("local_login_failed_no_user", email=email)
            raise LocalLoginNotAvailable("invalid email or password")

        # OAuth 전용 계정 (password_hash 없음) → 동일 예외
        if user.password_hash is None:
            logger.info(
                "local_login_failed_oauth_only",
                email=email,
                provider=user.auth_provider,
            )
            raise LocalLoginNotAvailable("invalid email or password")

        # 비밀번호 불일치 → 동일 예외
        if not verify_password(password, user.password_hash):
            logger.info("local_login_failed_wrong_password", email=email)
            raise LocalLoginNotAvailable("invalid email or password")

        # 이메일 미인증 → 로그인 차단 (별도 예외로 안내)
        if not user.email_verified:
            logger.info("local_login_failed_unverified", email=email)
            raise EmailNotVerified(email)

        # 차단된 계정 → 로그인 거부
        if user.is_banned:
            logger.warning("local_login_blocked_banned", user_id=str(user.id))
            raise AccountBanned(str(user.id))

        pair = await self._issue_token_pair(user.id)
        logger.info("local_login_success", user_id=str(user.id))
        return pair, user

    # ─────────────────────────────────────────
    #  토큰 발급 헬퍼 (Day 17 ⭐ 공통화)
    # ─────────────────────────────────────────

    async def _issue_token_pair(self, user_id: UUID) -> TokenPair:
        """토큰 페어 발급 + refresh 저장.

        signup / local_login / (카카오) login 에서 공통 사용.
        """
        pair, refresh_jti = create_token_pair(user_id)

        settings = get_settings()
        expires_at = datetime.now(UTC) + timedelta(
            days=settings.jwt_refresh_token_expire_days
        )
        await self.refresh_repo.save(
            jti=refresh_jti,
            user_id=str(user_id),
            expires_at=expires_at,
        )
        return pair

    # ─────────────────────────────────────────
    #  로그인 (카카오/시뮬레이션 — 기존 Day 11)
    # ─────────────────────────────────────────

    async def login(self, user_id: UUID) -> TokenPair:
        user = await self.user_repo.get_by_id_active(user_id)
        if user is None:
            logger.warning("login_failed_user_not_found", user_id=str(user_id))
            raise UserNotFoundForAuth(f"user not found: {user_id}")

        if user.is_banned:
            logger.warning("login_blocked_banned", user_id=str(user.id))
            raise AccountBanned(str(user.id))

        pair = await self._issue_token_pair(user.id)
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

        # 차단된 계정 → 갱신 거부 (이미 발급된 토큰도 무력화)
        user = await self.user_repo.get_by_id_active(UUID(payload.sub))
        if user is None or user.is_banned:
            logger.warning("refresh_blocked_banned_or_missing", user_id=payload.sub)
            raise InvalidCredentials("account unavailable")

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
