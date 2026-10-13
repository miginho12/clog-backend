"""하루 점수 평균 종합점수 검증 (ADR-068) — 순수 계산, DB 불필요.

top-N 합(ADR-045)에서 새 등급 첫 완등이 반영되지 않던 문제를 고친
compute_day_score / aggregate_daily_average 의 성질을 확인한다.
실행: uv run python -m scripts.verify_grade_daily_average
"""

import sys
from datetime import UTC, date, datetime, timedelta

from app.domain.grade.service import (
    FLASH_BONUS,
    MAX_AGE_DAYS,
    aggregate_daily_average,
    compute_day_score,
    grade_points,
    local_today,
)

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f"  {detail}" if detail else ""))


TODAY = date(2026, 10, 14)
COLORS = ["빨", "주", "노", "초", "파", "남", "보", "핑", "갈", "검"]  # 10색
R = {c: i / 9 for i, c in enumerate(COLORS)}


def day(*sends: tuple[str, int]) -> float | None:
    return compute_day_score([(R[c], a) for c, a in sends])


def agg(logs: list[tuple[str, bool, int, date]]) -> tuple[float, int, int]:
    return aggregate_daily_average(
        [(R[c], ok, a, d) for c, ok, a, d in logs], today=TODAY
    )


def session(d: date, *main: tuple[str, bool, int]) -> list[tuple[str, bool, int, date]]:
    """워밍업(빨·주·노 1트) + 메인 기록"""
    warm = [("빨", True, 1, d), ("주", True, 1, d), ("노", True, 1, d)]
    return warm + [(c, ok, a, d) for c, ok, a in main]


def main() -> int:
    print("\n[등급 환산 단위]")
    steps = [grade_points(R[COLORS[i + 1]]) - grade_points(R[COLORS[i]]) for i in range(9)]
    check("10색 암장 한 단계 = 항상 100점 (위로 가도 완만해지지 않음)",
          all(abs(s - 100) < 1e-9 for s in steps), f"{steps[0]:.0f} … {steps[-1]:.0f}")
    check("최고 난이도 = 900 (색 개수 무관)",
          grade_points(1.0) == 900 and abs(grade_points(7 / 7) - 900) < 1e-9)
    check("8색 암장 한 단계 ≈ 128.6점", abs(grade_points(1 / 7) - 900 / 7) < 1e-9)

    print("\n[하루 점수]")
    base = day(("파", 2), ("파", 2), ("파", 2))
    check("하루 점수 = 상위 3개 평균", base == 400, f"{base}")
    check("워밍업을 기록해도 손해 없음",
          day(("빨", 1), ("주", 1), ("파", 2), ("파", 2), ("파", 2)) == base)
    check("1트 완등은 +20 보너스",
          day(("파", 1), ("파", 1), ("파", 1)) == 400 + FLASH_BONUS)
    check("시도 횟수로 나누지 않음 (15트 = 2트)",
          day(("남", 15), ("파", 2), ("파", 2)) == day(("남", 2), ("파", 2), ("파", 2)))
    with_first = day(("남", 15), ("파", 2), ("파", 2))
    check("새 등급 첫 완등(15트)이 그날 점수에 바로 반영",
          with_first is not None and base is not None and with_first > base,
          f"{base:.0f} → {with_first:.0f}")
    check("완등이 없는 날은 점수 없음", compute_day_score([]) is None)

    print("\n[종합 = 최근 10일 중 잘한 7일 평균]")
    good = [
        rec
        for i in range(7)
        for rec in session(TODAY - timedelta(days=i * 3), ("파", True, 2), ("파", True, 2))
    ]
    s_good, days_good, logs_good = agg(good)
    bad = [("빨", True, 1, TODAY - timedelta(days=1 + i * 3)) for i in range(3)]
    s_mixed, days_mixed, _ = agg(good + bad)
    check("컨디션 나쁜 날 3일을 기록해도 점수 그대로",
          abs(s_good - s_mixed) < 1e-9 and days_mixed == 7, f"{s_good:.0f} / {s_mixed:.0f}")
    check("counted_days / counted_logs", days_good == 7 and logs_good == 21,
          f"{days_good}일 {logs_good}건")
    fails = [("핑", False, 1, TODAY - timedelta(days=i)) for i in range(20)]
    check("실패를 아무리 쌓아도 점수 불변 (ADR-047 악용 원천 차단)",
          agg(good + fails)[0] == s_good)
    check("완등이 하나도 없으면 (0, 0, 0)", agg(fails) == (0.0, 0, 0))
    old = [("갈", True, 1, TODAY - timedelta(days=MAX_AGE_DAYS + d)) for d in range(5)]
    check(f"{MAX_AGE_DAYS}일 넘은 기록은 제외 (장기 휴식 = 점수 없음)",
          agg(old) == (0.0, 0, 0))
    edge = [("갈", True, 1, TODAY - timedelta(days=MAX_AGE_DAYS - 1))]
    check(f"{MAX_AGE_DAYS - 1}일 전 기록은 포함", agg(edge)[1] == 1)
    future = [("검", True, 1, TODAY + timedelta(days=1))]
    check("today 이후 기록은 제외 (추이 스냅샷용)", agg(good + future)[0] == s_good)

    print("\n[날짜 기준 = 한국 시간]")
    kst = (datetime.now(UTC) + timedelta(hours=9)).date()
    check("local_today() == UTC+9 날짜 (KST 00~09시에도 한국 날짜)",
          local_today() == kst, f"{local_today()} (UTC {datetime.now(UTC).date()})")

    print("\n[성장 시나리오: 파랑 → 남색, 주 2회 16세션]")
    plan = [
        [("파", True, 1), ("파", True, 2), ("파", True, 3), ("남", False, 6)],
        [("파", True, 1), ("파", True, 1), ("남", False, 5), ("남", False, 4)],
        [("파", True, 1), ("파", True, 2), ("파", True, 1), ("남", True, 9)],
        [("파", True, 1), ("남", False, 3), ("남", True, 6), ("파", True, 1)],
        [("남", True, 4), ("파", True, 1), ("남", True, 3), ("파", True, 1)],
        [("남", True, 2), ("남", True, 3), ("남", False, 5), ("파", True, 1)],
        [("남", True, 1), ("남", True, 2), ("남", True, 3), ("보", False, 8)],
        [("남", True, 1), ("남", True, 2), ("남", True, 1), ("남", True, 4)],
    ]
    start = TODAY - timedelta(days=55)
    logs: list[tuple[str, bool, int, date]] = []
    scores: list[float] = []
    d = start
    for i in range(16):
        logs += session(d, *plan[i // 2])
        s, _, _ = aggregate_daily_average(
            [(R[c], ok, a, dd) for c, ok, a, dd in logs], today=d)
        scores.append(s)
        d += timedelta(days=3 if i % 2 == 0 else 4)
    print("     " + " ".join(f"{s:.0f}" for s in scores))
    check("남색 첫 완등 구간(5~8회) 상승 ≥ 20점 (기존 방식 +1.2)",
          scores[7] - scores[3] >= 20, f"+{scores[7] - scores[3]:.0f}")
    check("마지막엔 남색 수준(≥480)에 도달", scores[-1] >= 480, f"{scores[-1]:.0f}")
    check("후반(9회 이후) 단조 증가",
          all(b >= a for a, b in zip(scores[8:], scores[9:], strict=False)))

    passed, total = sum(_results), len(_results)
    print()
    if passed == total:
        print(f"{GREEN}ALL PASS{RESET}  ({passed}/{total})")
        return 0
    print(f"{RED}FAIL{RESET}  ({passed}/{total})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
