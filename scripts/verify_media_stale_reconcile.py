"""멈춘(stale) 미디어 처리 reconcile 검증 (rollback — 실제 변경 없음).

OOM 등으로 워커가 통째로 죽으면 climbing_log 가 media_status=processing 에
갇힌 채 실패 알림도 못 받는 갭을, updated_at 기준 stale 판정으로 메우는
app.scripts.media_stale_reconcile 을 검증한다.

실행:
    uv run python -m scripts.verify_media_stale_reconcile

savepoint 패턴으로 서비스 내부 commit 을 savepoint 로 가두고, 마지막에
바깥 트랜잭션을 rollback 하므로 DB 에 아무것도 남지 않는다.
"""

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401 — 전체 모델 로드(FK 참조 등록)
from app.domain.climbing.models import ClimbingLog
from app.domain.notifications.repository import NotificationRepository
from app.domain.users.models import User
from app.infra.db.engine import close_engine, init_engine
from app.scripts.media_stale_reconcile import find_stale_logs, mark_stale_failed

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

_results: list[bool] = []


def check(name: str, ok: bool) -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}")


def make_user() -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"stale_{uid}@clog.test",
        nickname=f"stale_{uid}",
        auth_provider="local",
        email_verified=True,
    )


def make_log(user: User, *, media_status: str | None) -> ClimbingLog:
    return ClimbingLog(
        user_id=user.id,
        grade_raw="V3",
        grade_system="v_scale",
        media_type="video",
        media_status=media_status,
        original_media_url="https://minio.internal/clog-media/orig.mp4",
    )


async def run(session: AsyncSession) -> None:
    user = make_user()
    session.add(user)
    await session.flush()

    cutoff = datetime.now(UTC) - timedelta(minutes=60)
    old_ts = datetime.now(UTC) - timedelta(minutes=120)
    recent_ts = datetime.now(UTC) - timedelta(minutes=5)

    stale_processing = make_log(user, media_status="processing")
    fresh_processing = make_log(user, media_status="processing")
    stale_done = make_log(user, media_status="done")
    stale_deleted = make_log(user, media_status="processing")
    session.add_all([stale_processing, fresh_processing, stale_done, stale_deleted])
    await session.flush()

    # updated_at 을 직접 과거로 되돌린다 (onupdate 는 명시적으로 건드리지 않은
    # 컬럼에만 적용되므로, 여기서 직접 대입한 값이 그대로 저장된다).
    stale_processing.updated_at = old_ts
    fresh_processing.updated_at = recent_ts
    stale_done.updated_at = old_ts
    stale_deleted.updated_at = old_ts
    stale_deleted.deleted_at = datetime.now(UTC)
    await session.flush()

    print("\n[stale 판정 — find_stale_logs]")
    found = await find_stale_logs(session, cutoff)
    found_ids = {log.id for log in found}
    check(
        "stale + processing 건만 조회됨 (fresh/done/soft-deleted 제외)",
        found_ids == {stale_processing.id},
    )

    print("\n[failed 처리 + 알림 — mark_stale_failed]")
    await mark_stale_failed(session, stale_processing)
    check(
        "media_status: processing → failed",
        stale_processing.media_status == "failed",
    )

    notif_repo = NotificationRepository(session)
    notifications = await notif_repo.list_by_recipient(user.id)
    media_failed = [n for n in notifications if n.type == "media_failed"]
    check("media_failed 알림 1건 생성", len(media_failed) == 1)
    check(
        "알림이 stale_processing 로그를 가리킴",
        bool(media_failed) and media_failed[0].climbing_log_id == stale_processing.id,
    )

    print("\n[재조회 시 더 이상 stale 목록에 없음]")
    found_after = await find_stale_logs(session, cutoff)
    check(
        "failed 처리된 건은 재조회에서 제외",
        stale_processing.id not in {log.id for log in found_after},
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
