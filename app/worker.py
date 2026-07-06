"""ARQ 워커 — 영상 트랜스코딩 백그라운드 처리.

실행: arq app.worker.WorkerSettings

구조:
- startup: DB 엔진 초기화
- transcode_video: 큐에서 꺼내 실행하는 태스크 (log_id 받아 압축)
- 태스크마다 새 DB 세션 + 서비스 조립
"""

import uuid

import structlog
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.domain.climbing.repository import ClimbingRepository
from app.domain.media.service import MediaService
from app.domain.media.transcode_service import TranscodeService
from app.domain.notifications.repository import NotificationRepository
from app.domain.notifications.service import NotificationService
from app.infra.db import get_sessionmaker
from app.infra.db.engine import close_engine, init_engine

logger = structlog.get_logger()


async def transcode_video(ctx: dict, log_id: str) -> None:
    """영상 트랜스코딩 태스크 (ARQ 가 큐에서 꺼내 실행)."""
    settings = get_settings()
    sm = get_sessionmaker()
    logger.info("worker_transcode_received", log_id=log_id)
    async with sm() as session:
        media = MediaService(settings, internal=True)
        climbing_repo = ClimbingRepository(session)
        notification_service = NotificationService(
            session, NotificationRepository(session)
        )
        service = TranscodeService(
            settings, media, climbing_repo, notification_service
        )
        await service.transcode(uuid.UUID(log_id))


async def on_startup(ctx: dict) -> None:
    init_engine()
    logger.info("worker_started")


async def on_shutdown(ctx: dict) -> None:
    await close_engine()
    logger.info("worker_stopped")


def _redis_settings() -> RedisSettings:
    s = get_settings()
    return RedisSettings(
        host=s.redis_host,
        port=s.redis_port,
        password=s.redis_password or None,
        database=s.arq_redis_db,
    )


class WorkerSettings:
    """ARQ 워커 설정 (arq app.worker.WorkerSettings 로 실행)."""

    functions = [transcode_video]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = _redis_settings()
    max_jobs = get_settings().transcode_max_jobs
    job_timeout = get_settings().transcode_timeout + 120
    max_tries = 2
