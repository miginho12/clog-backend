"""Kakao OAuth Service.

비즈니스 로직:
1. 로그인 시작 → state 생성 + authorize URL
2. 콜백 처리 → state 검증 → code 교환 → 사용자 정보 → User 생성/조회 → JWT
   - 앱(client=app, 감사 M14): JWT 대신 일회용 코드 → 3. 교환(PKCE 검증) 때 JWT
"""

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import TokenPair, create_token_pair
from app.domain.auth.app_code_repository import AppLoginCodeRepository
from app.domain.auth.exceptions import (
    AccountBanned,
    KakaoEmailNotAvailable,
    OAuthAppCodeInvalid,
    OAuthStateInvalid,
)
from app.domain.auth.kakao_schemas import KakaoTokenResponse, KakaoUserInfo
from app.domain.auth.repository import RedisRefreshTokenRepository
from app.domain.auth.state_repository import OAuthState, OAuthStateRepository
from app.domain.users.models import User
from app.domain.users.repository import UserRepository
from app.infra.kakao import KakaoOAuthClient

logger = get_logger(__name__)

# RFC 7636 — verifier 43~128자 unreserved 문자, challenge = BASE64URL(SHA256(verifier)) 43자
_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
CODE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def pkce_matches(verifier: str, challenge: str) -> bool:
    if not _VERIFIER_RE.fullmatch(verifier or ""):
        return False
    return hmac.compare_digest(pkce_s256(verifier), challenge)


@dataclass(frozen=True)
class KakaoCallbackResult:
    """콜백 결과 — web 은 pair, app 은 app_code 중 하나만 채워진다."""

    user: User
    is_new_user: bool
    pair: TokenPair | None = None
    app_code: str | None = None


class KakaoOAuthService:
    """카카오 OAuth 흐름 전체 관리."""

    def __init__(
        self,
        session: AsyncSession,
        kakao_client: KakaoOAuthClient,
        state_repo: OAuthStateRepository,
        refresh_repo: RedisRefreshTokenRepository,
        user_repo: UserRepository,
        app_code_repo: AppLoginCodeRepository,
    ):
        self.session = session
        self.app_code_repo = app_code_repo
        self.kakao = kakao_client
        self.state_repo = state_repo
        self.refresh_repo = refresh_repo
        self.user_repo = user_repo

    # ── 1. 로그인 시작 ──

    async def initiate_login(self, meta: OAuthState | None = None) -> tuple[str, str]:
        """카카오 로그인 시작.

        Args:
            meta: 앱 로그인이면 OAuthState(client="app", code_challenge=...)

        Returns:
            (authorize_url, state):
            - authorize_url: 사용자가 리다이렉트될 카카오 URL
            - state: CSRF 방어용 (Redis 에 저장됨)
        """
        meta = meta or OAuthState()
        state = await self.state_repo.create(meta)
        authorize_url = self.kakao.build_authorize_url(state)

        logger.info("kakao_login_initiated", state_prefix=state[:10], client=meta.client)
        return authorize_url, state

    # ── 2. 콜백 처리 ──

    async def handle_callback(self, code: str, state: str) -> KakaoCallbackResult:
        """카카오 콜백 처리 → 우리 JWT 발급.

        Args:
            code: 카카오가 보낸 인증 코드
            state: 카카오가 그대로 돌려준 state (CSRF 검증)

        Returns:
            KakaoCallbackResult:
            - web: pair (우리 시스템의 access + refresh JWT)
            - app: app_code (일회용 코드 — exchange_app_code 로 JWT 교환)

        Raises:
            OAuthStateInvalid: state 검증 실패
            KakaoTokenExchangeFailed: 카카오 토큰 교환 실패
            KakaoUserInfoFailed: 카카오 사용자 정보 조회 실패
            KakaoEmailNotAvailable: 이메일 동의 안 함 (회원가입에 필수)
        """
        # 1. State 검증 (CSRF 방어)
        meta = await self.state_repo.consume(state)
        if meta is None:
            raise OAuthStateInvalid("invalid or expired state parameter")
        if meta.client == "app" and not meta.code_challenge:
            raise OAuthStateInvalid("app state without code_challenge")

        # 2. Code → Access Token 교환
        token_dict = await self.kakao.exchange_code_for_token(code)
        token_response = KakaoTokenResponse.model_validate(token_dict)

        # 3. 사용자 정보 조회
        user_info_dict = await self.kakao.fetch_user_info(token_response.access_token)
        user_info = KakaoUserInfo.model_validate(user_info_dict)

        # 4. User 자동 생성/조회
        user, is_new = await self._get_or_create_user(user_info)

        # 차단된 계정 → 카카오 재로그인도 거부
        if user.is_banned:
            logger.warning("kakao_login_blocked_banned", user_id=str(user.id))
            raise AccountBanned(str(user.id))

        # 5-a. 앱: 토큰 대신 일회용 코드 (커스텀 스킴 URL 에 토큰을 싣지 않는다)
        if meta.client == "app":
            app_code = await self.app_code_repo.create(
                user_id=str(user.id), is_new=is_new, code_challenge=meta.code_challenge
            )
            logger.info(
                "kakao_login_complete",
                user_id=str(user.id),
                is_new_user=is_new,
                kakao_id=user_info.auth_provider_id,
                client="app",
            )
            return KakaoCallbackResult(user=user, is_new_user=is_new, app_code=app_code)

        # 5-b. web: 우리 시스템 JWT 발급
        pair = await self._issue_tokens(user)

        logger.info(
            "kakao_login_complete",
            user_id=str(user.id),
            is_new_user=is_new,
            kakao_id=user_info.auth_provider_id,
            client="web",
        )
        return KakaoCallbackResult(user=user, is_new_user=is_new, pair=pair)

    # ── 3. 앱 일회용 코드 교환 (감사 M14) ──

    async def exchange_app_code(
        self, code: str, code_verifier: str
    ) -> tuple[TokenPair, User, bool]:
        """앱이 커스텀 스킴으로 받은 코드 + PKCE verifier → JWT.

        Raises:
            OAuthAppCodeInvalid: 코드 없음·만료·재사용, verifier 불일치, 사용자 없음
            AccountBanned: 코드 발급 뒤 차단된 계정
        """
        entry = await self.app_code_repo.consume(code)
        if entry is None:
            raise OAuthAppCodeInvalid("unknown, expired or used code")
        if not pkce_matches(code_verifier, entry.code_challenge):
            raise OAuthAppCodeInvalid("code_verifier mismatch")

        user = await self.user_repo.get_by_id_active(UUID(entry.user_id))
        if user is None:
            raise OAuthAppCodeInvalid("user not found")
        if user.is_banned:
            logger.warning("kakao_login_blocked_banned", user_id=str(user.id))
            raise AccountBanned(str(user.id))

        pair = await self._issue_tokens(user)
        logger.info("kakao_app_code_exchanged", user_id=str(user.id), is_new_user=entry.is_new)
        return pair, user, entry.is_new

    async def _issue_tokens(self, user: User) -> TokenPair:
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
        return pair

    # ── 내부: User 자동 생성/조회 ──

    async def _get_or_create_user(self, user_info: KakaoUserInfo) -> tuple[User, bool]:
        """카카오 사용자 정보 → User 조회 또는 생성.

        Returns:
            (User, is_new):
            - is_new=False: 기존 사용자
            - is_new=True: 이번에 자동 가입
        """
        kakao_id = user_info.auth_provider_id

        # 1. 기존 사용자 조회 (auth_provider_id 로)
        existing = await self.user_repo.get_by_oauth(
            provider="kakao",
            provider_id=kakao_id,
        )
        if existing:
            logger.info(
                "existing_kakao_user_found",
                user_id=str(existing.id),
                kakao_id=kakao_id,
            )
            return existing, False

        # 2. 새 사용자 → 가입 진행

        # 2-1. 이메일 검증 (회원가입에 필수)
        email = user_info.get_email()
        if email is None:
            logger.warning("kakao_email_missing", kakao_id=kakao_id)
            raise KakaoEmailNotAvailable(kakao_id=kakao_id)

        # 2-2. 이메일 중복 검증
        # 같은 이메일로 다른 OAuth (Google 등) 가입한 케이스 있을 수 있음
        # → 이번 사례는 일단 거부 (보안: 계정 탈취 방지)
        if await self.user_repo.get_by_email(email):
            logger.warning(
                "kakao_email_already_registered_with_other_provider",
                email=email,
                kakao_id=kakao_id,
            )
            # 이메일 중복 → 다른 가입 경로 안내 (서비스 정책)
            # 일단 fallback: 이메일에 카카오 ID 붙여서 회피
            email = f"{kakao_id}+kakao+{email}"

        # 2-3. 닉네임 자동 suffix
        base_nickname = user_info.get_nickname() or f"climber_{uuid4().hex[:8]}"
        nickname = await self.user_repo.find_available_nickname(base_nickname)

        # 2-4. User 생성
        user = await self.user_repo.create(
            email=email,
            nickname=nickname,
            auth_provider="kakao",
            auth_provider_id=kakao_id,
            profile_image_url=user_info.get_profile_image(),
            email_verified=True,  # OAuth(카카오)는 이미 이메일 검증됨
        )
        await self.session.commit()
        await self.session.refresh(user)

        logger.info(
            "new_kakao_user_created",
            user_id=str(user.id),
            kakao_id=kakao_id,
            nickname=nickname,
            nickname_was_modified=(nickname != base_nickname),
        )
        return user, True
