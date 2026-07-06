"""Notification 의존성 주입."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notifications.repository import NotificationRepository
from app.domain.notifications.service import NotificationService
from app.infra.db import get_session


def get_notification_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotificationService:
    return NotificationService(session, NotificationRepository(session))


NotificationServiceDep = Annotated[
    NotificationService, Depends(get_notification_service)
]
