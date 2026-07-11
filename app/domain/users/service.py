"""User Service.

비즈니스 로직 레이어:
- 중복 검증 (이메일/닉네임/OAuth)
- 트랜잭션 경계 (commit)
- 도메인 이벤트 (나중에)

Repository 가 DB 와 대화한다면, Service 는 도메인 규칙을 다룸.
HTTP 와 분리 — 도메인 예외 던지면 API 가 변환.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.password import (
    hash_password,
    validate_password_policy,
    verify_password,
)
from app.domain.users.exceptions import (
    CannotBanSelf,
    CurrentPasswordMismatch,
    EmailAlreadyExists,
    NicknameAlreadyExists,
    OAuthIdentityAlreadyExists,
    PasswordChangeNotAllowed,
    UserAlreadyDeleted,
    UserNotFound,
    UserProfilePrivate,
)
from app.domain.users.models import User
from app.domain.users.repository import UserRepository
from app.domain.users.schemas import UserCreate, UserUpdate

logger = get_logger(__name__)


class UserService:
    """User 도메인 서비스."""

    def __init__(self, session: AsyncSession, repository: UserRepository):
        self.session = session
        self.repository = repository

    # ── 조회 ──

    async def get_user(self, user_id: UUID) -> User:
        """단일 사용자 조회.

        Raises:
            UserNotFound: 존재 X 또는 삭제됨
        """
        user = await self.repository.get_by_id_active(user_id)
        if user is None:
            raise UserNotFound(user_id=str(user_id))
        return user

    async def list_users(
        self, page: int = 1, page_size: int = 20
    ) -> tuple[list[User], int]:
        """활성 사용자 목록 + 총 수."""
        users = await self.repository.list_active(page=page, page_size=page_size)
        total = await self.repository.count_active()
        return users, total

    # ── 생성 ──

    async def create_user(self, payload: UserCreate) -> User:
        """새 사용자 생성.

        검증 순서:
        1. OAuth 식별자 중복 (가장 강한 제약)
        2. 이메일 중복
        3. 닉네임 중복

        Raises:
            OAuthIdentityAlreadyExists
            EmailAlreadyExists
            NicknameAlreadyExists
        """
        logger.info(
            "user_create_attempt",
            email=payload.email,
            nickname=payload.nickname,
            auth_provider=payload.auth_provider,
        )

        # 1. OAuth 중복 검증
        existing_oauth = await self.repository.get_by_oauth(
            provider=payload.auth_provider,
            provider_id=payload.auth_provider_id,
        )
        if existing_oauth:
            raise OAuthIdentityAlreadyExists(
                provider=payload.auth_provider,
                provider_id=payload.auth_provider_id,
            )

        # 2. 이메일 중복 검증
        existing_email = await self.repository.get_by_email(payload.email)
        if existing_email:
            raise EmailAlreadyExists(email=payload.email)

        # 3. 닉네임 중복 검증
        existing_nickname = await self.repository.get_by_nickname(payload.nickname)
        if existing_nickname:
            raise NicknameAlreadyExists(nickname=payload.nickname)

        # 생성
        user = await self.repository.create(
            email=payload.email,
            nickname=payload.nickname,
            auth_provider=payload.auth_provider,
            auth_provider_id=payload.auth_provider_id,
            profile_image_url=payload.profile_image_url,
            bio=payload.bio,
        )

        # ⭐ Service 가 트랜잭션 경계
        await self.session.commit()
        await self.session.refresh(user)

        logger.info(
            "user_created",
            user_id=str(user.id),
            email=user.email,
            nickname=user.nickname,
        )
        return user

    # ── 수정 ──

    async def update_user(self, user_id: UUID, payload: UserUpdate) -> User:
        """사용자 정보 수정.

        닉네임 변경 시 중복 검증.

        Raises:
            UserNotFound
            NicknameAlreadyExists
        """
        user = await self.get_user(user_id)  # 없으면 여기서 UserNotFound

        # 닉네임 변경 시 중복 검증
        if payload.nickname and payload.nickname != user.nickname:
            existing = await self.repository.get_by_nickname(payload.nickname)
            if existing:
                raise NicknameAlreadyExists(nickname=payload.nickname)

        # 변경된 필드만 적용
        update_data = payload.model_dump(exclude_unset=True, exclude_none=True)
        if update_data:
            await self.repository.update(user, **update_data)
            await self.session.commit()
            await self.session.refresh(user)

            logger.info(
                "user_updated",
                user_id=str(user.id),
                fields=list(update_data.keys()),
            )

        return user

    # ── 삭제 ──

    async def delete_user(self, user_id: UUID) -> None:
        """Soft delete.

        Raises:
            UserNotFound: 존재 X
            UserAlreadyDeleted: 이미 삭제됨
        """
        user = await self.repository.get_by_id(user_id)  # 삭제된 것도 포함
        if user is None:
            raise UserNotFound(user_id=str(user_id))
        if user.deleted_at is not None:
            raise UserAlreadyDeleted(user_id=str(user_id))

        await self.repository.soft_delete(user)
        await self.session.commit()

        logger.info("user_deleted", user_id=str(user_id))

    # ── 비밀번호 변경 (Day 23 ⭐) ──

    async def change_password(
        self, *, user_id: UUID, current_password: str, new_password: str
    ) -> None:
        """자체(local) 계정 비밀번호 변경.

        Raises:
            UserNotFound
            PasswordChangeNotAllowed: OAuth 가입자 (password_hash 없음)
            CurrentPasswordMismatch: 현재 비밀번호 불일치
            PasswordPolicyError: 새 비밀번호 정책 위반
        """
        user = await self.get_user(user_id)

        if user.auth_provider != "local" or not user.password_hash:
            raise PasswordChangeNotAllowed(user_id=str(user_id))

        if not verify_password(current_password, user.password_hash):
            raise CurrentPasswordMismatch(user_id=str(user_id))

        validate_password_policy(new_password)  # 위반 시 PasswordPolicyError

        user.password_hash = hash_password(new_password)
        await self.session.commit()
        await self.session.refresh(user)

        logger.info("user_password_changed", user_id=str(user_id))

    # ── 계정 탈퇴 (Day 23 ⭐) ──

    async def deactivate_account(self, user_id: UUID) -> None:
        """본인 계정 탈퇴 (soft delete + 익명화).

        멱등하지 않음: 이미 삭제된 계정은 UserNotFound 로 처리
        (get_by_id_active 가 deleted_at IS NULL 만 반환).

        refresh 토큰 무효화는 Redis 라 트랜잭션 밖 → 라우트에서 커밋 후 호출.

        Raises:
            UserNotFound: 존재 X 또는 이미 탈퇴됨
        """
        user = await self.get_user(user_id)  # 활성 사용자만
        await self.repository.anonymize_and_soft_delete(user)
        await self.session.commit()
        logger.info("user_account_deactivated", user_id=str(user_id))

    # ── admin Step 3: 차단 ──

    async def set_ban(
        self, *, user_id: UUID, banned: bool, actor_id: UUID
    ) -> User:
        """사용자 차단/차단해제 (admin 전용).

        Raises:
            CannotBanSelf: 자기 자신 차단 시도 (락아웃 방지)
            UserNotFound: 대상 없음/삭제됨
        """
        if banned and user_id == actor_id:
            raise CannotBanSelf(str(user_id))

        user = await self.get_user(user_id)  # UserNotFound 자동
        await self.repository.set_banned(user, banned)
        await self.session.commit()
        await self.session.refresh(user)
        logger.info(
            "user_ban_changed",
            user_id=str(user_id),
            banned=banned,
            actor_id=str(actor_id),
        )
        return user

    async def get_user_for_viewer(
        self, target_user_id: UUID, viewer_user_id: UUID
    ) -> User:
        """다른 사용자 조회 시 is_public 권한 체크.

        본인: 항상 OK / 다른 사용자: is_public=True 만.

        Raises:
            UserNotFound
            UserProfilePrivate
        """
        target = await self.get_user(target_user_id)  # UserNotFound 자동
        if target.id == viewer_user_id:
            return target
        if not target.is_public:
            raise UserProfilePrivate(user_id=str(target_user_id))
        return target
