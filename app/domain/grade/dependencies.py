"""Grade 도메인 FastAPI 의존성 주입."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.grade.repository import GradeRepository
from app.infra.db import get_session


def get_grade_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GradeRepository:
    return GradeRepository(session)


GradeRepositoryDep = Annotated[GradeRepository, Depends(get_grade_repository)]
