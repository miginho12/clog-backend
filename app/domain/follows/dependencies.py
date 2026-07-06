"""Follows 도메인 FastAPI 의존성 주입."""
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.follows.repository import FollowRepository
from app.domain.follows.service import FollowService
from app.domain.notifications.repository import NotificationRepository
from app.domain.notifications.service import NotificationService
from app.domain.users.repository import UserRepository
from app.infra.db import get_session


def get_follow_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FollowService:
    return FollowService(
        session=session,
        repository=FollowRepository(session),
        user_repo=UserRepository(session),
        notification_service=NotificationService(
            session, NotificationRepository(session)
        ),
    )


FollowServiceDep = Annotated[FollowService, Depends(get_follow_service)]
