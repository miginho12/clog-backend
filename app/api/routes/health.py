"""헬스체크 엔드포인트.

K8s probe 패턴:
- /health/live  → Liveness:  "프로세스가 살아있는가" (재시작 트리거)
- /health/ready → Readiness: "트래픽 받을 준비 됐는가" (Service 라우팅)

Day 11B: Redis 의존성 추가.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.core.config import get_settings
from app.infra.db import ping_db
from app.infra.redis import ping_redis

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    environment: str
    timestamp: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    app: str
    version: str
    environment: str
    timestamp: str
    dependencies: dict[str, Literal["ok", "fail"]]


@router.get(
    "/live",
    status_code=status.HTTP_200_OK,
    response_model=HealthResponse,
)
async def liveness() -> HealthResponse:
    """프로세스 생존 여부."""
    settings = get_settings()
    return HealthResponse(
        status="alive",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        timestamp=datetime.now(UTC).isoformat(),
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={
        503: {"model": ReadinessResponse},
    },
)
async def readiness(response: Response) -> ReadinessResponse:
    """트래픽 수신 준비 여부.

    의존성:
    - PostgreSQL (Day 8 부터)
    - Redis (Day 11B 부터)
    """
    settings = get_settings()

    # 의존성 병렬 확인 (둘 다 비동기라 가능)
    import asyncio

    db_ok, redis_ok = await asyncio.gather(
        ping_db(),
        ping_redis(),
    )

    dependencies = {
        "database": "ok" if db_ok else "fail",
        "redis": "ok" if redis_ok else "fail",
    }

    all_ok = all(v == "ok" for v in dependencies.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if all_ok else "not_ready",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        timestamp=datetime.now(UTC).isoformat(),
        dependencies=dependencies,
    )
