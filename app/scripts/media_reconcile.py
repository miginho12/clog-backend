"""MinIO orphan 미디어 청소 (reconcile).

보존 집합 = (활성 기록 media_url) ∪ (soft-deleted 이지만 유예기간 내 기록 media_url)
버킷 객체 중 보존 집합에 없는 것 = orphan → 삭제.

실행:
  python -m app.scripts.media_reconcile           # dry-run (삭제 안 함, 로그만)
  python -m app.scripts.media_reconcile --apply    # 실제 삭제
"""

import argparse
import asyncio
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.climbing.models import ClimbingLog
from app.domain.media.service import MediaService
from app.infra.db.engine import close_engine, init_engine
from app.infra.db import get_sessionmaker

logger = get_logger(__name__)

# soft delete 후 미디어 보존 유예 (일)
# 환경변수 MEDIA_RECONCILE_RETENTION_DAYS 로 오버라이드 가능 (기본 2)
RETENTION_DAYS = int(os.getenv("MEDIA_RECONCILE_RETENTION_DAYS", "2"))


async def collect_referenced_keys(media: MediaService) -> set[str]:
    """보존해야 할 object_key 집합 수집."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    sm = get_sessionmaker()
    referenced: set[str] = set()
    async with sm() as session:
        # 활성(deleted_at IS NULL) + 유예기간 내(deleted_at > cutoff) 의 media_url
        stmt = select(ClimbingLog.media_url).where(
            ClimbingLog.media_url.is_not(None),
            (ClimbingLog.deleted_at.is_(None))
            | (ClimbingLog.deleted_at > cutoff),
        )
        rows = (await session.execute(stmt)).scalars().all()
    for url in rows:
        if not url:
            continue
        key = media.extract_object_key(url)
        if key:
            referenced.add(key)
    return referenced


async def main(apply: bool) -> None:
    init_engine()
    settings = get_settings()
    media = MediaService(settings, internal=True)

    referenced = await collect_referenced_keys(media)
    all_keys = list(media.list_object_keys())
    orphans = [k for k in all_keys if k not in referenced]

    logger.info(
        "media_reconcile_scan",
        total_objects=len(all_keys),
        referenced=len(referenced),
        orphans=len(orphans),
        retention_days=RETENTION_DAYS,
        apply=apply,
    )

    deleted = 0
    for key in orphans:
        if apply:
            try:
                media.delete_object(key)
                deleted += 1
                logger.info("media_orphan_deleted", object_key=key)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "media_orphan_delete_failed", object_key=key, error=str(exc)
                )
        else:
            logger.info("media_orphan_dryrun", object_key=key)

    logger.info(
        "media_reconcile_done",
        orphans=len(orphans),
        deleted=deleted if apply else 0,
        mode="apply" if apply else "dry-run",
    )
    await close_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true", help="실제 삭제 (기본은 dry-run)"
    )
    args = parser.parse_args()
    asyncio.run(main(args.apply))
