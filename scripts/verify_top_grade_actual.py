"""'최고 등급'이 실제 완등 기록을 반영하는지 검증 (rollback — 실제 변경 없음).

버그: base_gym 미지정 시(기본/프로필 최고등급) top_ratio(전체 암장 중 최대
ratio)를 "가장 자주 기록한 암장"의 색 체계에 그대로 투영(ratio_to_color)해서,
그 암장에서 실제로 딴 적 없는 색이 "최고 등급"으로 표시되던 문제.
(2026-07-27 QA: "손상원 보라 영상을 업로드한 적이 없는데도 손상원 보라로
표시된다")

재현 시나리오:
- 손상원(4단계: 흰/노/파/보) — 1건만 기록, "보"(맨 위) 완등 → ratio=1.0
- 더클라임(8단계) — 여러 건 기록(더 자주 감), 최고는 3번째 색까지만 완등
  → ratio ~0.29

예전 로직: gym_counter 기준 "더클라임"이 base_gym으로 뽑히고, 거기에
ratio=1.0(손상원 기록)을 투영하면 더클라임의 최상위색(예: 검정)이 나옴 —
실제로 딴 적 없는 색.
새 로직: 실제 최고 완등 기록(손상원·보)의 원본 그대로 나와야 한다.

실행:
    uv run python -m scripts.verify_top_grade_actual
"""

import asyncio
import sys
import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.domain.climbing.repository import ClimbingRepository
from app.domain.grade.repository import GradeRepository
from app.domain.grade.service import GradeService
from app.domain.users.models import User
from app.infra.db.engine import close_engine, init_engine

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
_results: list[bool] = []


def check(name: str, ok: bool) -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}")


def make_user(nick: str) -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"topgrade_{uid}@clog.test",
        nickname=f"{nick}_{uid}",
        auth_provider="local",
        email_verified=True,
        is_public=True,
    )


async def run(session: AsyncSession) -> None:
    crepo = ClimbingRepository(session)
    grepo = GradeRepository(session)
    service = GradeService(grepo, session)

    user = make_user("author")
    session.add(user)
    await session.flush()

    suffix = uuid.uuid4().hex[:6]
    small_gym_name = f"손상원_{suffix}"
    big_gym_name = f"더클라임_{suffix}"

    small_gym = await grepo.create(
        gym_name=small_gym_name,
        color_order=["흰", "노", "파", "보"],
        created_by=user.id,
        is_official=True,
        brand_name=None,
    )
    big_gym = await grepo.create(
        gym_name=big_gym_name,
        color_order=["흰", "노", "주", "초", "파", "빨", "보", "검"],
        created_by=user.id,
        is_official=True,
        brand_name=None,
    )
    await session.flush()

    # 손상원: 1건만, 맨 위 색(보) 완등 → ratio=1.0
    await crepo.create(
        user_id=user.id,
        grade_raw="보",
        grade_system="color",
        gym_name=small_gym.gym_name,
        categories=[],
        attempts=1,
        is_success=True,
        visibility="public",
        climbed_at=date(2026, 1, 1),
    )

    # 더클라임: 훨씬 자주 기록(6건), 최고는 3번째 색(초)까지만 완등
    for i in range(6):
        await crepo.create(
            user_id=user.id,
            grade_raw="초" if i == 0 else "노",
            grade_system="color",
            gym_name=big_gym.gym_name,
            categories=[],
            attempts=1,
            is_success=True,
            visibility="public",
            climbed_at=date(2026, 2, i + 1),
        )
    await session.flush()

    result = await service.compute_color_grade(user.id)

    check(
        "base_gym이 실제 최고 완등 암장(손상원)",
        result.base_gym == small_gym.gym_name,
    )
    check(
        "top_rating_label이 실제로 딴 색('보')",
        result.top_rating_label == "보",
    )
    check(
        "더클라임 최상위색('검')이 아님 — 실제로 딴 적 없는 색이 안 나옴",
        result.top_rating_label != "검",
    )

    # base_gym 명시 시(환산 목록 용도)는 기존처럼 투영되어야 한다 — 회귀 확인
    projected = await service.compute_color_grade(user.id, base_gym=big_gym_name)
    check(
        "base_gym 명시 시엔 투영된 라벨(더클라임 체계로 변환)",
        projected.base_gym == big_gym_name,
    )
    check(
        "투영 결과는 더클라임 색 체계 안의 값",
        projected.top_rating_label in big_gym.color_order,
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
