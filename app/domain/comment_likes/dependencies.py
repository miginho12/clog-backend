"""CommentLikes 도메인 FastAPI 의존성 주입."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.climbing.repository import ClimbingRepository
from app.domain.comment_likes.repository import CommentLikeRepository
from app.domain.comment_likes.service import CommentLikeService
from app.domain.comments.repository import CommentRepository
from app.infra.db import get_session


def get_comment_like_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CommentLikeService:
    return CommentLikeService(
        session=session,
        repository=CommentLikeRepository(session),
        comment_repo=CommentRepository(session),
        climbing_repo=ClimbingRepository(session),
    )


CommentLikeServiceDep = Annotated[
    CommentLikeService, Depends(get_comment_like_service)
]
