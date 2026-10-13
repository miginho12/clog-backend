"""공식 짐 색체계 동기화: 원본 환경(prod) → 이 환경(dev) (ADR-069).

difficulty_offset·color_order 가 개인 종합점수를 직접 움직이게 되면서(ADR-068)
환경마다 값이 따로 놀면 dev 검증이 의미 없어진다(2026-10-14: dev 보정치 전부 0,
알레 색 개수 9 vs 10). prod 가 기준이고, 원본은 **공개 API**로 읽는다 — prod DB
접속 정보가 필요 없다.

- 대상: 원본의 공식(is_official) 암장만. 사용자 등록 암장은 건드리지 않는다.
- 없으면 생성, 다르면 color_order·brand_name·difficulty_offset·is_official 갱신.
- 원본에 없는 이 환경의 공식 암장은 지우지 않고 보고만 한다.
- 변경마다 이력(action=sync, changed_by=NULL, 사유=원본 URL)을 남긴다.

실행 (파드 안):
  python -m app.scripts.sync_gym_systems --source https://climb-log.com/api          # 미리보기
  python -m app.scripts.sync_gym_systems --source https://climb-log.com/api --apply  # 반영
"""

import argparse
import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.grade.models import GymGradeSystem
from app.domain.grade.repository import GradeRepository
from app.infra.db import get_sessionmaker
from app.infra.db.engine import close_engine, init_engine

logger = get_logger(__name__)

SYNC_FIELDS = ("color_order", "brand_name", "difficulty_offset", "is_official")


@dataclass
class SyncPlan:
    create: list[dict[str, Any]] = field(default_factory=list)
    update: list[tuple[GymGradeSystem, dict[str, Any]]] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    local_only: list[str] = field(default_factory=list)  # 원본에 없는 이 환경의 공식 암장


def plan_sync(
    source: list[dict[str, Any]], local: dict[str, GymGradeSystem]
) -> SyncPlan:
    """원본 목록과 이 환경의 암장(gym_name → 모델)을 비교한 반영 계획 (DB 무관)."""
    plan = SyncPlan()
    source_official = [g for g in source if g.get("is_official")]
    names = {g["gym_name"] for g in source_official}
    for g in source_official:
        mine = local.get(g["gym_name"])
        if mine is None:
            plan.create.append(g)
            continue
        changes = {
            f: g[f]
            for f in SYNC_FIELDS
            if (list(getattr(mine, f)) if f == "color_order" else getattr(mine, f))
            != g[f]
        }
        if changes:
            plan.update.append((mine, changes))
        else:
            plan.unchanged.append(g["gym_name"])
    plan.local_only = sorted(
        name for name, m in local.items() if m.is_official and name not in names
    )
    return plan


async def apply_plan(session: AsyncSession, plan: SyncPlan, reason: str) -> None:
    """계획 반영 + 변경마다 sync 이력 (commit 은 호출측)."""
    repo = GradeRepository(session)
    for g in plan.create:
        created = await repo.create(
            gym_name=g["gym_name"],
            color_order=g["color_order"],
            created_by=None,
            is_official=True,
            brand_name=g["brand_name"],
            difficulty_offset=g["difficulty_offset"],
        )
        await repo.add_change(
            system_id=created.id, gym_name=created.gym_name, action="sync",
            changed_by=None, before=None, after=repo.snapshot(created), reason=reason,
        )
    for mine, changes in plan.update:
        before = repo.snapshot(mine)
        for f, v in changes.items():
            setattr(mine, f, v)
        await session.flush()
        await repo.add_change(
            system_id=mine.id, gym_name=mine.gym_name, action="sync",
            changed_by=None, before=before, after=repo.snapshot(mine), reason=reason,
        )


async def fetch_source(source: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(f"{source.rstrip('/')}/gym-grade-systems")
        res.raise_for_status()
        data: list[dict[str, Any]] = res.json()
        return data


async def run(source: str, apply: bool) -> SyncPlan:
    source_rows = await fetch_source(source)
    reason = f"원본 동기화: {source}"
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        repo = GradeRepository(session)
        local = {g.gym_name: g for g in await repo.list_all()}
        plan = plan_sync(source_rows, local)

        for g in plan.create:
            logger.info("gym_sync_create", gym=g["gym_name"], offset=g["difficulty_offset"])
        for mine, changes in plan.update:
            logger.info("gym_sync_update", gym=mine.gym_name, changes=changes)
        logger.info(
            "gym_sync_plan",
            apply=apply,
            create=len(plan.create),
            update=len(plan.update),
            unchanged=len(plan.unchanged),
            local_only=plan.local_only,
        )
        if not apply:
            return plan

        await apply_plan(session, plan, reason)
        await session.commit()
        logger.info("gym_sync_applied", create=len(plan.create), update=len(plan.update))
    return plan


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="원본 API 베이스 URL (예: https://climb-log.com/api)")
    parser.add_argument("--apply", action="store_true", help="실제 반영 (없으면 미리보기)")
    args = parser.parse_args()
    init_engine()
    try:
        await run(args.source, args.apply)
    finally:
        await close_engine()


if __name__ == "__main__":
    asyncio.run(main())
