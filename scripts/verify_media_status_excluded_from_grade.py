"""트랜스코딩 미완료(processing/failed) 영상 게시물이 점수/완등수/랭킹/인기태그
집계에서 제외되는지 검증 (rollback — 실제 변경 없음).

2026-07-20 QA: 영상 업로드 직후(트랜스코딩 끝나기 전)에도 점수/완등수가
먼저 올라가 있어서, 만약 압축이 실패하면 되돌릴 방법이 없던 문제.
list_feed 는 이미 media_status IN (NULL, 'done') 만 노출했는데, 다음
5개 집계 쿼리에는 이 필터가 빠져 있었다:
- GradeRepository.list_user_logs_for_grading (점수 계산)
- GradeRepository.count_success / count_total (완등수/전체 기록수)
- GradeRepository.list_public_color_logs_for_gym (암장 랭킹)
- ClimbingRepository.count_popular_categories (인기 해시태그)

실행:
    uv run python -m scripts.verify_media_status_excluded_from_grade
"""

import asyncio
import sys
import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.domain.climbing.repository import ClimbingRepository
from app.domain.grade.repository import GradeRepository
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
        email=f"mediastatus_{uid}@clog.test",
        nickname=f"{nick}_{uid}",
        auth_provider="local",
        email_verified=True,
        is_public=True,
    )


async def run(session: AsyncSession) -> None:
    crepo = ClimbingRepository(session)
    grepo = GradeRepository(session)

    user = make_user("author")
    session.add(user)
    await session.flush()

    gym = await grepo.create(
        gym_name=f"검증암장_{uuid.uuid4().hex[:6]}",
        color_order=["흰", "노", "주", "초", "파", "빨", "보"],
        created_by=user.id,
        is_official=True,
        brand_name=None,
    )
    await session.flush()

    async def make_log(*, media_status, categories, grade_system="color"):
        return await crepo.create(
            user_id=user.id,
            grade_raw="빨" if grade_system == "color" else "V3",
            grade_system=grade_system,
            gym_name=gym.gym_name,
            categories=categories,
            attempts=1,
            is_success=True,
            visibility="public",
            climbed_at=date.today(),
            media_type="video",
            media_status=media_status,
        )

    processing_log = await make_log(
        media_status="processing", categories=["processing-tag"]
    )
    failed_log = await make_log(media_status="failed", categories=["failed-tag"])
    done_log = await make_log(media_status="done", categories=["done-tag"])
    no_media_log = await crepo.create(
        user_id=user.id,
        grade_raw="파",
        grade_system="color",
        gym_name=gym.gym_name,
        categories=["no-media-tag"],
        attempts=1,
        is_success=True,
        visibility="public",
        climbed_at=date.today(),
    )
    await session.flush()

    # ── list_user_logs_for_grading ──
    grading_logs = await grepo.list_user_logs_for_grading(user.id, "color")
    grading_ids = {log.id for log in grading_logs}
    check(
        "list_user_logs_for_grading: processing 제외",
        processing_log.id not in grading_ids,
    )
    check(
        "list_user_logs_for_grading: failed 제외", failed_log.id not in grading_ids
    )
    check("list_user_logs_for_grading: done 포함", done_log.id in grading_ids)
    check(
        "list_user_logs_for_grading: 미디어 없음 포함",
        no_media_log.id in grading_ids,
    )

    # ── count_success / count_total ──
    success_count = await grepo.count_success(user.id)
    total_count = await grepo.count_total(user.id)
    check("count_success: processing/failed 제외하고 2건만", success_count == 2)
    check("count_total: processing/failed 제외하고 2건만", total_count == 2)

    # ── list_public_color_logs_for_gym (암장 랭킹) ──
    ranking_logs = await grepo.list_public_color_logs_for_gym(gym.gym_name)
    ranking_ids = {log.id for log in ranking_logs}
    check(
        "list_public_color_logs_for_gym: processing 제외",
        processing_log.id not in ranking_ids,
    )
    check(
        "list_public_color_logs_for_gym: failed 제외", failed_log.id not in ranking_ids
    )
    check("list_public_color_logs_for_gym: done 포함", done_log.id in ranking_ids)

    # ── count_popular_categories (인기 해시태그) ──
    popular = await crepo.count_popular_categories(limit=50)
    popular_tags = {tag for tag, _ in popular}
    check(
        "count_popular_categories: processing 태그 제외",
        "processing-tag" not in popular_tags,
    )
    check(
        "count_popular_categories: failed 태그 제외", "failed-tag" not in popular_tags
    )
    check("count_popular_categories: done 태그 포함", "done-tag" in popular_tags)
    check(
        "count_popular_categories: 미디어 없음 태그 포함",
        "no-media-tag" in popular_tags,
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
