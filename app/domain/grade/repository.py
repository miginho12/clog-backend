"""Grade Repository.

짐 색 순서 조회 + 색 to rank 변환 (구현 1).
점수 계산은 구현 2~3에서 service 에 추가 예정.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.grade.models import GymGradeSystem


class GradeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── 짐 색 순서 조회 ──

    async def get_by_gym_name(self, gym_name: str) -> GymGradeSystem | None:
        result = await self.session.execute(
            select(GymGradeSystem).where(GymGradeSystem.gym_name == gym_name)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[GymGradeSystem]:
        result = await self.session.execute(
            select(GymGradeSystem).order_by(GymGradeSystem.gym_name)
        )
        return list(result.scalars().all())

    async def list_official(self) -> list[GymGradeSystem]:
        result = await self.session.execute(
            select(GymGradeSystem)
            .where(GymGradeSystem.is_official.is_(True))
            .order_by(GymGradeSystem.gym_name)
        )
        return list(result.scalars().all())

    # ── 색 to rank 변환 (핵심 헬퍼) ──

    @staticmethod
    def color_to_rank(system: GymGradeSystem, color: str) -> int | None:
        """색 이름 → 그 짐 내 rank (0-based 인덱스).

        색이 그 짐 순서에 없으면 None.
        """
        try:
            return system.color_order.index(color)
        except ValueError:
            return None

    @staticmethod
    def rank_to_ratio(system: GymGradeSystem, rank: int) -> float:
        """rank → 비율 (0.0 ~ 1.0).

        단계 수가 다른 짐 간 비교를 위해 정규화.
        총 N단계 중 rank 위치 = rank / (N-1).
        단일 단계 짐은 0.0.
        """
        n = len(system.color_order)
        if n <= 1:
            return 0.0
        return rank / (n - 1)
