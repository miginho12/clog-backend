"""MinIO orphan 미디어 청소 (reconcile).

보존 집합 = (활성 기록 media_url/thumbnail_url) ∪ (soft-deleted 이지만
유예기간 내 기록 media_url/thumbnail_url)
  + 위 기록 중 미완료(processing/failed)의 original_media_url
버킷 객체 중 보존 집합에 없고, 만들어진 지 MIN_AGE_HOURS 가 지난 것 = orphan → 삭제.

실행:
  python -m app.scripts.media_reconcile           # dry-run (삭제 안 함, 로그만)
  python -m app.scripts.media_reconcile --apply    # 실제 삭제
"""

import argparse
import asyncio
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.climbing.models import ClimbingLog
from app.domain.media.service import MediaService
from app.infra.db import get_sessionmaker
from app.infra.db.engine import close_engine, init_engine

logger = get_logger(__name__)

# soft delete 후 미디어 보존 유예 (일)
# 환경변수 MEDIA_RECONCILE_RETENTION_DAYS 로 오버라이드 가능 (기본 2)
RETENTION_DAYS = int(os.getenv("MEDIA_RECONCILE_RETENTION_DAYS", "2"))

# 이보다 최근에 만들어진 객체는 orphan 이어도 건너뜀 (시간)
# 게시 직후 원본은 original_media_url 로 보존되지만, presigned 업로드만 끝나고
# 게시 전인 객체·트랜스코딩 중 새로 올린 결과물은 아직 DB 에 없다 → 경합 방지.
MIN_AGE_HOURS = int(os.getenv("MEDIA_RECONCILE_MIN_AGE_HOURS", "24"))


async def collect_referenced_keys(
    session: AsyncSession, media: MediaService, cutoff: datetime
) -> set[str]:
    """보존해야 할 object_key 집합 수집.

    활성(deleted_at IS NULL) + 유예기간 내(deleted_at > cutoff) 의
    media_url/thumbnail_url — thumbnail_url 을 빼먹으면 영상 게시물의
    정지 썸네일이 매번 orphan 으로 오인되어 삭제된다(실제 발생한 회귀).

    미완료(processing/failed) 기록은 original_media_url 도 보존한다 — 빼면
    트랜스코딩 실패 영상의 원본이 다음 실행에 삭제돼 재시도가 불가능해진다
    (2026-07-21 failed 1건이 실제로 원본을 잃음). done 기록의 원본은 워커가
    이미 지웠거나, 삭제 실패로 남은 것이라 여기서 정리돼야 하므로 제외.
    """
    referenced: set[str] = set()
    stmt = select(
        ClimbingLog.media_url,
        ClimbingLog.thumbnail_url,
        ClimbingLog.original_media_url,
        ClimbingLog.media_status,
    ).where(
        (ClimbingLog.deleted_at.is_(None)) | (ClimbingLog.deleted_at > cutoff),
    )
    rows = (await session.execute(stmt)).all()
    for media_url, thumbnail_url, original_media_url, media_status in rows:
        original = original_media_url if media_status != "done" else None
        for url in (media_url, thumbnail_url, original):
            if not url:
                continue
            key = media.extract_object_key(url)
            if key:
                referenced.add(key)
    return referenced


def find_orphans(
    objects: Iterable[tuple[str, datetime | None]],
    referenced: set[str],
    now: datetime,
) -> tuple[list[str], int]:
    """(삭제 대상 키 목록, 너무 최근이라 건너뛴 개수).

    수정 시각을 모르는 객체는 안전하게 건너뛴다(삭제는 되돌릴 수 없음).
    """
    min_age_cutoff = now - timedelta(hours=MIN_AGE_HOURS)
    orphans: list[str] = []
    skipped_recent = 0
    for key, last_modified in objects:
        if key in referenced:
            continue
        if last_modified is None or last_modified > min_age_cutoff:
            skipped_recent += 1
            continue
        orphans.append(key)
    return orphans, skipped_recent


async def main(apply: bool) -> None:
    init_engine()
    settings = get_settings()
    media = MediaService(settings, internal=True)
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    sm = get_sessionmaker()

    async with sm() as session:
        referenced = await collect_referenced_keys(session, media, cutoff)
    all_objects = list(media.list_objects())
    orphans, skipped_recent = find_orphans(all_objects, referenced, now)

    logger.info(
        "media_reconcile_scan",
        total_objects=len(all_objects),
        referenced=len(referenced),
        orphans=len(orphans),
        skipped_recent=skipped_recent,
        retention_days=RETENTION_DAYS,
        min_age_hours=MIN_AGE_HOURS,
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
