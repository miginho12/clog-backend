"""암장 랭킹(GradeService.compute_gym_ranking) 검증 (rollback — 실제 변경 없음).

- 같은 암장 여러 유저 → 완등 난이도 높은 쪽이 상위 랭크
- 비공개 계정(is_public=False) 기록 → 랭킹 제외
- 비공개 글(visibility=private) 기록 → 랭킹 제외 (공개 계정이어도)
- 완등 기록이 하나도 없는 유저(시도만 있음) → 랭킹에서 아예 빠짐
- V스케일 기록 → 컬러 랭킹에 영향 없음
- 다른 암장 기록 → 이 암장 랭킹에 안 섞임
- 등록 안 된 암장으로 조회 → GymGradeSystemNotFound
- period=week → 그 ISO 주 기록만, period=month → 그 달 기록만
- period 파라미터 조합이 잘못되면 InvalidRankingPeriod

★ 선행: brand_name 마이그레이션이 적용돼 있어야 한다 (같은 alembic 라인).
    uv run alembic upgrade head
실행:
    uv run python -m scripts.verify_gym_ranking
"""

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401 — 전체 모델 로드(FK 참조 등록)
from app.domain.climbing.models import ClimbingLog
from app.domain.grade.exceptions import GymGradeSystemNotFound, InvalidRankingPeriod
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


async def expect(coro, exc_type: type[Exception], name: str) -> None:
    try:
        await coro
        check(name, False)
    except exc_type:
        check(name, True)


def make_user(nickname: str, *, is_public: bool = True) -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"gr_{uid}@clog.test",
        nickname=f"{nickname}_{uid}",
        auth_provider="local",
        email_verified=True,
        is_public=is_public,
    )


def make_log(
    user: User,
    *,
    gym_name: str,
    grade_raw: str,
    grade_system: str = "color",
    is_success: bool = True,
    visibility: str = "public",
    climbed_at: date | None = None,
) -> ClimbingLog:
    return ClimbingLog(
        user_id=user.id,
        gym_name=gym_name,
        grade_raw=grade_raw,
        grade_system=grade_system,
        is_success=is_success,
        attempts=1,
        climbed_at=climbed_at or date.today(),
        visibility=visibility,
        categories=[],
    )


async def run(session: AsyncSession) -> None:
    repo = GradeRepository(session)
    grade = GradeService(repo, session)
    gym = f"랭킹테스트짐_{uuid.uuid4().hex[:6]}"
    other_gym = f"다른짐_{uuid.uuid4().hex[:6]}"

    await grade.create_gym_system(
        gym_name=gym,
        color_order=["흰", "노", "주", "초", "파", "빨", "보", "검"],
        user_id=uuid.uuid4(),
    )
    await grade.create_gym_system(
        gym_name=other_gym,
        color_order=["흰", "검"],
        user_id=uuid.uuid4(),
    )

    strong = make_user("고수")  # 검(최상위) 완등
    weak = make_user("초심")  # 노(하위) 완등
    private_acct = make_user("비공계", is_public=False)  # 계정 자체 비공개
    private_post = make_user("비공글")  # 계정은 공개, 글만 비공개
    only_fail = make_user("만년시도")  # 완등 없음(시도만)
    session.add_all([strong, weak, private_acct, private_post, only_fail])
    await session.flush()

    session.add_all(
        [
            make_log(strong, gym_name=gym, grade_raw="검", is_success=True),
            make_log(weak, gym_name=gym, grade_raw="노", is_success=True),
            make_log(
                private_acct, gym_name=gym, grade_raw="검", is_success=True
            ),
            make_log(
                private_post,
                gym_name=gym,
                grade_raw="검",
                is_success=True,
                visibility="private",
            ),
            make_log(only_fail, gym_name=gym, grade_raw="검", is_success=False),
            # v_scale 기록 — 컬러 랭킹에 안 섞여야 함
            make_log(
                weak, gym_name=gym, grade_raw="V10", grade_system="v_scale"
            ),
            # 다른 암장 기록 — 이 암장 랭킹에 안 섞여야 함
            make_log(strong, gym_name=other_gym, grade_raw="검", is_success=True),
        ]
    )
    await session.flush()

    print("\n[정상 랭킹]")
    ranking = await grade.compute_gym_ranking(gym)
    nicknames = [e.user.nickname for e in ranking.entries]

    check(
        "고수/초심만 랭킹에 존재 (비공개계정/비공개글/시도만 제외)",
        strong.nickname in nicknames
        and weak.nickname in nicknames
        and private_acct.nickname not in nicknames
        and private_post.nickname not in nicknames
        and only_fail.nickname not in nicknames,
    )
    check("랭킹 2명 정확히", len(ranking.entries) == 2)

    strong_entry = next(e for e in ranking.entries if e.user.nickname == strong.nickname)
    weak_entry = next(e for e in ranking.entries if e.user.nickname == weak.nickname)
    check("검 완등이 노 완등보다 점수 높음", strong_entry.score > weak_entry.score)
    check("1등 rank=1, 2등 rank=2", strong_entry.rank == 1 and weak_entry.rank == 2)
    check("1등 top_color_label = 검", strong_entry.top_color_label == "검")
    check("gym_name 응답에 그대로 반영", ranking.gym_name == gym)

    print("\n[다른 암장은 섞이지 않음]")
    other_ranking = await grade.compute_gym_ranking(other_gym)
    check(
        "다른 암장 랭킹엔 그 암장 기록만(1명)",
        len(other_ranking.entries) == 1
        and other_ranking.entries[0].user.nickname == strong.nickname,
    )

    print("\n[미등록 암장]")
    await expect(
        grade.compute_gym_ranking(f"없는짐_{uuid.uuid4().hex[:6]}"),
        GymGradeSystemNotFound,
        "등록 안 된 암장 조회 → GymGradeSystemNotFound",
    )

    print("\n[기간 랭킹 — week/month]")
    period_gym = f"기간테스트짐_{uuid.uuid4().hex[:6]}"
    await grade.create_gym_system(
        gym_name=period_gym,
        color_order=["흰", "노", "검"],
        user_id=uuid.uuid4(),
    )
    # 월 중순(15일) 기준 — 이번 주(week_a) / 같은 달 다른 주(week_b, -7일) /
    # 다른 달(-40일). 경계 이슈 없게 항상 15일에서 계산.
    today = date.today()
    ref = date(today.year, today.month, 15)
    week_a_date = ref
    week_b_date = ref - timedelta(days=7)
    other_month_date = ref - timedelta(days=40)

    week_a_user = make_user("이번주")
    week_b_user = make_user("같은달다른주")
    other_month_user = make_user("다른달")
    session.add_all([week_a_user, week_b_user, other_month_user])
    await session.flush()
    session.add_all(
        [
            make_log(
                week_a_user,
                gym_name=period_gym,
                grade_raw="검",
                climbed_at=week_a_date,
            ),
            make_log(
                week_b_user,
                gym_name=period_gym,
                grade_raw="노",
                climbed_at=week_b_date,
            ),
            make_log(
                other_month_user,
                gym_name=period_gym,
                grade_raw="검",
                climbed_at=other_month_date,
            ),
        ]
    )
    await session.flush()

    iso_year, iso_week, _ = week_a_date.isocalendar()
    week_ranking = await grade.compute_gym_ranking(
        period_gym, period="week", year=iso_year, week=iso_week
    )
    week_nicknames = [e.user.nickname for e in week_ranking.entries]
    check(
        "week 랭킹엔 그 주 기록 유저만",
        week_a_user.nickname in week_nicknames
        and week_b_user.nickname not in week_nicknames
        and other_month_user.nickname not in week_nicknames,
    )
    check("week 응답 period 필드", week_ranking.period == "week")
    check(
        "week 응답 range 는 월요일~일요일",
        week_ranking.range_start is not None
        and week_ranking.range_start.weekday() == 0
        and (week_ranking.range_end - week_ranking.range_start).days == 6,  # type: ignore[operator]
    )

    month_ranking = await grade.compute_gym_ranking(
        period_gym, period="month", year=ref.year, month=ref.month
    )
    month_nicknames = [e.user.nickname for e in month_ranking.entries]
    check(
        "month 랭킹엔 같은 달(다른 주 포함) 유저만, 다른 달 제외",
        week_a_user.nickname in month_nicknames
        and week_b_user.nickname in month_nicknames
        and other_month_user.nickname not in month_nicknames,
    )

    print("\n[기간 파라미터 검증]")
    await expect(
        grade.compute_gym_ranking(period_gym, period="week", year=2026),
        InvalidRankingPeriod,
        "period=week 인데 week 누락 → InvalidRankingPeriod",
    )
    await expect(
        grade.compute_gym_ranking(period_gym, period="month", year=2026),
        InvalidRankingPeriod,
        "period=month 인데 month 누락 → InvalidRankingPeriod",
    )
    await expect(
        grade.compute_gym_ranking(period_gym, period="week", year=2026, week=60),
        InvalidRankingPeriod,
        "존재하지 않는 ISO 주차(60) → InvalidRankingPeriod",
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
