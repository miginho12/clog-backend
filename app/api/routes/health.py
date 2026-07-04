"""Health check 엔드포인트."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import get_settings
from app.core.rate_limit import RateLimits, limiter
from app.infra.db.engine import get_engine
from app.infra.redis import get_redis

router = APIRouter(prefix="/health", tags=["health"])


class HealthLiveResponse(BaseModel):
    status: str
    app: str
    version: str
    environment: str
    timestamp: datetime


class HealthReadyResponse(BaseModel):
    status: str
    app: str
    version: str
    environment: str
    timestamp: datetime
    dependencies: dict[str, str]


@router.get(
    "/live",
    response_model=HealthLiveResponse,
    summary="Liveness probe",
)
@limiter.limit(RateLimits.HEALTH)
async def health_live(request: Request) -> HealthLiveResponse:
    """프로세스 동작 여부.

    Rate limit 1000/min — 실제 무제한.
    """
    settings = get_settings()
    return HealthLiveResponse(
        status="alive",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        timestamp=datetime.now(UTC),
    )


@router.get(
    "/ready",
    response_model=HealthReadyResponse,
    summary="Readiness probe",
)
@limiter.limit(RateLimits.HEALTH)
async def health_ready(request: Request) -> HealthReadyResponse:
    """의존성 (DB, Redis) 상태 포함."""
    import asyncio

    settings = get_settings()
    dependencies: dict[str, str] = {}

    async def _check_db() -> None:
        try:
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            dependencies["database"] = "ok"
        except Exception as e:
            dependencies["database"] = f"error: {type(e).__name__}"

    async def _check_redis() -> None:
        try:
            redis = get_redis()
            await redis.ping()
            dependencies["redis"] = "ok"
        except Exception as e:
            dependencies["redis"] = f"error: {type(e).__name__}"

    await asyncio.gather(_check_db(), _check_redis())

    all_ok = all(v == "ok" for v in dependencies.values())
    if not all_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "not_ready",
                "dependencies": dependencies,
            },
        )

    return HealthReadyResponse(
        status="ready",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        timestamp=datetime.now(UTC),
        dependencies=dependencies,
    )
