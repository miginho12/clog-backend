"""User Repository."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── 조회 ──

    async def get_by_id(self, user_id: UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_id_active(self, user_id: UUID) -> User | None:
        result = await self.session.execute(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.email == email, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_nickname(self, nickname: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.nickname == nickname, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_oauth(self, provider: str, provider_id: str) -> User | None:
        result = await self.session.execute(
            select(User).where(
                User.auth_provider == provider,
                User.auth_provider_id == provider_id,
                User.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def search_by_nickname(
        self,
        *,
        query: str,
        exclude_user_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[User], bool]:
        """닉네임 부분검색 (활성·미차단만). ilike 대소문자 무시.

        반환: (users, has_next). 탈퇴(deleted_at)·차단(is_banned)·본인 제외.
        정렬: 접두 일치 우선(닉네임이 query 로 시작) → 닉네임 오름차순.
        """
        from sqlalchemy import case

        pattern = f"%{query}%"
        prefix = f"{query}%"
        conds = [
            User.deleted_at.is_(None),
            User.is_banned.is_(False),
            User.nickname.ilike(pattern),
        ]
        if exclude_user_id is not None:
            conds.append(User.id != exclude_user_id)

        offset = (page - 1) * page_size
        stmt = (
            select(User)
            .where(*conds)
            .order_by(
                # 접두 일치(0)가 부분 일치(1)보다 먼저
                case((User.nickname.ilike(prefix), 0), else_=1),
                User.nickname.asc(),
            )
            .offset(offset)
            .limit(page_size + 1)
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        has_next = len(rows) > page_size
        return rows[:page_size], has_next

    async def list_active(self, page: int = 1, page_size: int = 20) -> list[User]:
        offset = (page - 1) * page_size
        result = await self.session.execute(
            select(User)
            .where(User.deleted_at.is_(None))
            .order_by(User.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        return list(result.scalars().all())

    async def count_active(self) -> int:
        result = await self.session.execute(
            select(func.count(User.id)).where(User.deleted_at.is_(None))
        )
        return result.scalar_one()

    # ── 닉네임 자동 suffix (⭐ Day 12 추가) ──

    async def find_available_nickname(
        self, base_nickname: str, max_attempts: int = 100
    ) -> str:
        """사용 가능한 닉네임 찾기 (자동 suffix).

        Args:
            base_nickname: 카카오에서 받은 닉네임
            max_attempts: 최대 시도 횟수

        Returns:
            사용 가능한 닉네임 (base, base_1, base_2, ...)

        Raises:
            RuntimeError: max_attempts 안에 못 찾음 (매우 드문 케이스)

        예:
            "홍길동" 사용 안 됨 → "홍길동" 반환
            "홍길동" 사용 중 → "홍길동_1" 시도 → 사용 가능 → 반환
        """
        # 닉네임 길이 제한 (모델: 50자)
        # suffix 붙일 공간 확보 (최대 _99 = 3자) → 47자까지만
        max_base_length = 47
        if len(base_nickname) > max_base_length:
            base_nickname = base_nickname[:max_base_length]

        # 1차 시도: 원본
        if await self.get_by_nickname(base_nickname) is None:
            return base_nickname

        # 2차+ 시도: suffix
        for attempt in range(1, max_attempts + 1):
            candidate = f"{base_nickname}_{attempt}"
            if await self.get_by_nickname(candidate) is None:
                return candidate

        # 100번 시도해도 못 찾음 (극히 드문 케이스)
        raise RuntimeError(
            f"could not find available nickname for '{base_nickname}' "
            f"after {max_attempts} attempts"
        )

    # ── 생성/수정/삭제 ──

    async def create(
        self,
        *,
        email: str,
        nickname: str,
        auth_provider: str,
        auth_provider_id: str | None = None,
        password_hash: str | None = None,
        profile_image_url: str | None = None,
        bio: str | None = None,
        email_verified: bool = False,
    ) -> User:
        user = User(
            email=email,
            nickname=nickname,
            auth_provider=auth_provider,
            auth_provider_id=auth_provider_id,
            password_hash=password_hash,
            profile_image_url=profile_image_url,
            bio=bio,
            email_verified=email_verified,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def update(self, user: User, **fields) -> User:
        for key, value in fields.items():
            if hasattr(user, key) and value is not None:
                setattr(user, key, value)
        await self.session.flush()
        return user

    async def soft_delete(self, user: User) -> User:
        from datetime import UTC, datetime

        user.deleted_at = datetime.now(UTC)
        await self.session.flush()
        return user

    async def delete_follows(self, user_id: UUID) -> int:
        """탈퇴 사용자가 얽힌 모든 팔로우 관계 hard delete.

        follows 는 복구 불필요 리소스(모델 주석)라 hard delete.
        follower/following 양방향 모두 제거 → 상대방 카운트도 정확해짐.
        """
        from sqlalchemy import delete, or_

        from app.domain.follows.models import Follow

        result = await self.session.execute(
            delete(Follow).where(
                or_(Follow.follower_id == user_id, Follow.following_id == user_id)
            )
        )
        await self.session.flush()
        return result.rowcount or 0

    async def anonymize_and_soft_delete(self, user: User) -> User:
        """탈퇴 처리: email/nickname 익명화 + soft delete.

        unique 제약(email, nickname)이 걸린 값을 익명 값으로 바꿔
        같은 이메일/닉네임으로 재가입이 가능하도록 슬롯을 비운다.
        같은 트랜잭션 안에서 한 번의 flush 로 원자적으로 처리.
        """
        from datetime import UTC, datetime

        token = user.id.hex[:12]
        user.email = f"deleted_{user.id.hex}@deleted.clog"
        user.nickname = f"deleted_{token}"
        user.deleted_at = datetime.now(UTC)
        await self.session.flush()
        return user

    async def set_banned(self, user: User, banned: bool) -> User:
        user.is_banned = banned
        await self.session.flush()
        return user
