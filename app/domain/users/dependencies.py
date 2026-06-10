"""User 도메인의 FastAPI 의존성 주입.

라우터에서 깔끔하게 Depends(get_user_service) 만 쓸 수 있도록.

흐름:
    get_session() → AsyncSession
        ↓
    get_user_repository(session) → UserRepository
        ↓
    get_user_service(session, repo) → UserService

Spring 의 @Autowired 와 비슷한 역할.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.repository import UserRepository
from app.domain.users.service import UserService
from app.infra.db import get_session


def get_user_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserRepository:
    """UserRepository 의존성."""
    return UserRepository(session)


def get_user_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    repository: Annotated[UserRepository, Depends(get_user_repository)],
) -> UserService:
    """UserService 의존성."""
    return UserService(session=session, repository=repository)


# 타입 alias (라우터에서 짧게 사용)
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
