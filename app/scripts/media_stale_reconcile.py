"""멈춘(stale) 미디어 처리 reconcile.

배경: transcode_service.transcode() 는 정상 예외 경로에서만 media_status=failed
+ 실패 알림을 보낸다. 워커 프로세스가 OOM 등으로 통째 SIGKILL 되면 이 except
블록에 도달하지 못해 climbing_log 가 media_status=processing 에 영원히
갇히고(피드에서 숨김), 유저는 실패 알림도 못 받는다.

arq 재시도(max_tries=2, job_timeout)가 다 소진돼도 이 갭은 메워지지 않으므로,
updated_at 기준으로 일정 시간 이상 processing 인 건을 실패로 간주해 정리한다.
(update 시 media_status 변경이 없었다면 onupdate=func.now() 가 안 걸려
updated_at 은 created_at 그대로 남는다.)

실행:
  python -m app.scripts.media_stale_reconcile           # dry-run (변경 안 함, 로그만)
  python -m app.scripts.media_stale_reconcile --apply    # 실제 failed 처리 + 알림
"""

import argparse
import asyncio
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.climbing.models import ClimbingLog
from app.domain.notifications.repository import NotificationRepository
from app.domain.notifications.service import NotificationService
from app.infra.db import get_sessionmaker
from app.infra.db.engine import close_engine, init_engine

logger = get_logger(__name__)

# processing 상태가 이 시간(분) 이상 멈춰 있으면 stale 로 간주.
# 근거: job_timeout(transcode_timeout+120s) x max_tries(2) 가 arq 가 포기하는
# 최대 시간(~34분, transcode_timeout 900s 기준) 이므로, 60분이면 정상 재시도 중인 건을 오탐하지 않는다.
# 환경변수 MEDIA_STALE_PROCESSING_MINUTES 로 오버라이드 가능.
STALE_MINUTES = int(os.getenv("MEDIA_STALE_PROCESSING_MINUTES", "60"))


async def find_stale_logs(
    session: AsyncSession, cutoff: datetime
) -> list[ClimbingLog]:
    """updated_at 이 cutoff 이전인 processing 건을 조회."""
    stmt = select(ClimbingLog).where(
        ClimbingLog.media_status == "processing",
        ClimbingLog.updated_at < cutoff,
        ClimbingLog.deleted_at.is_(None),
    )
    return list((await session.execute(stmt)).scalars().all())


async def mark_stale_failed(session: AsyncSession, log: ClimbingLog) -> None:
    """단건 failed 처리 + 실패 알림. 커밋은 호출자 책임(레포지토리 규칙과 동일)."""
    log.media_status = "failed"
    notification_service = NotificationService(
        session, NotificationRepository(session)
    )
    await notification_service.notify_media_failed(
        recipient_id=log.user_id, climbing_log_id=log.id
    )
    await session.flush()


async def main(apply: bool) -> None:
    init_engine()
    cutoff = datetime.now(UTC) - timedelta(minutes=STALE_MINUTES)
    sm = get_sessionmaker()

    async with sm() as session:
        stale_logs = await find_stale_logs(session, cutoff)
        logger.info(
            "media_stale_reconcile_scan",
            stale_count=len(stale_logs),
            stale_minutes=STALE_MINUTES,
            apply=apply,
        )

        processed = 0
        for log in stale_logs:
            if not apply:
                logger.info(
                    "media_stale_dryrun",
                    log_id=str(log.id),
                    updated_at=log.updated_at.isoformat(),
                )
                continue
            try:
                await mark_stale_failed(session, log)
                await session.commit()
                processed += 1
                logger.info("media_stale_marked_failed", log_id=str(log.id))
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                logger.warning(
                    "media_stale_mark_failed_error",
                    log_id=str(log.id),
                    error=str(exc),
                )

        logger.info(
            "media_stale_reconcile_done",
            stale_count=len(stale_logs),
            processed=processed if apply else 0,
            mode="apply" if apply else "dry-run",
        )
    await close_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true", help="실제 failed 처리 (기본은 dry-run)"
    )
    args = parser.parse_args()
    asyncio.run(main(args.apply))
