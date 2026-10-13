"""하루 점수 평균(ADR-068)이 서비스 전체에 적용됐는지 검증 (rollback — 실제 변경 없음).

- V/컬러 트랙 종합점수, 주별 추이(두 트랙 통합 + 90일 창), 프로필 점수가 새 산식
- 최고 등급·진척도는 기존 의미 유지
- 암장 랭킹은 기존 top-N 합 산식(aggregate_score) 그대로

실행:
    uv run python -m scripts.verify_grade_daily_average_service
"""

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.domain.climbing.repository import ClimbingRepository
from app.domain.grade.repository import GradeRepository
from app.domain.grade.service import (
    GradeService,
    aggregate_score,
    color_difficulty,
    display_score,
    local_today,
)
from app.domain.users.models import User
from app.infra.db.engine import close_engine, init_engine

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f"  ({detail})" if detail else ""))


def close(a: float, b: float) -> bool:
    return abs(a - b) < 0.01


COLORS = ["빨", "주", "노", "초", "파", "남", "보", "핑", "갈", "검"]  # 10색: 한 단계 100점


async def run(session: AsyncSession) -> None:
    crepo = ClimbingRepository(session)
    grepo = GradeRepository(session)
    service = GradeService(grepo, session)
    today = local_today()  # 서비스와 같은 기준일(KST)

    uid = uuid.uuid4().hex[:8]
    user = User(
        email=f"dailyavg_{uid}@clog.test",
        nickname=f"dailyavg_{uid}",
        auth_provider="local",
        email_verified=True,
        is_public=True,
    )
    session.add(user)
    await session.flush()
    gym = await grepo.create(
        gym_name=f"일평균짐_{uid}",
        color_order=COLORS,
        created_by=user.id,
        is_official=True,
        brand_name=None,
    )
    await session.flush()

    async def log(system: str, grade: str, ok: bool, attempts: int, days_ago: int) -> None:
        await crepo.create(
            user_id=user.id,
            grade_raw=grade,
            grade_system=system,
            gym_name=gym.gym_name if system == "color" else "불암산",
            categories=[],
            attempts=attempts,
            is_success=ok,
            visibility="public",
            climbed_at=today - timedelta(days=days_ago),
        )

    # 컬러: 2일 전 파1트·파3트·남5트 → (420+400+500)/3 = 440
    #       5일 전 초2트×3 → 300,  100일 전 검1트(90일 창 밖), 1일 전 핑 실패(무시)
    for g, a in [("파", 1), ("파", 3), ("남", 5)]:
        await log("color", g, True, a, 2)
    for _ in range(3):
        await log("color", "초", True, 2, 5)
    await log("color", "검", True, 1, 100)
    await log("color", "핑", False, 1, 1)
    # V: 1일 전 V5 1트·V3 2트 → (5/17·900+20 + 3/17·900)/2
    await log("v_scale", "V5", True, 1, 1)
    await log("v_scale", "V3", True, 2, 1)
    # 미등록 암장 기록(등급 해석 불가): 점수엔 없고 활동량(잔디)에는 들어가야 함
    await crepo.create(
        user_id=user.id, grade_raw="보", grade_system="color",
        gym_name=f"미등록짐_{uid}", categories=[], attempts=1, is_success=True,
        visibility="public", climbed_at=today - timedelta(days=3),
    )
    await session.flush()

    v_day = (5 / 17 * 900 + 20 + 3 / 17 * 900) / 2

    print("\n[컬러 트랙]")
    color = await service.compute_color_grade(user.id)
    check("종합점수 = 최근 운동일 하루 점수 평균 (440, 300)",
          close(color.comprehensive_score, 370.0), f"{color.comprehensive_score:.2f}")
    check("반영 운동일 2일 / 완등 6건 (90일 밖·실패 제외)",
          color.counted_days == 2 and color.counted_logs == 6,
          f"{color.counted_days}일 {color.counted_logs}건")
    check("최고 등급은 기존 의미(실제 최고 완등 = 검) 유지", color.top_rating_label == "검")

    print("\n[V 트랙]")
    vg = await service.compute_v_scale_grade(user.id)
    check("V 종합점수 = 같은 ratio 축 환산", close(vg.comprehensive_score, v_day),
          f"{vg.comprehensive_score:.2f} vs {v_day:.2f}")
    check("V 최고 등급 V5 + 다음 등급 진척도 유지",
          vg.top_rating_label == "V5" and vg.next_grade_label == "V6"
          and vg.readiness_pct is not None)

    print("\n[주별 추이 (두 트랙 통합)]")
    tl = await service.compute_grade_timeline(user.id, weeks=4)
    scores = [p["score"] for p in tl]
    merged = (v_day + 440 + 300) / 3
    check("마지막 점(오늘) = V 하루 + 컬러 이틀의 평균",
          close(scores[-1], round(merged, 2)), f"{scores[-1]} vs {merged:.2f}")
    check("3주·2주 전: 100일 전 검 1트가 90일 창 안 → 920",
          scores[0] == 920 and scores[1] == 920, f"{scores[:2]}")
    check("1주 전: 그 기록이 창 밖이고 다른 기록 없음 → 0", scores[2] == 0, f"{scores[2]}")
    check("days 필드 = 반영 운동일 수", tl[-1]["days"] == 3, f"{tl[-1]['days']}")

    print("\n[프로필]")
    stats = await service.compute_profile_stats(user.id)
    check("current_score = 추이 마지막 점", stats["current_score"] == scores[-1],
          f"{stats['current_score']}")

    print("\n[일별 활동 (잔디)]")
    act = await service.compute_daily_activity(user.id, days=28)
    by = {a["date"]: a for a in act}
    d = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    check("기록 있는 날만, 날짜 오름차순 (100일 전은 기간 밖)",
          [a["date"] for a in act] == [d(5), d(3), d(2), d(1)], f"{list(by)}")
    check("1일 전: 기록 3(핑 실패 포함)·완등 2·하루 점수 = V 하루 점수",
          by[d(1)]["total"] == 3 and by[d(1)]["success"] == 2
          and close(by[d(1)]["day_score"], round(v_day, 1)), f"{by[d(1)]}")
    check("2일 전 하루 점수 440 / 5일 전 300",
          by[d(2)]["day_score"] == 440 and by[d(5)]["day_score"] == 300)
    check("미등록 암장 기록: 활동량엔 포함, 하루 점수는 None",
          by[d(3)]["total"] == 1 and by[d(3)]["day_score"] is None, f"{by[d(3)]}")
    from app.main import app as fastapi_app
    paths = {getattr(r, "path", "") for r in fastapi_app.routes}
    check("라우트 GET /me/grade/activity 등록", "/me/grade/activity" in paths)

    print("\n[암장 랭킹 — 기존 산식 유지]")
    ranking = await service.compute_gym_ranking(gym.gym_name)
    me = next((e for e in ranking.entries if e.user.id == user.id), None)
    r = gym.color_order
    succ = [(color_difficulty(r.index(g) / 9), a, today - timedelta(days=d))
            for g, a, d in [("파", 1, 2), ("파", 3, 2), ("남", 5, 2),
                            ("초", 2, 5), ("초", 2, 5), ("초", 2, 5), ("검", 1, 100)]]
    fail = [(color_difficulty(r.index("핑") / 9), 1, today - timedelta(days=1))]
    raw, _ = aggregate_score(successes=succ, failures=fail, today=today)
    check("랭킹 점수 = display_score(aggregate_score) (옛 top-N 합)",
          me is not None and close(me.score, display_score(raw)),
          f"{me.score if me else None:.2f} vs {display_score(raw):.2f}")


async def main() -> int:
    engine = init_engine()
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
            autoflush=False,
        )
        try:
            await run(session)
        finally:
            await session.close()
            await outer.rollback()  # fixture 포함 전부 되돌림 (DB 무변경)
    await close_engine()

    total = len(_results)
    passed = sum(_results)
    print(f"\n{'─' * 40}")
    if passed == total:
        print(f"{GREEN}ALL PASS{RESET}  ({passed}/{total})  — DB 변경 없음(rollback)")
        return 0
    print(f"{RED}FAILED{RESET}  ({passed}/{total})")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
