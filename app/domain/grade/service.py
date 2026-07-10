"""Grade Service.

- 종합점수 + 탑레이팅 산정 (ADR-041, ADR-042): 구현 2~5
- 짐 색체계 등록/조회/수정/삭제 (구현 6)

순수 계산 함수는 모듈 레벨에 두어 DB 없이 단위 검증 가능.
DB 조회가 필요한 집계/CRUD 는 GradeService 에 위치.
"""

import math
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
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

# 종합점수에 반영할 상위 기록 수 (ADR-045: 상위 N개의 합)
TOP_N = 10

# 난이도 곡률 (ADR-046) — 위로 갈수록 단계 배율이 커진다.
#   빨(0)→주(1) = 1.5배,  갈(7)→검(8) = 7.0배 (사용자 체감에서 역산)
# 표본 1명의 체감이므로 완등률 데이터가 쌓이면 재보정할 것.
CURVE_A = 0.295
CURVE_B = 0.1101
# ratio(0~1) 를 가상 rank(0~VIRTUAL_SPAN) 로 환산.
# 짐마다 색 개수가 달라도 최고색은 항상 동일 난이도가 되도록 정규화 (ADR-041).
VIRTUAL_SPAN = 9

# 실패 기여도 상한 (ADR-047)
FAIL_WEIGHT = 0.3
FAIL_RATIO_CAP = 0.10  # 실패 총합 <= 완등 총합의 10%

# 표시용 로그 변환 계수 (ADR-049).
# 곡률 지수 난이도로 원시 점수가 1 ~ 106,202 범위라 그대로 노출하면
# 카드 UI 가 깨지고 사용자가 읽을 수 없다.
DISPLAY_SCALE = 10.0

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


# v_scale 정규화 상한 (V0~V17). ratio = v / V_SCALE_MAX
V_SCALE_MAX = 17


def v_scale_difficulty(v: int) -> float:
    """v_scale 트랙 difficulty (ADR-046: color 와 동일한 곡률 지수).

    선형(v+1)에서 바꾼 이유:
      · color 트랙만 지수로 두면 두 트랙 스케일이 어긋난다
        (color 최대 106,202 vs v_scale 최대 18).
        compute_grade_timeline 이 둘을 합치므로 색 기록 하나가
        V17 완등 수백 개를 압도해버린다.
      · V4→V5 보다 V14→V15 가 훨씬 어렵다는 사실은 색과 동일하다.

    v / V_SCALE_MAX 로 정규화하여 color 의 ratio 와 같은 축에 둔다.
    """
    ratio = min(1.0, max(0.0, v / V_SCALE_MAX))
    vr = ratio * VIRTUAL_SPAN
    return math.exp(CURVE_A * vr + CURVE_B * vr * vr)


def color_difficulty(ratio: float) -> float:
    """color 트랙 difficulty (ADR-046: 곡률 지수).

    difficulty = exp(a·vr + b·vr²),  vr = ratio × VIRTUAL_SPAN

    선형(ratio*10+1)에서 바꾼 이유:
      · 합(sum) 집계로 전환하니 '쉬운 문제를 많이 푼 쪽'이 이겼다
        (V9 3개=27.0 < V4 10개=40.0). 선형은 개수를 못 이긴다.
      · 실제 클라이밍은 한 단계 오를수록 훨씬 어렵다.
        곡률(b>0)이 있으면 초보 구간은 완만(1.5배), 상급은 가파르다(7~9배).

    ratio 로 정규화하므로 색 개수가 다른 짐끼리도 최고색이 같은 값이 된다.
    """
    vr = ratio * VIRTUAL_SPAN
    return math.exp(CURVE_A * vr + CURVE_B * vr * vr)


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


def display_score(raw: float) -> float:
    """원시 점수 → 표시용 점수 (ADR-049).

    display = ln(raw + 1) × DISPLAY_SCALE

    원시 점수는 곡률 지수 난이도 때문에 1 ~ 106,202 범위를 갖는다.
    로그를 취하면 입문 8.9 ~ 엘리트 119 로 읽기 좋은 구간이 되고,
    클라이밍 그레이드 자체가 로그 스케일이므로 감각과도 일치한다.

    단조증가하므로 순위는 보존된다. 다만 raw 차이가 압축되므로
    (검정3개/갈색4개 = raw 6.6배 → 표시 1.2배) 정밀 비교가 필요한
    리더보드에서는 raw 를 쓸 것.
    """
    return math.log(raw + 1) * DISPLAY_SCALE if raw > 0 else 0.0


def compute_readiness(
    *,
    top_ratio: float,
    top_grade_logs: list[tuple[int, date]],
    next_ratio: float | None,
    today: date,
) -> float | None:
    """다음 등급 도전 진척도 (ADR-050).

    top_grade_logs: 최고 등급 완등 기록의 (attempts, climbed_at)

    readiness = Σ(최고등급 difficulty × time_weight) / 다음등급 difficulty

    ── 점수와 무엇이 다른가 ──
    · 점수  : 난이도 × 시간 × 효율 × 성공가중  → '최근 성과의 총량'
    · 진척도: 난이도 × 시간                    → '이 등급을 다룰 수 있는가'

    efficiency(시도횟수)를 뺀 이유: 34트라이에 겨우 깬 갈색도
    '갈색을 깼다'는 사실은 같다. 트라이 수는 '얼마나 잘 깼나'이지
    '깰 수 있나'가 아니다. 이를 넣으면 점수와 정보가 중복된다.

    시간 감쇠는 유지: 1년 전에 깬 갈색으로 '지금 검정 준비됨'이라 할 수 없다.

    최고 등급 완등만 센다: 갈색 1개 + 보라 20개인 사람은
    검정 준비가 된 것이 아니다 (모든 완등을 세면 65%, 최고만 세면 14%).

    반환: 0~100 (%). 다음 등급이 없으면(최상위 색) None.
    """
    if next_ratio is None or not top_grade_logs:
        return None

    top_difficulty = color_difficulty(top_ratio)
    next_difficulty = color_difficulty(next_ratio)
    if next_difficulty <= 0:
        return None

    accumulated = sum(
        top_difficulty * (0.5 ** (max(0, (today - climbed).days) / 60))
        for _, climbed in top_grade_logs
    )
    return min(100.0, accumulated / next_difficulty * 100)


def aggregate_score(
    *,
    successes: list[tuple[float, int, date]],
    failures: list[tuple[float, int, date]],
    today: date,
) -> tuple[float, int]:
    """종합점수 집계 (ADR-045, ADR-047).

    successes/failures: (difficulty, attempts, climbed_at) 튜플 리스트

    반환: (comprehensive_score, counted_logs)

    ── 왜 평균이 아니라 합인가 (ADR-045) ──
    평균이면 쉬운 문제나 실패를 기록할수록 점수가 떨어진다.
    실제 데이터로 검증: 빨강 완등 3개 추가 → 2.86→2.24(-21.7%),
    실패 2건 삭제 → 2.86→3.09(+7.9%). 기록 앱이 기록을 억제하고
    실패를 숨기는 것이 이득이 된다. top-N 을 자르는 것 자체가 상한이므로
    합으로 두어도 무한히 오르지 않는다.

    ── 실패 이중 상한 (ADR-047) ──
    곡률 지수 난이도(ADR-046)를 도입하는 순간 악용이 생긴다.
    핑크/갈색 난이도 비가 61배인데 실패 페널티는 3.3분의 1뿐이라,
    "최고난도에 매달렸다 떨어지기"만 반복하는 것이 최적 전략이 된다
    (아무것도 못 깬 사람이 갈색 완등자의 38.5배 점수).

      상한1: 개별 실패 <= 자기 최고 완등 난이도 기준 기여도
             → 완등이 없으면 실패 기여도는 0
      상한2: 실패 총합 <= 완등 총합 × 10%
             → 실패를 양산해도 이득이 없다 (1회든 6회든 동일)
    """
    succ_contrib = [
        compute_contribution(
            difficulty=d,
            is_success=True,
            attempts=a,
            climbed_at=c,
            today=today,
        )
        for d, a, c in successes
    ]
    succ_top = sorted(succ_contrib, reverse=True)[:TOP_N]
    succ_sum = sum(succ_top)

    if not successes:
        # 상한1: 완등 기록이 없으면 실패 기여도도 0
        return 0.0, 0

    cap_difficulty = max(d for d, _, _ in successes)
    fail_contrib: list[float] = []
    for d, a, c in failures:
        raw = compute_contribution(
            difficulty=d, is_success=False, attempts=a, climbed_at=c, today=today
        )
        capped = compute_contribution(
            difficulty=cap_difficulty,
            is_success=False,
            attempts=a,
            climbed_at=c,
            today=today,
        )
        fail_contrib.append(min(raw, capped))

    # 상한2: 실패 총합은 완등 총합의 일정 비율을 넘지 못한다
    fail_sum = min(sum(fail_contrib), succ_sum * FAIL_RATIO_CAP)

    counted = len(succ_top) + min(len(fail_contrib), TOP_N)
    return succ_sum + fail_sum, counted


class GradeService:
    def __init__(self, repo: GradeRepository, session=None):
        self.repo = repo
        # 쓰기 트랜잭션 커밋용 (get_session 은 자동 커밋 안 함). 산정은 읽기라 불필요.
        self.session = session

    # ── 산정 (구현 2~5) ──

    async def compute_v_scale_grade(self, user_id: UUID) -> VScaleGrade:
        logs = await self.repo.list_user_logs_for_grading(user_id, "v_scale")
        today = datetime.now(UTC).date()

        successes: list[tuple[float, int, date]] = []
        failures: list[tuple[float, int, date]] = []
        success_v: list[int] = []
        for log in logs:
            v = parse_v_scale(log.grade_raw)
            if v is None:
                continue
            entry = (v_scale_difficulty(v), log.attempts, log.climbed_at)
            if log.is_success:
                successes.append(entry)
                success_v.append(v)
            else:
                failures.append(entry)

        raw_score, counted = aggregate_score(
            successes=successes, failures=failures, today=today
        )
        comprehensive_score = display_score(raw_score)

        top_rating = max(success_v) if success_v else None
        top_rating_label = f"V{top_rating}" if top_rating is not None else None

        return VScaleGrade(
            comprehensive_score=comprehensive_score,
            top_rating=top_rating,
            top_rating_label=top_rating_label,
            counted_logs=counted,
        )

    async def compute_grade_timeline(
        self, user_id: UUID, weeks: int = 12
    ) -> list[dict]:
        """주별 종합 그레이드 점수 추이 (v_scale + color 통합).

        v_scale·color 두 트랙 로그를 하나의 난이도 스케일로 합쳐
        각 주말(스냅샷) 시점의 종합 점수를 계산한다.
        - v_scale: v_scale_difficulty(v)   (ADR-046 곡률 지수)
        - color:   color_difficulty(ratio) (동일 곡률, 같은 축)
        - 각 시점 이후 로그 제외, today=스냅샷일 로 반감기 반영
        → 실력 성장 곡선. 안 오르면 반감기로 서서히 하락.

        두 트랙을 합칠 수 있는 이유(ADR-046): 둘 다 ratio 로 정규화한 뒤
        같은 곡률 지수를 태우므로 최고 등급이 동일한 값(≈106,202)이 된다.
        선형 시절에는 v_scale 최대 18 / color 최대 11 로 축이 달랐다.

        반환: [{"date", "score", "count"}, ...]
        """
        # (difficulty, climbed_at, is_success, attempts) 로 통일해 미리 파싱
        parsed: list[tuple[float, date, bool, int]] = []

        # 1) v_scale 로그
        v_logs = await self.repo.list_user_logs_for_grading(user_id, "v_scale")
        for log in v_logs:
            v = parse_v_scale(log.grade_raw)
            if v is None:
                continue
            parsed.append(
                (
                    v_scale_difficulty(v),
                    log.climbed_at,
                    log.is_success,
                    log.attempts,
                )
            )

        # 2) color 로그 (짐 색 순서 → ratio → difficulty)
        c_logs = await self.repo.list_user_logs_for_grading(user_id, "color")
        systems = await self.repo.get_systems_map(
            log.gym_name for log in c_logs if log.gym_name
        )
        for log in c_logs:
            system = systems.get(log.gym_name) if log.gym_name else None
            if system is None:
                continue
            rank = self.repo.color_to_rank(system, log.grade_raw)
            if rank is None:
                continue
            ratio = self.repo.rank_to_ratio(system, rank)
            parsed.append(
                (
                    color_difficulty(ratio),
                    log.climbed_at,
                    log.is_success,
                    log.attempts,
                )
            )

        today = datetime.now(UTC).date()
        points: list[dict] = []
        for i in range(weeks):
            snapshot = today - timedelta(weeks=(weeks - 1 - i))

            # 스냅샷 시점까지의 로그만, 완등/실패를 분리해 공용 집계에 위임
            successes = [
                (diff, att, climbed)
                for (diff, climbed, succ, att) in parsed
                if climbed <= snapshot and succ
            ]
            failures = [
                (diff, att, climbed)
                for (diff, climbed, succ, att) in parsed
                if climbed <= snapshot and not succ
            ]
            raw, counted = aggregate_score(
                successes=successes, failures=failures, today=snapshot
            )
            score = display_score(raw)

            points.append(
                {
                    "date": snapshot.isoformat(),
                    "score": round(score, 2),
                    "count": counted,
                }
            )
        return points

    async def compute_profile_stats(self, user_id: UUID) -> dict:
        """프로필용 클라이머 통계 요약.

        - success_count: 총 완등 수
        - total_count: 전체 기록 수 (완등+시도)
        - current_score: 현재 실력 지수 (통합 종합 점수, 반감기 반영)
        - top_grade: 최고 등급 라벨 (v_scale 우선, 없으면 color)
        반환: {success_count, total_count, current_score, top_grade}
        """
        success_count = await self.repo.count_success(user_id)
        total_count = await self.repo.count_total(user_id)

        # 현재 실력 지수 = timeline 최근값 (없으면 0)
        timeline = await self.compute_grade_timeline(user_id, weeks=1)
        current_score = timeline[-1]["score"] if timeline else 0.0

        # 최고 등급: v_scale 우선(표준, 짐 무관), 없으면 color(짐 기준 명시)
        # color 는 짐마다 색 난이도가 달라 반드시 기준 짐을 함께 표시해야 함
        v_grade = await self.compute_v_scale_grade(user_id)
        top_grade = v_grade.top_rating_label  # 예: "V5"
        top_grade_gym = None  # v_scale 은 짐 무관
        top_grade_system = "v_scale"
        if top_grade is None:
            color_grade = await self.compute_color_grade(user_id)
            top_grade = color_grade.top_rating_label  # 예: "보"
            top_grade_gym = color_grade.base_gym  # 예: "서울숲클라이밍"
            top_grade_system = "color"

        return {
            "success_count": success_count,
            "total_count": total_count,
            "current_score": current_score,
            "top_grade": top_grade,
            "top_grade_gym": top_grade_gym,
            "top_grade_system": top_grade_system,
        }

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

        successes: list[tuple[float, int, date]] = []
        failures: list[tuple[float, int, date]] = []
        gym_counter: Counter[str] = Counter()
        success_ratios: list[float] = []
        # (ratio, attempts, climbed_at) — 진척도용 (ADR-050)
        success_details: list[tuple[float, int, date]] = []
        for log in logs:
            system = systems.get(log.gym_name) if log.gym_name else None
            if system is None:
                continue
            rank = self.repo.color_to_rank(system, log.grade_raw)
            if rank is None:
                continue
            ratio = self.repo.rank_to_ratio(system, rank)
            entry = (color_difficulty(ratio), log.attempts, log.climbed_at)
            if log.is_success:
                successes.append(entry)
                success_ratios.append(ratio)
                success_details.append((ratio, log.attempts, log.climbed_at))
            else:
                failures.append(entry)
            gym_counter[log.gym_name] += 1

        raw_score, counted = aggregate_score(
            successes=successes, failures=failures, today=today
        )
        comprehensive_score = display_score(raw_score)

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
        next_grade_label: str | None = None
        readiness_pct: float | None = None
        if success_ratios and base_system is not None:
            top_ratio = max(success_ratios)
            top_rating_label = self.repo.ratio_to_color(base_system, top_ratio)

            # 다음 등급 진척도 (ADR-050)
            order = base_system.color_order
            n = len(order)
            top_rank = order.index(top_rating_label)
            if top_rank < n - 1:  # 최상위 색이면 다음 등급이 없다
                next_grade_label = order[top_rank + 1]
                next_ratio = self.repo.rank_to_ratio(base_system, top_rank + 1)
                # 최고 등급 완등 기록만 센다 (ADR-050).
                # 갈색 1개 + 보라 20개인 사람은 검정 준비가 된 것이 아니다.
                top_logs = [
                    (att, climbed)
                    for r, att, climbed in success_details
                    if abs(r - top_ratio) < 1e-9
                ]
                readiness_pct = compute_readiness(
                    top_ratio=top_ratio,
                    top_grade_logs=top_logs,
                    next_ratio=next_ratio,
                    today=today,
                )

        return ColorGrade(
            comprehensive_score=comprehensive_score,
            base_gym=resolved_base_gym,
            top_rating_label=top_rating_label,
            counted_logs=counted,
            next_grade_label=next_grade_label,
            readiness_pct=readiness_pct,
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
        self,
        *,
        gym_name: str,
        color_order: list[str],
        user_id: UUID,
        is_admin: bool = False,
        is_official: bool = False,
    ) -> GymGradeSystem:
        """짐 색체계 등록.

        일반 사용자: is_official=False, created_by=user (개인 등록).
        admin: is_official 을 지정 가능 (공식 암장 등록). created_by 는
        감사용으로 등록한 admin id 를 남긴다 (시드는 NULL).

        gym_name 중복이면 GymGradeSystemAlreadyExists(409).
        """
        existing = await self.repo.get_by_gym_name(gym_name)
        if existing is not None:
            raise GymGradeSystemAlreadyExists(gym_name)
        # 공식 등록은 admin 만 — 비admin 이 is_official=True 보내도 무시
        official = is_official and is_admin
        system = await self.repo.create(
            gym_name=gym_name,
            color_order=color_order,
            created_by=user_id,
            is_official=official,
        )
        await self.session.commit()
        await self.session.refresh(system)  # commit 후 expire 방지 (응답 직렬화용)
        return system


    async def update_gym_system(
        self,
        *,
        system_id: UUID,
        color_order: list[str],
        user_id: UUID,
        is_admin: bool = False,
    ) -> GymGradeSystem:
        """color_order 수정.

        일반 사용자: 본인 등록분(비공식)만. admin: 공식·타인 등록분 포함 전체.
        """
        system = await self.repo.get_by_id(system_id)
        if system is None:
            raise GymGradeSystemNotFoundById(str(system_id))
        if not is_admin and (
            system.is_official or system.created_by != user_id
        ):
            raise GymGradeSystemForbidden(str(system_id))
        updated = await self.repo.update_color_order(system, color_order)
        await self.session.commit()
        await self.session.refresh(updated)  # commit 후 expire 방지 (응답 직렬화용)
        return updated

    async def delete_gym_system(
        self, *, system_id: UUID, user_id: UUID, is_admin: bool = False
    ) -> None:
        """삭제.

        일반 사용자: 본인 등록분(비공식)만. admin: 공식·타인 등록분 포함 전체.
        """
        system = await self.repo.get_by_id(system_id)
        if system is None:
            raise GymGradeSystemNotFoundById(str(system_id))
        if not is_admin and (
            system.is_official or system.created_by != user_id
        ):
            raise GymGradeSystemForbidden(str(system_id))
        await self.repo.delete(system)
        await self.session.commit()
