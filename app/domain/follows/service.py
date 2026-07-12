"""Follows Service — 팔로우/언팔로우 (likes 패턴) + 알림 연계."""
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.follows.exceptions import (
    CannotFollowSelf,
    FollowRequestNotFound,
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
    ) -> str:
        """팔로우 (idempotent).

        대상이 공개 계정이면 즉시 accepted, 비공개면 pending(요청).
        자기 자신 팔로우는 거부.

        Returns: "accepted" | "pending" | "noop"(이미 관계 존재)
        """
        if follower_id == following_id:
            raise CannotFollowSelf()
        target = await self._assert_target_exists(following_id)

        # 이미 관계가 있으면 그 상태를 반환 (idempotent)
        existing = await self.repo.get_status(
            follower_id=follower_id, following_id=following_id
        )
        if existing is not None:
            await self.session.commit()
            return existing

        new_status = "accepted" if target.is_public else "pending"
        await self.repo.add(
            follower_id=follower_id,
            following_id=following_id,
            status=new_status,
        )
        if new_status == "accepted":
            await self.notification_service.notify_follow(
                recipient_id=following_id, actor_id=follower_id
            )
        else:
            await self.notification_service.notify_follow_request(
                recipient_id=following_id, actor_id=follower_id
            )
        await self.session.commit()
        logger.info(
            "follow_created",
            status=new_status,
            follower=str(follower_id),
            following=str(following_id),
        )
        return new_status

    async def unfollow(
        self, *, follower_id: UUID, following_id: UUID
    ) -> None:
        """언팔로우 (idempotent)."""
        await self.repo.remove(
            follower_id=follower_id, following_id=following_id
        )
        # 좋아요 취소와 대칭: 팔로우/요청 알림도 함께 제거
        await self.notification_service.remove_follow(
            actor_id=follower_id, recipient_id=following_id
        )
        await self.notification_service.remove_follow_request(
            actor_id=follower_id, recipient_id=following_id
        )
        await self.session.commit()
        logger.info(
            "follow_removed",
            follower=str(follower_id),
            following=str(following_id),
        )

    async def accept_request(
        self, *, owner_id: UUID, requester_id: UUID
    ) -> None:
        """팔로우 요청 수락 (owner 가 requester 의 pending 을 accepted 로).

        Raises:
            FollowRequestNotFound: 해당 pending 요청 없음
        """
        ok = await self.repo.accept(
            follower_id=requester_id, following_id=owner_id
        )
        if not ok:
            raise FollowRequestNotFound(str(requester_id))
        # 요청 알림 제거 + 수락 알림 발송(요청자에게)
        await self.notification_service.remove_follow_request(
            actor_id=requester_id, recipient_id=owner_id
        )
        await self.notification_service.notify_follow_accept(
            recipient_id=requester_id, actor_id=owner_id
        )
        await self.session.commit()
        logger.info(
            "follow_request_accepted",
            owner=str(owner_id),
            requester=str(requester_id),
        )

    async def reject_request(
        self, *, owner_id: UUID, requester_id: UUID
    ) -> None:
        """팔로우 요청 거절 (pending row 삭제).

        Raises:
            FollowRequestNotFound: 해당 pending 요청 없음
        """
        status = await self.repo.get_status(
            follower_id=requester_id, following_id=owner_id
        )
        if status != "pending":
            raise FollowRequestNotFound(str(requester_id))
        await self.repo.remove(
            follower_id=requester_id, following_id=owner_id
        )
        await self.notification_service.remove_follow_request(
            actor_id=requester_id, recipient_id=owner_id
        )
        await self.session.commit()
        logger.info(
            "follow_request_rejected",
            owner=str(owner_id),
            requester=str(requester_id),
        )

    async def get_follow_status(
        self, *, follower_id: UUID, following_id: UUID
    ) -> str | None:
        """viewer→target 팔로우 상태 (none=None | pending | accepted)."""
        return await self.repo.get_status(
            follower_id=follower_id, following_id=following_id
        )
