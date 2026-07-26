"""암장별 난이도 보정치(difficulty_offset)가 환산(투영)에 반영되는지 검증
(rollback — 실제 변경 없음).

QA #5: 색 단계 위치(ratio)만으로 암장 간 난이도를 환산하면 실제 체감
난이도 차이(을지로 최상급이 다른 암장 중급 수준 등)를 반영 못 한다는
문제. 관리자가 암장별 difficulty_offset(기본 0, 보정 없음)을 설정하면
환산 시 반영되도록 GradeRepository.normalize_ratio/denormalize_ratio +
GradeService.compute_color_grade(base_gym=...) 에 적용했다.

기대 동작:
- offset=0(기본)이면 예전과 동일하게 순수 ratio 투영
- 출발 암장 offset이 양수(더 어려움)면, 도착 암장에서 더 높은 색으로 투영
- 실제 완등 기록 표시(base_gym 미지정, QA #4)에는 영향 없어야 함(투영 아님)

실행:
    uv run python -m scripts.verify_gym_difficulty_offset
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
        email=f"offset_{uid}@clog.test",
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
    hard_gym_name = f"을지로담장_{suffix}"
    normal_gym_name = f"평범암장_{suffix}"

    # 4단계씩 동일 — offset만 다름
    hard_gym = await grepo.create(
        gym_name=hard_gym_name,
        color_order=["흰", "노", "파", "보"],
        created_by=user.id,
        is_official=True,
        brand_name=None,
        difficulty_offset=0.2,  # 실제로 더 어려운 암장
    )
    await grepo.create(
        gym_name=normal_gym_name,
        color_order=["흰", "노", "파", "보"],
        created_by=user.id,
        is_official=True,
        brand_name=None,
        difficulty_offset=0.0,
    )
    await session.flush()

    # 을지로담장에서 "파"(rank2/3, ratio=0.667) 완등
    await crepo.create(
        user_id=user.id,
        grade_raw="파",
        grade_system="color",
        gym_name=hard_gym_name,
        categories=[],
        attempts=1,
        is_success=True,
        visibility="public",
        climbed_at=date(2026, 1, 1),
    )
    await session.flush()

    # ── 정적 메서드 단위 검증 ──
    ratio = grepo.rank_to_ratio(hard_gym, hard_gym.color_order.index("파"))
    normalized = grepo.normalize_ratio(hard_gym, ratio)
    check("normalize_ratio: 양수 offset만큼 올라감", normalized > ratio)
    check(
        "normalize_ratio 값 정확성 (0.667+0.2=0.867)",
        abs(normalized - (ratio + 0.2)) < 1e-9,
    )
    back = grepo.denormalize_ratio(hard_gym, normalized)
    check("denormalize_ratio: 원래 ratio로 복원", abs(back - ratio) < 1e-9)

    # ── 서비스 전체 흐름: 평범암장 기준으로 환산했을 때 "보"로 올라가야 함 ──
    projected = await service.compute_color_grade(user.id, base_gym=normal_gym_name)
    check(
        "offset 반영: 평범암장 환산 결과가 '보'로 상향 투영됨",
        projected.top_rating_label == "보",
    )

    # ── 실제 최고등급(base_gym 미지정)엔 영향 없어야 함 — QA #4 회귀 확인 ──
    actual = await service.compute_color_grade(user.id)
    check(
        "실제 최고등급은 offset과 무관하게 실제 기록 그대로('파')",
        actual.top_rating_label == "파" and actual.base_gym == hard_gym_name,
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
