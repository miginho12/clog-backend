"""트랜스코딩 서비스 — ffmpeg 영상 압축.

흐름 (워커에서 호출):
1. climbing_log 의 original_media_url 에서 원본 다운로드 (MinIO, internal)
2. ffmpeg 로 압축 (720p CRF23, 벤치마크 근거)
3. 압축본을 MinIO 에 업로드
4. climbing_log 갱신: media_url=압축본, media_status=done
5. 원본 삭제
6. 완료 알림 (media_ready)

실패 시: media_status=failed + 실패 알림 (media_failed). 원본은 유지.
"""

import asyncio
import os
import tempfile
import uuid
from pathlib import Path

import structlog

from app.core.config import Settings
from app.domain.climbing.repository import ClimbingRepository
from app.domain.media.service import MediaService
from app.domain.notifications.service import NotificationService

logger = structlog.get_logger()


class TranscodeError(Exception):
    """트랜스코딩 실패."""


class TranscodeService:
    def __init__(
        self,
        settings: Settings,
        media_service: MediaService,
        climbing_repo: ClimbingRepository,
        notification_service: NotificationService,
    ):
        self.settings = settings
        self.media = media_service
        self.climbing_repo = climbing_repo
        self.notification_service = notification_service

    async def _run_ffmpeg(self, src: str, dst: str) -> None:
        """ffmpeg 로 720p CRF23 압축 (비동기 subprocess)."""
        s = self.settings
        cmd = [
            "ffmpeg", "-y", "-i", src,
            "-vf", f"scale={s.transcode_scale}",
            "-c:v", "libx264",
            "-preset", s.transcode_preset,
            "-crf", str(s.transcode_crf),
            "-c:a", "aac",
            "-b:a", s.transcode_audio_bitrate,
            "-movflags", "+faststart",
            dst,
        ]
        logger.info("ffmpeg_start", src=src, dst=dst)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=s.transcode_timeout
            )
        except TimeoutError:
            proc.kill()
            raise TranscodeError("ffmpeg timeout") from None
        if proc.returncode != 0:
            tail = (stderr or b"").decode(errors="replace")[-500:]
            raise TranscodeError(f"ffmpeg failed: {tail}")

    async def transcode(self, log_id: uuid.UUID) -> None:
        """게시물의 원본 영상을 압축하고 상태를 갱신한다."""
        log = await self.climbing_repo.get_by_id(log_id)
        if log is None:
            logger.warning("transcode_log_not_found", log_id=str(log_id))
            return
        if log.original_media_url is None:
            logger.warning("transcode_no_original", log_id=str(log_id))
            return

        object_key = self.media.extract_object_key(log.original_media_url)
        if object_key is None:
            await self._mark_failed(log, "invalid original url")
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "original.mp4")
            dst = os.path.join(tmpdir, "compressed.mp4")
            try:
                self.media.download_to_file(object_key, src)
                await self._run_ffmpeg(src, dst)
                if not Path(dst).exists() or Path(dst).stat().st_size == 0:
                    raise TranscodeError("output empty")
                user_prefix = object_key.rsplit("/", 1)[0]
                new_key = f"{user_prefix}/{uuid.uuid4().hex}.mp4"
                public_url = self.media.upload_file(
                    new_key, dst, content_type="video/mp4"
                )
            except Exception as e:
                logger.error(
                    "transcode_failed", log_id=str(log_id), error=str(e)
                )
                await self._mark_failed(log, str(e))
                return

        await self.climbing_repo.update(
            log, media_url=public_url, media_status="done"
        )
        try:
            self.media.delete_object(object_key)
        except Exception as e:
            logger.warning(
                "transcode_original_delete_failed",
                log_id=str(log_id), error=str(e),
            )
        await self.notification_service.notify_media_ready(
            recipient_id=log.user_id, climbing_log_id=log.id
        )
        await self.climbing_repo.session.commit()
        logger.info("transcode_done", log_id=str(log_id), url=public_url)

    async def _mark_failed(self, log, reason: str) -> None:
        """실패 처리: status=failed + 실패 알림. 원본 유지."""
        await self.climbing_repo.update(log, media_status="failed")
        await self.notification_service.notify_media_failed(
            recipient_id=log.user_id, climbing_log_id=log.id
        )
        await self.climbing_repo.session.commit()
        logger.info("transcode_marked_failed", log_id=str(log.id), reason=reason)
