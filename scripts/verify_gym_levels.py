"""암장별 내 수준(GET /me/grade/gyms) 검증 (rollback — 실제 변경 없음).

- 가 본 암장 = 실제 최고 완등 색·그 암장 기록만의 진척도, 안 가 본 암장 = 추정만
- 추정은 보정치 반영 후 위쪽을 마지막에만 자른다 (먼저 자르면 낮게 환산되던 문제)
- 기본 기준 암장 = 최근 90일 가장 많이 기록한 암장

실행:
    uv run python -m scripts.verify_gym_levels
"""

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.domain.climbing.repository import ClimbingRepository
from app.domain.grade.repository import GradeRepository
from app.domain.grade.service import GradeService, compute_readiness, local_today
from app.domain.users.models import User
from app.infra.db.engine import close_engine, init_engine
from app.main import app as fastapi_app

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f"  ({detail})" if detail else ""))


C10 = ["빨", "주", "노", "초", "파", "남", "보", "핑", "갈", "검"]
C11 = ["흰", "노", "주", "초", "파", "빨", "핑", "보", "회", "갈", "검"]


async def run(session: AsyncSession) -> None:
    crepo = ClimbingRepository(session)
    grepo = GradeRepository(session)
    service = GradeService(grepo, session)
    today = local_today()
    t = uuid.uuid4().hex[:6]

    def make_user(nick: str) -> User:
        return User(email=f"lv_{nick}_{t}@clog.test", nickname=f"lv_{nick}_{t}",
                    auth_provider="local", email_verified=True, is_public=True)

    user, empty = make_user("u"), make_user("e")
    session.add_all([user, empty])
    await session.flush()

    names = {k: f"{k}암장_{t}" for k in ["A", "B", "C", "E"]}
    sys_a = await grepo.create(gym_name=names["A"], color_order=C10, created_by=None,
                               is_official=True, difficulty_offset=0.0)
    sys_b = await grepo.create(gym_name=names["B"], color_order=C10, created_by=None,
                               is_official=True, difficulty_offset=0.15)
    sys_c = await grepo.create(gym_name=names["C"], color_order=C11, created_by=None,
                               is_official=True, difficulty_offset=0.3)
    await grepo.create(gym_name=names["E"], color_order=C10, created_by=user.id,
                       is_official=False)  # 사용자 등록(비공식) 암장
    await session.flush()

    async def log(gym: str, grade: str, ok: bool, attempts: int, days_ago: int) -> None:
        await crepo.create(user_id=user.id, grade_raw=grade, grade_system="color",
                           gym_name=names[gym], categories=[], attempts=attempts,
                           is_success=ok, visibility="public",
                           climbed_at=today - timedelta(days=days_ago))

    # A: 최근 4건(남 2·파 1 완등, 보 실패) + 목록에 없는 색 1건 + 100일 전 10건 → 전체 15, 최근 4
    await log("A", "남", True, 1, 3)
    await log("A", "남", True, 3, 10)
    await log("A", "파", True, 1, 3)
    await log("A", "보", False, 5, 3)
    await log("A", "금", True, 1, 100)  # 색 목록에 없음: 기록 수엔 포함, 완등 판정 제외
    for _ in range(10):
        await log("A", "초", True, 1, 100)
    # B: 최근 5건, 최고 = 검(최고 색) → 보정 후 1.0 + 0.15 = 1.15
    for g in ["검", "갈", "갈", "보", "보"]:
        await log("B", g, True, 2, 5)
    # E: 비공식 암장, 실패 1건만
    await log("E", "파", False, 2, 1)
    await session.flush()

    res = await service.compute_gym_levels(user.id)
    mine = [g for g in res.gyms if g.gym_name.endswith(t)]
    by = {g.gym_name: g for g in mine}
    a, b, c, e = (by[names[k]] for k in "ABCE")

    print("\n[기본 기준 암장 · 순서]")
    check("기본 기준 암장 = 최근 90일 가장 많이 기록한 B (전체 기록은 A가 더 많아도)",
          res.default_base_gym == names["B"], f"{res.default_base_gym}")
    order = [g.gym_name for g in mine]
    check("가 본 암장(최근 많은 순) → 안 가 본 암장", order == [names[k] for k in "BAEC"], f"{order}")
    check("비공식이라도 가 본 암장은 포함, 안 가 본 비공식 암장은 제외",
          e.visited and all(g.visited or g.gym_name != names["E"] for g in mine))

    print("\n[가 본 암장 = 실제 기록]")
    check("A 기록 수 15 (실패·목록에 없는 색 포함), 최근 90일 4",
          a.record_count == 15 and a.recent_count == 4, f"{a.record_count}/{a.recent_count}")
    check("A 최고 = 실제 완등 최고 색 '남' (실패한 '보' 아님), 다음 = '보'",
          a.top_label == "남" and a.next_grade_label == "보", f"{a.top_label}→{a.next_grade_label}")
    expected = compute_readiness(
        top_ratio=5 / 9,
        top_grade_logs=[(1, today - timedelta(days=3)), (3, today - timedelta(days=10))],
        next_ratio=6 / 9, today=today,
    )
    check("A 진척도 = A의 '남' 완등 2건만으로 계산",
          a.readiness_pct is not None and expected is not None
          and abs(a.readiness_pct - expected) < 1e-9, f"{a.readiness_pct} vs {expected}")
    check("B 최고 '검'(최고 색) → 다음 등급·진척도 없음",
          b.top_label == "검" and b.next_grade_label is None and b.readiness_pct is None)
    check("E 실패만 → visited 이지만 최고·진척도 없음",
          e.visited and e.top_label is None and e.readiness_pct is None)
    check("마지막 방문일", b.last_climbed_at == today - timedelta(days=5)
          and a.last_climbed_at == today - timedelta(days=3))

    print("\n[안 가 본 암장 = 추정]")
    check("C 안 가 봄: 기록 0, 실제 최고 없음", not c.visited and c.record_count == 0
          and c.top_label is None and c.last_climbed_at is None)
    # 최댓값 = B 검 1.0 + 0.15 = 1.15 → C(+0.3) 0.85 × 10 = 8.5 → 반올림 9 = '갈'
    # (예전처럼 1.0 에서 먼저 자르면 0.7 × 10 = 7 → '보' 로 낮게 나옴)
    check("C 추정 = 위쪽을 마지막에만 잘라 '갈' (먼저 자르면 '보')",
          c.estimated_top_label == C11[9], f"{c.estimated_top_label}")
    check("보정치 0 암장 A 추정 = 최고 색 (1.15 → 1.0)", a.estimated_top_label == "검")
    check("응답에 색 순서·보정치 포함 (프론트 게이지·평소 환산용)",
          c.color_order == C11 and c.difficulty_offset == 0.3 and b.difficulty_offset == 0.15)

    print("\n[기록 없는 사용자]")
    r0 = await service.compute_gym_levels(empty.id)
    m0 = [g for g in r0.gyms if g.gym_name.endswith(t)]
    check("기본 기준 암장 없음, 전부 안 가 봄, 추정 없음",
          r0.default_base_gym is None and all(not g.visited for g in m0)
          and all(g.estimated_top_label is None for g in m0)
          and {g.gym_name for g in m0} == {names["A"], names["B"], names["C"]}, f"{len(m0)}")

    print("\n[기존 동작 무변경]")
    old = await service.compute_color_grade(user.id, base_gym=names["C"])
    check("compute_color_grade(base_gym) 투영 로직은 그대로 (기존 화면 호환)",
          old.top_rating_label is not None and old.base_gym == names["C"])
    _ = (sys_a, sys_b, sys_c)

    print("\n[라우트]")
    route = next((r for r in fastapi_app.routes if getattr(r, "path", "") == "/me/grade/gyms"), None)
    check("GET /me/grade/gyms 등록", route is not None and "GET" in getattr(route, "methods", set()))
    deps = [d.call.__name__ for d in route.dependant.dependencies] if route else []  # type: ignore[attr-defined]
    check("인증 필요 (get_current_user 의존성)", "get_current_user" in deps, f"{deps}")


async def main() -> int:
    engine = init_engine()
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint",
                               expire_on_commit=False, autoflush=False)
        try:
            await run(session)
        finally:
            await session.close()
            await outer.rollback()  # fixture 포함 전부 되돌림 (DB 무변경)
    await close_engine()

    total, passed = len(_results), sum(_results)
    print(f"\n{'─' * 40}")
    if passed == total:
        print(f"{GREEN}ALL PASS{RESET}  ({passed}/{total})  — DB 변경 없음(rollback)")
        return 0
    print(f"{RED}FAILED{RESET}  ({passed}/{total})")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
