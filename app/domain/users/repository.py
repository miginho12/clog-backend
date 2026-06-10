"""User Repository.

DB 와 직접 대화하는 유일한 레이어.
Service 가 이걸 사용하고, Service 는 DB 의 세부사항을 모름.

Spring Data JPA 의 JpaRepository 와 비슷한 역할.
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User


class UserRepository:
    """User 의 영속성 관리.

    명시적 컨스트럭터 주입 — FastAPI 가 dependencies.py 에서 주입.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # ── 조회 ──

    async def get_by_id(self, user_id: UUID) -> User | None:
        """ID 로 조회 (soft-deleted 포함)."""
        return await self.session.get(User, user_id)

    async def get_by_id_active(self, user_id: UUID) -> User | None:
        """ID 로 조회 (soft-deleted 제외)."""
        result = await self.session.execute(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        """이메일로 조회 (soft-deleted 제외)."""
        result = await self.session.execute(
            select(User).where(User.email == email, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_nickname(self, nickname: str) -> User | None:
        """닉네임으로 조회 (soft-deleted 제외)."""
        result = await self.session.execute(
            select(User).where(User.nickname == nickname, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_oauth(self, provider: str, provider_id: str) -> User | None:
        """OAuth 식별자로 조회 (soft-deleted 제외)."""
        result = await self.session.execute(
            select(User).where(
                User.auth_provider == provider,
                User.auth_provider_id == provider_id,
                User.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_active(self, page: int = 1, page_size: int = 20) -> list[User]:
        """활성 사용자 목록 (페이지네이션)."""
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
        """활성 사용자 총 수."""
        result = await self.session.execute(
            select(func.count(User.id)).where(User.deleted_at.is_(None))
        )
        return result.scalar_one()

    # ── 생성/수정/삭제 ──

    async def create(
        self,
        *,
        email: str,
        nickname: str,
        auth_provider: str,
        auth_provider_id: str,
        profile_image_url: str | None = None,
        bio: str | None = None,
    ) -> User:
        """새 사용자 생성.

        ⚠️ commit 은 Service 책임 (Unit of Work 패턴).
        Repository 는 add 만, transaction 경계는 Service.
        """
        user = User(
            email=email,
            nickname=nickname,
            auth_provider=auth_provider,
            auth_provider_id=auth_provider_id,
            profile_image_url=profile_image_url,
            bio=bio,
        )
        self.session.add(user)
        await self.session.flush()  # PK (UUID) 가 DB 에서 생성되므로 flush 필요
        return user

    async def update(self, user: User, **fields) -> User:
        """필드 수정.

        ORM 객체에 직접 setattr.
        """
        for key, value in fields.items():
            if hasattr(user, key) and value is not None:
                setattr(user, key, value)
        await self.session.flush()
        return user

    async def soft_delete(self, user: User) -> User:
        """Soft delete (deleted_at 만 설정)."""
        from datetime import UTC, datetime

        user.deleted_at = datetime.now(UTC)
        await self.session.flush()
        return user
