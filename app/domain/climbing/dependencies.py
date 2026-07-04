"""Climbing 도메인 FastAPI 의존성 주입."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.climbing.repository import ClimbingRepository
from app.domain.climbing.service import ClimbingService
from app.domain.comment_likes.repository import CommentLikeRepository
from app.domain.comments.repository import CommentRepository
from app.domain.likes.repository import LikeRepository
from app.infra.db import get_session


def get_climbing_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ClimbingRepository:
    return ClimbingRepository(session)


def get_climbing_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    repository: Annotated[ClimbingRepository, Depends(get_climbing_repository)],
) -> ClimbingService:
    return ClimbingService(
        session=session,
        repository=repository,
        like_repo=LikeRepository(session),
        comment_repo=CommentRepository(session),
        comment_like_repo=CommentLikeRepository(session),
    )


ClimbingServiceDep = Annotated[ClimbingService, Depends(get_climbing_service)]
