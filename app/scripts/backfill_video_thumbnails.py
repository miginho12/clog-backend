"""기존 영상 게시물 정지 썸네일 백필.

thumbnail_url 컬럼 추가(2026-07-30) 이전에 압축 완료된 영상들은
thumbnail_url 이 비어있다. 대상 게시물마다 압축본(media_url)을 다운로드해
extract_video_thumbnail 로 프레임을 뽑고 업로드, DB 갱신.

실행:
  python -m app.scripts.backfill_video_thumbnails                     # dry-run (조회만, 처리 안 함)
  python -m app.scripts.backfill_video_thumbnails --apply             # thumbnail_url 없는 것만 처리
  python -m app.scripts.backfill_video_thumbnails --apply --force     # 이미 있는 것도 재생성
                                                                       # (예: ffmpeg 인코딩 버그로 재생성 필요할 때)
"""

import argparse
import asyncio
import os
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.climbing.models import ClimbingLog
from app.domain.media.service import MediaService
from app.domain.media.transcode_service import extract_video_thumbnail
from app.infra.db import get_sessionmaker
from app.infra.db.engine import close_engine, init_engine

logger = get_logger(__name__)


async def main(apply: bool, force: bool) -> None:
    init_engine()
    settings = get_settings()
    media = MediaService(settings, internal=True)
    sm = get_sessionmaker()

    async with sm() as session:
        conditions = [
            ClimbingLog.media_type == "video",
            ClimbingLog.media_status == "done",
            ClimbingLog.media_url.is_not(None),
        ]
        if not force:
            conditions.append(ClimbingLog.thumbnail_url.is_(None))
        stmt = select(ClimbingLog).where(*conditions)
        targets = (await session.execute(stmt)).scalars().all()

        logger.info("backfill_scan", targets=len(targets), apply=apply, force=force)

        done = 0
        failed = 0
        for log in targets:
            if not apply:
                logger.info("backfill_dryrun", log_id=str(log.id))
                continue

            assert log.media_url is not None
            object_key = media.extract_object_key(log.media_url)
            if object_key is None:
                logger.warning("backfill_invalid_url", log_id=str(log.id), url=log.media_url)
                failed += 1
                continue

            with tempfile.TemporaryDirectory() as tmpdir:
                src = os.path.join(tmpdir, "video.mp4")
                thumb = os.path.join(tmpdir, "thumbnail.jpg")
                try:
                    media.download_to_file(object_key, src)
                    await extract_video_thumbnail(settings, src, thumb)
                    if not Path(thumb).exists() or Path(thumb).stat().st_size == 0:
                        raise RuntimeError("thumbnail empty")
                    user_prefix = object_key.rsplit("/", 1)[0]
                    thumb_key = f"{user_prefix}/{uuid.uuid4().hex}.jpg"
                    thumbnail_url = media.upload_file(thumb_key, thumb, content_type="image/jpeg")
                except Exception as e:  # noqa: BLE001
                    logger.warning("backfill_failed", log_id=str(log.id), error=str(e))
                    failed += 1
                    continue

            log.thumbnail_url = thumbnail_url
            await session.commit()
            done += 1
            logger.info("backfill_done", log_id=str(log.id), url=thumbnail_url)

    logger.info(
        "backfill_summary",
        targets=len(targets),
        done=done if apply else 0,
        failed=failed if apply else 0,
        mode="apply" if apply else "dry-run",
    )
    await close_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="실제 처리 (기본은 dry-run 조회만)")
    parser.add_argument("--force", action="store_true", help="thumbnail_url 이미 있어도 재생성")
    args = parser.parse_args()
    asyncio.run(main(args.apply, args.force))
