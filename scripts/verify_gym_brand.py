"""gym_grade_systems.brand_name 검증 (rollback — 실제 변경 없음).

- brand_name 없이 등록 → None
- brand_name 붙여서 등록 → 그대로 저장
- list_gym_systems(brand_name=...) → 같은 브랜드만 필터, 다른 브랜드/None 제외
- list_gym_systems() (필터 없음) → 전부 조회
- update_gym_system 으로 brand_name 변경/제거 가능
- 본인 비공식 등록분이 아니면(타인 소유) 여전히 GymGradeSystemForbidden

★ 선행: brand_name 컬럼 마이그레이션이 적용돼 있어야 한다.
    uv run alembic upgrade head
실행:
    uv run python -m scripts.verify_gym_brand
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401 — 전체 모델 로드(FK 참조 등록)
from app.domain.grade.exceptions import GymGradeSystemForbidden
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


async def expect(coro, exc_type: type[Exception], name: str) -> None:
    try:
        await coro
        check(name, False)
    except exc_type:
        check(name, True)


def gym_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def run(session: AsyncSession) -> None:
    repo = GradeRepository(session)
    grade = GradeService(repo, session)
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    brand = f"피커스_{uuid.uuid4().hex[:6]}"  # 다른 실행과 안 겹치게

    print("\n[등록 — brand_name]")
    no_brand = await grade.create_gym_system(
        gym_name=gym_name("자유입력짐"),
        color_order=["흰", "노", "검"],
        user_id=owner_id,
    )
    check("brand_name 안 넘기면 None", no_brand.brand_name is None)

    jongno = await grade.create_gym_system(
        gym_name=gym_name(f"{brand}_종로"),
        color_order=["흰", "노", "주", "검"],
        user_id=owner_id,
        brand_name=brand,
    )
    check("brand_name 지정하면 그대로 저장", jongno.brand_name == brand)

    sinchon = await grade.create_gym_system(
        gym_name=gym_name(f"{brand}_신촌"),
        color_order=["흰", "파", "빨", "검"],  # 지점마다 색체계 달라도 됨
        user_id=owner_id,
        brand_name=brand,
    )
    check(
        "같은 브랜드 다른 지점, color_order 는 서로 독립",
        sinchon.color_order != jongno.color_order and sinchon.brand_name == brand,
    )

    other_brand = await grade.create_gym_system(
        gym_name=gym_name("전혀다른브랜드"),
        color_order=["흰", "검"],
        user_id=owner_id,
        brand_name=f"다른브랜드_{uuid.uuid4().hex[:6]}",
    )

    print("\n[조회 — brand_name 필터]")
    same_brand = await grade.list_gym_systems(brand_name=brand)
    same_brand_ids = {s.id for s in same_brand}
    check(
        "brand_name 필터 → 같은 브랜드 지점만(2개)",
        same_brand_ids == {jongno.id, sinchon.id},
    )
    check(
        "다른 브랜드/None 은 필터 결과에서 제외",
        other_brand.id not in same_brand_ids and no_brand.id not in same_brand_ids,
    )

    all_systems = await grade.list_gym_systems()
    all_ids = {s.id for s in all_systems}
    check(
        "필터 없이 조회하면 전부 포함",
        {no_brand.id, jongno.id, sinchon.id, other_brand.id} <= all_ids,
    )

    print("\n[수정 — brand_name 변경/제거]")
    renamed = await grade.update_gym_system(
        system_id=no_brand.id,
        color_order=no_brand.color_order,
        user_id=owner_id,
        brand_name=brand,
    )
    check("등록 후 brand_name 나중에 붙이기", renamed.brand_name == brand)

    cleared = await grade.update_gym_system(
        system_id=jongno.id,
        color_order=jongno.color_order,
        user_id=owner_id,
        brand_name=None,
    )
    check("brand_name 을 None 으로 제거 가능", cleared.brand_name is None)

    print("\n[권한 — brand_name 수정도 기존 소유권 규칙 따름]")
    await expect(
        grade.update_gym_system(
            system_id=sinchon.id,
            color_order=sinchon.color_order,
            user_id=other_id,
            brand_name="탈취시도",
        ),
        GymGradeSystemForbidden,
        "타인 소유 비공식 등록분 수정 시도 → GymGradeSystemForbidden",
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
            await outer.rollback()  # 전부 되돌림 (DB 무변경)
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
