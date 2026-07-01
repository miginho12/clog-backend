"""Comments 도메인 FastAPI 의존성 주입."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.climbing.repository import ClimbingRepository
from app.domain.comments.repository import CommentRepository
from app.domain.comment_likes.repository import CommentLikeRepository
from app.domain.comments.service import CommentService
from app.infra.db import get_session


def get_comment_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CommentService:
    return CommentService(
        session=session,
        repository=CommentRepository(session),
        climbing_repo=ClimbingRepository(session),
        like_repo=CommentLikeRepository(session),
    )


CommentServiceDep = Annotated[CommentService, Depends(get_comment_service)]
