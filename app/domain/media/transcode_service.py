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
from app.domain.climbing.models import ClimbingLog
from app.domain.climbing.repository import ClimbingRepository
from app.domain.media.service import MediaService
from app.domain.notifications.service import NotificationService

logger = structlog.get_logger()


class TranscodeError(Exception):
    """트랜스코딩 실패."""


async def extract_video_thumbnail(settings: Settings, src: str, dst: str) -> None:
    """영상에서 대표 프레임 1장을 JPG 로 추출 (그리드 정지 썸네일용).

    'thumbnail' 필터는 배치(기본 100프레임) 내에서 장면을 대표하는 프레임을
    고른다 — 항상 0프레임을 쓰면 페이드인/암전 프레임이 잡힐 수 있어서.
    그리드 타일용이라 본편보다 훨씬 작게 리사이즈.
    모듈 레벨 함수인 이유: 신규 업로드(TranscodeService) 뿐 아니라 기존
    영상 백필 스크립트(backfill_video_thumbnails.py)에서도 동일 로직을
    재사용해야 커맨드가 둘로 갈라지지 않는다.

    -bsf:v mjpeg2jpeg: 이 이미지의 ffmpeg 빌드(5.1.9)의 mjpeg 인코더가 만드는
    출력엔 표준 JFIF APP0 헤더가 빠져있다(2026-07-30 실제 prod 산출물을 바이트
    단위로 까본 결과 확인 — 데스크톱에서 curl로 받으면 file/ffmpeg는 문제없이
    읽지만, Android WebView 이미지 디코더가 이걸 못 읽어서 그리드에서 깨진
    미디어 아이콘으로 보였을 가능성이 높음). 이 비트스트림 필터를 거치면
    표준 JFIF 헤더가 붙은 완전한 JPEG이 나온다(재디코딩 검증 완료).
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        src,
        "-vf",
        (
            "thumbnail,"
            f"scale=w={settings.thumbnail_max_dimension}:h={settings.thumbnail_max_dimension}"
            ":force_original_aspect_ratio=decrease"
        ),
        "-frames:v",
        "1",
        "-bsf:v",
        "mjpeg2jpeg",
        dst,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=settings.transcode_timeout)
    except TimeoutError:
        proc.kill()
        raise TranscodeError("ffmpeg thumbnail timeout") from None
    if proc.returncode != 0:
        tail = (stderr or b"").decode(errors="replace")[-500:]
        raise TranscodeError(f"ffmpeg thumbnail failed: {tail}")


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
            "ffmpeg",
            "-y",
            "-i",
            src,
            # 비율 유지 + 긴 변을 max_dimension 이하로 (세로/가로 무관).
            # force_original_aspect_ratio=decrease: 원본보다 크게 안 늘림.
            # 짝수 강제(-2 대신 trunc): H.264 는 너비/높이가 짝수여야 함.
            "-vf",
            (
                f"scale=w={s.transcode_max_dimension}:h={s.transcode_max_dimension}"
                ":force_original_aspect_ratio=decrease"
                ",scale=trunc(iw/2)*2:trunc(ih/2)*2"
            ),
            "-c:v",
            "libx264",
            "-preset",
            s.transcode_preset,
            "-crf",
            str(s.transcode_crf),
            "-c:a",
            "aac",
            "-b:a",
            s.transcode_audio_bitrate,
            "-movflags",
            "+faststart",
            dst,
        ]
        logger.info("ffmpeg_start", src=src, dst=dst)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=s.transcode_timeout)
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
            thumb = os.path.join(tmpdir, "thumbnail.jpg")
            try:
                self.media.download_to_file(object_key, src)
                await self._run_ffmpeg(src, dst)
                if not Path(dst).exists() or Path(dst).stat().st_size == 0:
                    raise TranscodeError("output empty")
                user_prefix = object_key.rsplit("/", 1)[0]
                new_key = f"{user_prefix}/{uuid.uuid4().hex}.mp4"
                public_url = self.media.upload_file(new_key, dst, content_type="video/mp4")
            except Exception as e:
                logger.error("transcode_failed", log_id=str(log_id), error=str(e))
                await self._mark_failed(log, str(e))
                return

            # 썸네일 추출/업로드 실패는 영상 게시 자체를 막지 않는다 — 게시물은
            # 이미 정상 압축됐으니 done 으로 공개하고, 썸네일만 null 로 남겨서
            # 프론트가 플레이스홀더로 대체 표시(백필 스크립트가 나중에 채움).
            thumbnail_url: str | None = None
            try:
                await extract_video_thumbnail(self.settings, dst, thumb)
                if Path(thumb).exists() and Path(thumb).stat().st_size > 0:
                    thumb_key = f"{user_prefix}/{uuid.uuid4().hex}.jpg"
                    thumbnail_url = self.media.upload_file(
                        thumb_key, thumb, content_type="image/jpeg"
                    )
            except Exception as e:
                logger.warning("transcode_thumbnail_failed", log_id=str(log_id), error=str(e))

        await self.climbing_repo.update(
            log,
            media_url=public_url,
            media_status="done",
            thumbnail_url=thumbnail_url,
        )
        await self.notification_service.notify_media_ready(
            recipient_id=log.user_id, climbing_log_id=log.id
        )
        # DB 를 먼저 확정(commit)한 뒤에 원본을 삭제한다.
        # 순서가 중요: 삭제를 commit 전에 하면, 이후 단계 실패로 롤백돼도
        # MinIO 삭제는 롤백 안 돼 원본이 사라진 채 DB 만 processing 으로 되돌아가
        # 재시도 불가능해진다 (부분 실패). commit 성공 후 삭제해야 안전.
        await self.climbing_repo.session.commit()
        try:
            self.media.delete_object(object_key)
        except Exception as e:
            # 삭제 실패해도 이미 done 커밋됐으니 게시엔 문제없음.
            # 남은 원본은 media-reconcile CronJob 이 정리.
            logger.warning(
                "transcode_original_delete_failed",
                log_id=str(log_id),
                error=str(e),
            )
        logger.info("transcode_done", log_id=str(log_id), url=public_url)

    async def _mark_failed(self, log: ClimbingLog, reason: str) -> None:
        """실패 처리: status=failed + 실패 알림. 원본 유지."""
        await self.climbing_repo.update(log, media_status="failed")
        await self.notification_service.notify_media_failed(
            recipient_id=log.user_id, climbing_log_id=log.id
        )
        await self.climbing_repo.session.commit()
        logger.info("transcode_marked_failed", log_id=str(log.id), reason=reason)
