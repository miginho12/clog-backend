"""Climbing 도메인 FastAPI 의존성 주입."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.climbing.repository import ClimbingRepository
from app.domain.climbing.service import ClimbingService
from app.infra.db import get_session


def get_climbing_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ClimbingRepository:
    return ClimbingRepository(session)


def get_climbing_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    repository: Annotated[ClimbingRepository, Depends(get_climbing_repository)],
) -> ClimbingService:
    return ClimbingService(session=session, repository=repository)


ClimbingServiceDep = Annotated[ClimbingService, Depends(get_climbing_service)]
