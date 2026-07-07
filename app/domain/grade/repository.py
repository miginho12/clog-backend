"""Grade Repository.

짐 색 순서 CRUD + 조회 (구현 1, 6) + 색 ↔ rank ↔ ratio 변환 (구현 1, 3).
점수 계산용 기록 조회 (구현 2~): list_user_logs_for_grading.
점수 계산 로직 자체는 service 에 위치.
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.climbing.models import ClimbingLog
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

    async def get_by_id(self, system_id: UUID) -> GymGradeSystem | None:
        result = await self.session.execute(
            select(GymGradeSystem).where(GymGradeSystem.id == system_id)
        )
        return result.scalar_one_or_none()

    async def get_systems_map(
        self, gym_names: Iterable[str]
    ) -> dict[str, GymGradeSystem]:
        """여러 짐 이름 → {gym_name: GymGradeSystem} 일괄 조회 (IN 쿼리).

        color 점수 계산 시 기록마다 짐 조회하는 N+1 을 피하려 한 번에.
        None/빈 이름 제외, DB 에 존재하는 것만 매핑.
        """
        names = list({n for n in gym_names if n})
        if not names:
            return {}
        result = await self.session.execute(
            select(GymGradeSystem).where(GymGradeSystem.gym_name.in_(names))
        )
        return {s.gym_name: s for s in result.scalars().all()}

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

    # ── 짐 색 순서 등록/수정/삭제 (구현 6) ──

    async def create(
        self,
        *,
        gym_name: str,
        color_order: list[str],
        created_by: UUID | None,
        is_official: bool = False,
    ) -> GymGradeSystem:
        system = GymGradeSystem(
            gym_name=gym_name,
            color_order=color_order,
            is_official=is_official,
            created_by=created_by,
        )
        self.session.add(system)
        await self.session.flush()
        return system

    async def update_color_order(
        self, system: GymGradeSystem, color_order: list[str]
    ) -> GymGradeSystem:
        """color_order 만 갱신. gym_name 은 불변(기록 매칭 키)."""
        system.color_order = color_order
        await self.session.flush()
        return system

    async def delete(self, system: GymGradeSystem) -> None:
        """hard delete (GymGradeSystem 은 soft-delete 미지원 모델)."""
        await self.session.delete(system)
        await self.session.flush()

    # ── 점수 계산용 본인 기록 조회 (구현 2~) ──

    async def list_user_logs_for_grading(
        self, user_id: UUID, grade_system: str = "v_scale"
    ) -> list[ClimbingLog]:
        """점수 계산 대상 본인 기록 전체 (grade_system 별).

        list_feed 와 달리 공개범위(visibility) 필터 없음 — 본인 점수
        계산이므로 private 도 포함. soft-delete 만 제외. grade_system
        인자로 v_scale(구현 2) / color(구현 3) 분리 조회. 페이지네이션
        없음(전체 필요). 정렬은 service 의 상위 N 선별에 맡김.
        """
        result = await self.session.execute(
            select(ClimbingLog).where(
                ClimbingLog.user_id == user_id,
                ClimbingLog.grade_system == grade_system,
                ClimbingLog.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def count_success(self, user_id: UUID) -> int:
        """유저의 완등(is_success=True) 기록 수. 프로필 통계용."""
        result = await self.session.execute(
            select(func.count())
            .select_from(ClimbingLog)
            .where(
                ClimbingLog.user_id == user_id,
                ClimbingLog.is_success.is_(True),
                ClimbingLog.deleted_at.is_(None),
            )
        )
        return int(result.scalar_one())

    async def count_total(self, user_id: UUID) -> int:
        """유저의 전체 기록 수 (완등+시도). 프로필 통계용."""
        result = await self.session.execute(
            select(func.count())
            .select_from(ClimbingLog)
            .where(
                ClimbingLog.user_id == user_id,
                ClimbingLog.deleted_at.is_(None),
            )
        )
        return int(result.scalar_one())

    # ── 색 ↔ rank ↔ ratio 변환 (핵심 헬퍼) ──

    @staticmethod
    def color_to_rank(system: GymGradeSystem, color: str) -> int | None:
        """색 이름 → 그 짐 내 rank (0-based 인덱스). 없으면 None."""
        try:
            return system.color_order.index(color)
        except ValueError:
            return None

    @staticmethod
    def rank_to_ratio(system: GymGradeSystem, rank: int) -> float:
        """rank → 비율 (0.0 ~ 1.0). rank / (N-1). 단일 단계 짐은 0.0."""
        n = len(system.color_order)
        if n <= 1:
            return 0.0
        return rank / (n - 1)

    @staticmethod
    def ratio_to_color(system: GymGradeSystem, ratio: float) -> str:
        """비율(0~1) → 그 짐 color_order 의 해당 위치 색 (rank_to_ratio 역).

        기준짐 환산(투영)용. idx = round(ratio×(N-1)) half-up, 범위 클램프.
        """
        n = len(system.color_order)
        if n == 0:
            return ""
        idx = int(ratio * (n - 1) + 0.5)
        idx = max(0, min(idx, n - 1))
        return system.color_order[idx]
