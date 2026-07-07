"""Kakao OAuth Service.

비즈니스 로직:
1. 로그인 시작 → state 생성 + authorize URL
2. 콜백 처리 → state 검증 → code 교환 → 사용자 정보 → User 생성/조회 → JWT
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import TokenPair, create_token_pair
from app.domain.auth.exceptions import (
    KakaoEmailNotAvailable,
    OAuthStateInvalid,
)
from app.domain.auth.kakao_schemas import KakaoTokenResponse, KakaoUserInfo
from app.domain.auth.repository import RedisRefreshTokenRepository
from app.domain.auth.state_repository import OAuthStateRepository
from app.domain.users.models import User
from app.domain.users.repository import UserRepository
from app.infra.kakao import KakaoOAuthClient

logger = get_logger(__name__)


class KakaoOAuthService:
    """카카오 OAuth 흐름 전체 관리."""

    def __init__(
        self,
        session: AsyncSession,
        kakao_client: KakaoOAuthClient,
        state_repo: OAuthStateRepository,
        refresh_repo: RedisRefreshTokenRepository,
        user_repo: UserRepository,
    ):
        self.session = session
        self.kakao = kakao_client
        self.state_repo = state_repo
        self.refresh_repo = refresh_repo
        self.user_repo = user_repo

    # ── 1. 로그인 시작 ──

    async def initiate_login(self) -> tuple[str, str]:
        """카카오 로그인 시작.

        Returns:
            (authorize_url, state):
            - authorize_url: 사용자가 리다이렉트될 카카오 URL
            - state: CSRF 방어용 (Redis 에 저장됨)
        """
        state = await self.state_repo.create()
        authorize_url = self.kakao.build_authorize_url(state)

        logger.info("kakao_login_initiated", state_prefix=state[:10])
        return authorize_url, state

    # ── 2. 콜백 처리 ──

    async def handle_callback(self, code: str, state: str) -> tuple[TokenPair, User, bool]:
        """카카오 콜백 처리 → 우리 JWT 발급.

        Args:
            code: 카카오가 보낸 인증 코드
            state: 카카오가 그대로 돌려준 state (CSRF 검증)

        Returns:
            (TokenPair, User, is_new_user):
            - TokenPair: 우리 시스템의 access + refresh JWT
            - User: 가입/조회된 사용자
            - is_new_user: 이번 콜백에서 새로 가입했는지 여부

        Raises:
            OAuthStateInvalid: state 검증 실패
            KakaoTokenExchangeFailed: 카카오 토큰 교환 실패
            KakaoUserInfoFailed: 카카오 사용자 정보 조회 실패
            KakaoEmailNotAvailable: 이메일 동의 안 함 (회원가입에 필수)
        """
        # 1. State 검증 (CSRF 방어)
        if not await self.state_repo.consume(state):
            raise OAuthStateInvalid("invalid or expired state parameter")

        # 2. Code → Access Token 교환
        token_dict = await self.kakao.exchange_code_for_token(code)
        token_response = KakaoTokenResponse.model_validate(token_dict)

        # 3. 사용자 정보 조회
        user_info_dict = await self.kakao.fetch_user_info(token_response.access_token)
        user_info = KakaoUserInfo.model_validate(user_info_dict)

        # 4. User 자동 생성/조회
        user, is_new = await self._get_or_create_user(user_info)

        # 5. 우리 시스템 JWT 발급
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

        logger.info(
            "kakao_login_complete",
            user_id=str(user.id),
            is_new_user=is_new,
            kakao_id=user_info.auth_provider_id,
        )
        return pair, user, is_new

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
