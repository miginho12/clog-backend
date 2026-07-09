"""Follows Service — 팔로우/언팔로우 (likes 패턴) + 알림 연계."""
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.follows.exceptions import (
    CannotFollowSelf,
    FollowTargetNotFound,
)
from app.domain.follows.repository import FollowRepository
from app.domain.notifications.service import NotificationService
from app.domain.users.models import User
from app.domain.users.repository import UserRepository

logger = get_logger(__name__)


class FollowService:
    def __init__(
        self,
        session: AsyncSession,
        repository: FollowRepository,
        user_repo: UserRepository,
        notification_service: NotificationService,
    ):
        self.session = session
        self.repo = repository
        self.user_repo = user_repo
        self.notification_service = notification_service

    async def _assert_target_exists(self, user_id: UUID) -> User:
        """팔로우 대상 사용자 존재 확인 (탈퇴/미존재 → NotFound)."""
        user = await self.user_repo.get_by_id_active(user_id)
        if user is None:
            raise FollowTargetNotFound(str(user_id))
        return user

    async def follow(
        self, *, follower_id: UUID, following_id: UUID
    ) -> bool:
        """팔로우 (idempotent). 새로 팔로우하면 True.

        자기 자신 팔로우는 거부. 새로 추가된 경우에만 알림.
        """
        if follower_id == following_id:
            raise CannotFollowSelf()
        await self._assert_target_exists(following_id)
        added = await self.repo.add(
            follower_id=follower_id, following_id=following_id
        )
        if added:
            await self.notification_service.notify_follow(
                recipient_id=following_id, actor_id=follower_id
            )
        await self.session.commit()
        logger.info(
            "follow_added" if added else "follow_noop",
            follower=str(follower_id),
            following=str(following_id),
        )
        return added

    async def unfollow(
        self, *, follower_id: UUID, following_id: UUID
    ) -> None:
        """언팔로우 (idempotent)."""
        await self.repo.remove(
            follower_id=follower_id, following_id=following_id
        )
        # 좋아요 취소와 대칭: 팔로우 알림도 함께 제거 (재팔로우 시 중복 누적 방지)
        await self.notification_service.remove_follow(
            actor_id=follower_id, recipient_id=following_id
        )
        await self.session.commit()
        logger.info(
            "follow_removed",
            follower=str(follower_id),
            following=str(following_id),
        )
