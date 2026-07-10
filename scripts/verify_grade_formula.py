"""그레이드 재설계 검증 (ADR-045~048) — 순수 계산, DB 불필요.

service.py 의 함수를 직접 불러 악용 방어와 사용자 체감을 확인한다.
실행: uv run python -m scripts.verify_grade_formula
"""

import sys
from datetime import date, timedelta

from app.domain.grade.service import (
    aggregate_score,
    color_difficulty,
    v_scale_difficulty,
)

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f"  {detail}" if detail else ""))


TODAY = date(2026, 7, 9)
# 서울숲 10색: 빨주노초파남보갈검핑 → rank/9 = ratio
R = {c: i / 9 for i, c in enumerate(
    ["빨", "주", "노", "초", "파", "남", "보", "갈", "검", "핑"])}


def score(logs):
    """logs: [(색, 완등여부, 시도, 날짜)]"""
    succ = [(color_difficulty(R[c]), a, d) for c, ok, a, d in logs if ok]
    fail = [(color_difficulty(R[c]), a, d) for c, ok, a, d in logs if not ok]
    s, _ = aggregate_score(successes=succ, failures=fail, today=TODAY)
    return s


def main() -> int:
    print("\n[난이도 곡률 (ADR-046)]")
    d_gal, d_geom = color_difficulty(R["갈"]), color_difficulty(R["검"])
    check("갈→검 배율이 7배 (사용자 체감)",
          abs(d_geom / d_gal - 7.0) < 0.05, f"{d_geom/d_gal:.2f}배")
    d_red, d_org = color_difficulty(R["빨"]), color_difficulty(R["주"])
    check("빨→주 배율이 1.5배 (초보 완만)",
          abs(d_org / d_red - 1.5) < 0.02, f"{d_org/d_red:.2f}배")
    check("단계 배율이 위로 갈수록 커진다",
          (d_geom / d_gal) > (d_org / d_red))

    print("\n[두 트랙 스케일 일치 (ADR-046)]")
    check("v_scale 최고(V17) == color 최고(ratio 1.0)",
          abs(v_scale_difficulty(17) - color_difficulty(1.0)) < 1.0,
          f"{v_scale_difficulty(17):,.0f} vs {color_difficulty(1.0):,.0f}")

    print("\n[집계: 평균 → 합 (ADR-045)]")
    base = [("갈", True, 3, TODAY)] * 4
    s_base = score(base)
    s_easy = score(base + [("빨", True, 1, TODAY)] * 3)
    check("쉬운 문제 기록해도 손해 없음", s_easy >= s_base - 1e-9,
          f"{s_base:,.0f} → {s_easy:,.0f}")
    s_more = score(base + [("갈", True, 3, TODAY)])
    check("같은 난이도 추가 완등 시 상승 (근지구력)", s_more > s_base,
          f"+{(s_more/s_base-1)*100:.0f}%")

    print("\n[실패 이중 상한 (ADR-047)]")
    s_honest = score(base + [("검", False, 1, TODAY)])
    check("검정 실패해도 점수 상승 (도전 보상)", s_honest > s_base,
          f"+{(s_honest/s_base-1)*100:.1f}%")
    s_hide = score(base)
    check("실패 숨기면 손해 (정직이 이득)", s_hide < s_honest)

    s_spam6 = score(base + [("검", False, 1, TODAY)] * 6)
    check("실패 양산해도 이득 없음 (상한2)",
          abs(s_spam6 - s_honest) < 1e-6, f"1회={s_honest:,.0f} 6회={s_spam6:,.0f}")

    s_pink_only = score([("핑", False, 1, TODAY)] * 10)
    check("완등 없이 최고난도 실패만 → 0점 (상한1)",
          s_pink_only == 0.0, f"{s_pink_only:,.0f}")

    s_pink_spam = score(base + [("핑", False, 1, TODAY)] * 6)
    check("자기 수준 밖 실패는 상한에 걸림",
          abs(s_pink_spam - s_honest) < 1e-6,
          f"핑크실패6={s_pink_spam:,.0f}")

    print("\n[완등이 항상 최고]")
    s_send = score(base + [("검", True, 5, TODAY)])
    check("검정 완등 > 검정 실패", s_send > s_honest,
          f"완등 {s_send:,.0f} vs 실패 {s_honest:,.0f}")
    check("검정 완등이 갈색 추가보다 큼", s_send > s_more)

    print("\n[시간 감쇠]")
    old = [("갈", True, 3, TODAY - timedelta(days=365))] * 4
    check("1년 전 기록은 크게 감쇠", score(old) < s_base * 0.05,
          f"잔존 {score(old)/s_base*100:.1f}%")

    total, passed = len(_results), sum(_results)
    print(f"\n{'─' * 44}")
    if passed == total:
        print(f"{GREEN}ALL PASS{RESET}  ({passed}/{total})")
        return 0
    print(f"{RED}FAILED{RESET}  ({passed}/{total})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
