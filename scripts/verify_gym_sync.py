"""공식 짐 색체계 동기화 검증 (ADR-069, rollback — 실제 변경 없음).

app/scripts/sync_gym_systems.py 의 plan_sync / apply_plan 을 확인한다.
마지막 항목은 실제 prod 공개 API 를 읽기만 한다(반영 안 함).

실행:
    uv run python -m scripts.verify_gym_sync
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.domain.grade.repository import GradeRepository
from app.infra.db.engine import close_engine, init_engine
from app.scripts.sync_gym_systems import apply_plan, fetch_source, plan_sync

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f"  ({detail})" if detail else ""))


C9 = ["빨", "주", "노", "초", "파", "남", "보", "갈", "검"]
C10 = C9 + ["회"]


def src(name: str, colors: list[str], offset: float, official: bool = True,
        brand: str | None = None) -> dict:
    return {"gym_name": name, "color_order": colors, "difficulty_offset": offset,
            "is_official": official, "brand_name": brand}


async def run(session: AsyncSession) -> None:
    repo = GradeRepository(session)
    t = uuid.uuid4().hex[:6]
    n = {k: f"{k}_{t}" for k in ["새암장", "다른값", "같은값", "사용자등록", "로컬만", "로컬개인"]}

    # 이 환경(dev 역할): 다른값(9색·0), 같은값, 로컬만(공식), 로컬개인(비공식)
    for key, colors, off, official in [
        ("다른값", C9, 0.0, True), ("같은값", C9, 0.2, True),
        ("로컬만", C9, 0.0, True), ("로컬개인", C9, 0.0, False),
    ]:
        await repo.create(gym_name=n[key], color_order=colors, created_by=None,
                          is_official=official, difficulty_offset=off)
    await session.flush()
    local = {g.gym_name: g for g in await repo.list_all() if g.gym_name.endswith(t)}

    # 원본(prod 역할)
    source = [
        src(n["새암장"], C9, 0.3, brand="브랜드"),
        src(n["다른값"], C10, 0.15),          # 색 개수 9→10, 보정 0→0.15 (알레 사례)
        src(n["같은값"], C9, 0.2),
        src(n["사용자등록"], C9, 0.5, official=False),
    ]

    print("\n[계획]")
    plan = plan_sync(source, local)
    check("원본에만 있는 공식 암장 → 생성", [g["gym_name"] for g in plan.create] == [n["새암장"]])
    upd = {m.gym_name: c for m, c in plan.update}
    check("값이 다른 암장 → 바뀐 필드만 수정",
          upd == {n["다른값"]: {"color_order": C10, "difficulty_offset": 0.15}}, f"{upd}")
    check("같은 값 → 변경 없음", plan.unchanged == [n["같은값"]])
    check("원본의 사용자 등록 암장은 무시",
          n["사용자등록"] not in [g["gym_name"] for g in plan.create])
    check("원본에 없는 이 환경 공식 암장은 삭제 안 하고 보고만 (비공식 제외)",
          plan.local_only == [n["로컬만"]], f"{plan.local_only}")

    print("\n[반영]")
    await apply_plan(session, plan, "원본 동기화: test")
    after = {g.gym_name: g for g in await repo.list_all() if g.gym_name.endswith(t)}
    new = after[n["새암장"]]
    check("생성: 공식 + 보정치·브랜드 원본대로",
          new.is_official and new.difficulty_offset == 0.3 and new.brand_name == "브랜드")
    diff = after[n["다른값"]]
    check("수정: 10색·보정 0.15", list(diff.color_order) == C10 and diff.difficulty_offset == 0.15)
    h_new = await repo.list_changes(new.id)
    h_diff = await repo.list_changes(diff.id)
    check("sync 이력: 생성 1건(before 없음, 바꾼 사람 없음, 사유)",
          len(h_new) == 1 and h_new[0].action == "sync" and h_new[0].before is None
          and h_new[0].changed_by is None and h_new[0].reason == "원본 동기화: test")
    check("sync 이력: 수정 before/after",
          len(h_diff) == 1 and h_diff[0].before is not None and h_diff[0].after is not None
          and h_diff[0].before["difficulty_offset"] == 0.0
          and h_diff[0].after["difficulty_offset"] == 0.15)
    check("같은 값 암장은 이력 없음", await repo.list_changes(after[n["같은값"]].id) == [])

    again = plan_sync(source, after)
    check("다시 실행하면 할 일 없음 (멱등)", not again.create and not again.update)

    print("\n[실제 prod 공개 API — 읽기만]")
    try:
        prod = await fetch_source("https://climb-log.com/api")
        official = [g for g in prod if g.get("is_official")]
        check("prod 공식 암장 목록 수신 + 필드 존재",
              len(official) > 0 and all(
                  {"gym_name", "color_order", "difficulty_offset", "brand_name"} <= g.keys()
                  for g in official), f"공식 {len(official)}곳")
    except Exception as e:  # noqa: BLE001
        check("prod 공개 API 수신", False, repr(e))


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
