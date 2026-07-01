"""Likes 도메인 FastAPI 의존성 주입."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.climbing.repository import ClimbingRepository
from app.domain.likes.repository import LikeRepository
from app.domain.likes.service import LikeService
from app.infra.db import get_session


def get_like_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LikeService:
    return LikeService(
        session=session,
        repository=LikeRepository(session),
        climbing_repo=ClimbingRepository(session),
    )


LikeServiceDep = Annotated[LikeService, Depends(get_like_service)]
