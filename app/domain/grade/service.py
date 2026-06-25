"""Grade Service.

종합점수 + 탑레이팅 산정 (ADR-041, ADR-042).
- v_scale 트랙 (구현 2): parse_v_scale, compute_v_scale_grade
- color 트랙 (구현 3): color_difficulty, compute_color_grade
  기준짐 환산(구현 4 흡수): compute_color_grade(base_gym=...) 선택 인자.

순수 계산 함수는 모듈 레벨에 두어 DB 없이 단위 검증 가능.
DB 조회가 필요한 집계는 GradeService 에 위치.
"""

import math
import re
from collections import Counter
from datetime import UTC, date, datetime
from uuid import UUID

from app.domain.grade.exceptions import GymGradeSystemNotFound
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
    최하단 색(ratio=0)도 최소 기여 1 을 갖도록.
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

    difficulty 정의만 트랙별로 다름:
      v_scale = V숫자 + 1,  color = ratio×10 + 1
    """
    success_weight = 1.0 if is_success else 0.3
    elapsed_days = max(0, (today - climbed_at).days)
    time_weight = 0.5 ** (elapsed_days / 60)
    a = attempts if (attempts and attempts > 0) else 1
    efficiency = 1.0 / (1 + math.log(a))
    return difficulty * success_weight * time_weight * efficiency


class GradeService:
    def __init__(self, repo: GradeRepository):
        self.repo = repo

    async def compute_v_scale_grade(self, user_id: UUID) -> VScaleGrade:
        """본인 v_scale 기록으로 종합점수 + 탑레이팅 산정.

        - 종합점수: 파싱된 기록 contribution 상위 TOP_N 단순 산술평균.
          표본이 TOP_N 미만이면 실제 개수로 나눔.
        - 탑레이팅: 완등(is_success) 기록 중 최고 V (상위 N 제한 없음).
        - 파싱 가능한 기록이 0개면 (0.0, None, None, 0).
        """
        logs = await self.repo.list_user_logs_for_grading(user_id, "v_scale")
        today = datetime.now(UTC).date()

        contributions: list[float] = []
        success_v: list[int] = []
        for log in logs:
            v = parse_v_scale(log.grade_raw)
            if v is None:
                continue  # 표준 V 표기 아님 -> 스킵
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
        """본인 color 기록으로 종합점수 + 탑레이팅 산정 (ADR-041).

        - 각 기록: 짐 color_order 에서 색 → rank → ratio → difficulty.
          짐 미등록(시스템 없음) 또는 색 미매칭 기록은 스킵.
        - 종합점수: contribution 상위 TOP_N 단순 산술평균 (기준짐 무관).
        - base_gym (기준짐, 탑레이팅 색 투영 기준):
            · 지정(None 아님): 그 짐으로 투영. DB 미등록이면 GymGradeSystemNotFound.
            · None: 유효 기록 최다 짐 자동 선택 (동률 가나다순).
        - 탑레이팅: 완등 중 최고 ratio → 기준짐 color_order 에 투영한 색.
        - 유효 기록이 0개면 (0.0, resolved_base_gym, None, 0).
        """
        # base_gym 지정 시 먼저 검증 (잘못된 짐이면 점수 계산 전 실패)
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
                continue  # 짐 미등록 -> 스킵
            rank = self.repo.color_to_rank(system, log.grade_raw)
            if rank is None:
                continue  # 그 짐에 그 색 없음 -> 스킵
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

        # 기준짐 결정: 지정값 우선, 없으면 최다기록 자동
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

        # 탑레이팅 = 완등 중 최고 ratio → 기준짐 투영 색
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
