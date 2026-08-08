"""암장 간 난이도 환산 탭 대상(list_gym_system_representatives) 검증 (rollback).

배경: GradePage 의 "암장 간 난이도 환산" 탭이 필터 없는 list_gym_systems() 를
그대로 써서, 같은 브랜드의 지점(예: "서울숲클라이밍 종로")까지 브랜드 대표
("서울숲클라이밍")와 함께 중복 노출되는 문제가 있었다. is_official=True 이면서
brand_name 이 없거나 gym_name 과 같은(=브랜드 대표/단독) 행만 반환하는지
확인한다. ef96f2ae7b85 마이그레이션이 기존 등록분을 자기 자신을 브랜드로
백필했기 때문에(brand_name == gym_name), NULL 뿐 아니라 이 케이스도 대표로
인정해야 한다.

실행:
    uv run python -m scripts.verify_gym_representatives

savepoint 패턴 — 마지막에 바깥 트랜잭션 rollback, DB 에 아무것도 남지 않는다.
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401 — 전체 모델 로드(FK 참조 등록)
from app.domain.grade.repository import GradeRepository
from app.domain.grade.service import GradeService
from app.infra.db.engine import close_engine, init_engine

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

_results: list[bool] = []


def check(name: str, ok: bool) -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}")


def gym_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def run(session: AsyncSession) -> None:
    repo = GradeRepository(session)
    svc = GradeService(session=session, repo=repo)

    base = ["흰", "노", "검"]
    brand = f"서울숲클라이밍_{uuid.uuid4().hex[:8]}"

    representative_null = await repo.create(  # 브랜드 대표/단독 (공식, brand_name 미지정)
        gym_name=gym_name("no_brand"),
        color_order=base,
        created_by=None,
        is_official=True,
        brand_name=None,
    )
    representative_self = await repo.create(  # 브랜드 대표 (공식, 백필 패턴: brand_name == gym_name)
        gym_name=brand,
        color_order=base,
        created_by=None,
        is_official=True,
        brand_name=brand,
    )
    branch = await repo.create(  # 같은 브랜드 지점 (공식)
        gym_name=f"{brand} 종로",
        color_order=base,
        created_by=None,
        is_official=True,
        brand_name=brand,
    )
    unofficial_standalone = await repo.create(  # 단독이지만 비공식(사용자 등록)
        gym_name=gym_name("user_gym"),
        color_order=base,
        created_by=None,
        is_official=False,
        brand_name=None,
    )
    await session.flush()

    reps = await svc.list_gym_system_representatives()
    rep_ids = {s.id for s in reps}

    print("\n[암장 간 난이도 환산 대상 필터]")
    check(
        "브랜드 대표(공식, brand_name 없음) 포함", representative_null.id in rep_ids
    )
    check(
        "브랜드 대표(공식, brand_name==gym_name 백필 패턴) 포함",
        representative_self.id in rep_ids,
    )
    check("같은 브랜드 지점(brand_name != gym_name) 제외", branch.id not in rep_ids)
    check("비공식 단독 암장 제외", unofficial_standalone.id not in rep_ids)

    # 기존 전체 목록(list_gym_systems)은 필터 없이 전부 반환 — 회귀 확인
    all_systems = await svc.list_gym_systems()
    all_ids = {s.id for s in all_systems}
    check(
        "기존 전체 목록(list_gym_systems)은 지점도 그대로 포함(회귀)",
        branch.id in all_ids,
    )


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
            await outer.rollback()  # DB 에 아무것도 남지 않음
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
