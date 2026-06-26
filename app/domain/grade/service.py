"""Grade Service.

- 종합점수 + 탑레이팅 산정 (ADR-041, ADR-042): 구현 2~5
- 짐 색체계 등록/조회/수정/삭제 (구현 6)

순수 계산 함수는 모듈 레벨에 두어 DB 없이 단위 검증 가능.
DB 조회가 필요한 집계/CRUD 는 GradeService 에 위치.
"""

import math
import re
from collections import Counter
from datetime import UTC, date, datetime
from uuid import UUID

from app.domain.grade.exceptions import (
    GymGradeSystemAlreadyExists,
    GymGradeSystemForbidden,
    GymGradeSystemNotFound,
    GymGradeSystemNotFoundById,
)
from app.domain.grade.models import GymGradeSystem
from app.domain.grade.repository import GradeRepository
from app.domain.grade.schemas import ColorGrade, VScaleGrade

# 종합점수에 반영할 상위 기록 수 (ADR-042: 상위 N개 가중평균)
TOP_N = 10

# v_scale 표준 표기만 허용 (V + 정수). "V3+", "v3", "보라" 등은 스킵.
_V_PATTERN = re.compile(r"V(\d+)\Z")


def parse_v_scale(grade_raw: str) -> int | None:
    """grade_raw 에서 V 숫자 추출. 표준 V+정수 만, 아니면 None.

    앞뒤 공백은 무시. 'V0'->0, 'V5'->5, 'V3+'/'v3'/'보라'->None.
    """
    if not grade_raw:
        return None
    m = _V_PATTERN.fullmatch(grade_raw.strip())
    return int(m.group(1)) if m else None


def color_difficulty(ratio: float) -> float:
    """color 트랙 difficulty (ADR-041, 확정 (b)).

    ratio(0~1) × 10 + 1 → 1~11. v_scale 의 V0=1 보정과 대칭.
    """
    return ratio * 10 + 1


def compute_contribution(
    *,
    difficulty: float,
    is_success: bool,
    attempts: int,
    climbed_at: date,
    today: date,
) -> float:
    """단일 기록의 기여도 (ADR-042). 두 트랙 공용.

    contribution = difficulty * success_weight * time_weight * efficiency
      success_weight = 완등 1.0 / 실패 0.3
      time_weight    = 0.5 ^ (경과일/60)  (60일 반감기, 미래날짜는 0일 clamp)
      efficiency     = 1 / (1 + ln(attempts))  (attempts<=0 은 1 보정 -> 1.0)
    """
    success_weight = 1.0 if is_success else 0.3
    elapsed_days = max(0, (today - climbed_at).days)
    time_weight = 0.5 ** (elapsed_days / 60)
    a = attempts if (attempts and attempts > 0) else 1
    efficiency = 1.0 / (1 + math.log(a))
    return difficulty * success_weight * time_weight * efficiency


class GradeService:
    def __init__(self, repo: GradeRepository, session=None):
        self.repo = repo
        # 쓰기 트랜잭션 커밋용 (get_session 은 자동 커밋 안 함). 산정은 읽기라 불필요.
        self.session = session

    # ── 산정 (구현 2~5) ──

    async def compute_v_scale_grade(self, user_id: UUID) -> VScaleGrade:
        logs = await self.repo.list_user_logs_for_grading(user_id, "v_scale")
        today = datetime.now(UTC).date()

        contributions: list[float] = []
        success_v: list[int] = []
        for log in logs:
            v = parse_v_scale(log.grade_raw)
            if v is None:
                continue
            contributions.append(
                compute_contribution(
                    difficulty=v + 1,
                    is_success=log.is_success,
                    attempts=log.attempts,
                    climbed_at=log.climbed_at,
                    today=today,
                )
            )
            if log.is_success:
                success_v.append(v)

        top = sorted(contributions, reverse=True)[:TOP_N]
        comprehensive_score = sum(top) / len(top) if top else 0.0

        top_rating = max(success_v) if success_v else None
        top_rating_label = f"V{top_rating}" if top_rating is not None else None

        return VScaleGrade(
            comprehensive_score=comprehensive_score,
            top_rating=top_rating,
            top_rating_label=top_rating_label,
            counted_logs=len(top),
        )

    async def compute_color_grade(
        self, user_id: UUID, base_gym: str | None = None
    ) -> ColorGrade:
        explicit_base_system = None
        if base_gym is not None:
            explicit_base_system = await self.repo.get_by_gym_name(base_gym)
            if explicit_base_system is None:
                raise GymGradeSystemNotFound(base_gym)

        logs = await self.repo.list_user_logs_for_grading(user_id, "color")
        today = datetime.now(UTC).date()

        systems = await self.repo.get_systems_map(
            log.gym_name for log in logs if log.gym_name
        )

        contributions: list[float] = []
        gym_counter: Counter[str] = Counter()
        success_ratios: list[float] = []
        for log in logs:
            system = systems.get(log.gym_name) if log.gym_name else None
            if system is None:
                continue
            rank = self.repo.color_to_rank(system, log.grade_raw)
            if rank is None:
                continue
            ratio = self.repo.rank_to_ratio(system, rank)
            contributions.append(
                compute_contribution(
                    difficulty=color_difficulty(ratio),
                    is_success=log.is_success,
                    attempts=log.attempts,
                    climbed_at=log.climbed_at,
                    today=today,
                )
            )
            gym_counter[log.gym_name] += 1
            if log.is_success:
                success_ratios.append(ratio)

        top = sorted(contributions, reverse=True)[:TOP_N]
        comprehensive_score = sum(top) / len(top) if top else 0.0

        if base_gym is not None:
            resolved_base_gym: str | None = base_gym
            base_system = explicit_base_system
        else:
            resolved_base_gym = None
            base_system = None
            if gym_counter:
                resolved_base_gym = sorted(
                    gym_counter.items(), key=lambda kv: (-kv[1], kv[0])
                )[0][0]
                base_system = systems.get(resolved_base_gym)

        top_rating_label: str | None = None
        if success_ratios and base_system is not None:
            top_ratio = max(success_ratios)
            top_rating_label = self.repo.ratio_to_color(base_system, top_ratio)

        return ColorGrade(
            comprehensive_score=comprehensive_score,
            base_gym=resolved_base_gym,
            top_rating_label=top_rating_label,
            counted_logs=len(top),
        )

    # ── 짐 색체계 CRUD (구현 6) ──

    async def list_gym_systems(self) -> list[GymGradeSystem]:
        return await self.repo.list_all()

    async def get_gym_system(self, system_id: UUID) -> GymGradeSystem:
        system = await self.repo.get_by_id(system_id)
        if system is None:
            raise GymGradeSystemNotFoundById(str(system_id))
        return system

    async def create_gym_system(
        self, *, gym_name: str, color_order: list[str], user_id: UUID
    ) -> GymGradeSystem:
        """짐 색체계 등록. 사용자 등록(is_official=False, created_by=user).

        gym_name 중복이면 GymGradeSystemAlreadyExists(409).
        """
        existing = await self.repo.get_by_gym_name(gym_name)
        if existing is not None:
            raise GymGradeSystemAlreadyExists(gym_name)
        system = await self.repo.create(
            gym_name=gym_name,
            color_order=color_order,
            created_by=user_id,
            is_official=False,
        )
        await self.session.commit()
        await self.session.refresh(system)  # commit 후 expire 방지 (응답 직렬화용)
        return system


    async def update_gym_system(
        self, *, system_id: UUID, color_order: list[str], user_id: UUID
    ) -> GymGradeSystem:
        """color_order 수정. 본인 등록분(비공식)만 (아니면 403)."""
        system = await self.repo.get_by_id(system_id)
        if system is None:
            raise GymGradeSystemNotFoundById(str(system_id))
        if system.is_official or system.created_by != user_id:
            raise GymGradeSystemForbidden(str(system_id))
        updated = await self.repo.update_color_order(system, color_order)
        await self.session.commit()
        await self.session.refresh(updated)  # commit 후 expire 방지 (응답 직렬화용)
        return updated

    async def delete_gym_system(self, *, system_id: UUID, user_id: UUID) -> None:
        """삭제. 본인 등록분(비공식)만 (아니면 403)."""
        system = await self.repo.get_by_id(system_id)
        if system is None:
            raise GymGradeSystemNotFoundById(str(system_id))
        if system.is_official or system.created_by != user_id:
            raise GymGradeSystemForbidden(str(system_id))
        await self.repo.delete(system)
        await self.session.commit()
