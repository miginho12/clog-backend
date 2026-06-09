"""헬스체크 엔드포인트.

K8s probe 패턴:
- /health/live  → Liveness:  "프로세스가 살아있는가" (재시작 트리거)
- /health/ready → Readiness: "트래픽 받을 준비 됐는가" (Service 라우팅)

면접 답변 자산:
- liveness 는 FastAPI 자체 응답만 (단순)
- readiness 는 의존하는 외부 시스템 (DB) 까지 확인 (정확함)
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.core.config import get_settings
from app.infra.db import ping_db

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    """기본 헬스체크 응답."""

    status: str
    app: str
    version: str
    environment: str
    timestamp: str


class ReadinessResponse(BaseModel):
    """readiness 의 상세 응답.

    각 의존성의 상태를 개별 표시.
    """

    status: Literal["ready", "not_ready"]
    app: str
    version: str
    environment: str
    timestamp: str
    dependencies: dict[str, Literal["ok", "fail"]]


@router.get(
    "/live",
    status_code=status.HTTP_200_OK,
    summary="Liveness probe",
    response_model=HealthResponse,
)
async def liveness() -> HealthResponse:
    """프로세스 생존 여부.

    이 응답이 실패하면 K8s가 Pod을 재시작.
    단순히 'FastAPI가 응답하는가' 만 확인.

    DB 같은 외부 의존성 확인 X.
    DB 가 잠시 죽었다고 Pod 재시작하는 건 비합리적.
    """
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
    summary="Readiness probe",
    response_model=ReadinessResponse,
    responses={
        503: {
            "model": ReadinessResponse,
            "description": "서비스가 트래픽 받을 준비 X",
        },
    },
)
async def readiness(response: Response) -> ReadinessResponse:
    """트래픽 수신 준비 여부.

    여기서 DB 같은 외부 의존성 확인.
    실패 시 K8s 가 Service 의 endpoint 에서 제외 → 트래픽 미라우팅.

    응답 코드:
    - 200: 모든 의존성 정상
    - 503: 하나라도 실패 (Service Unavailable)
    """
    settings = get_settings()

    # DB ping
    db_ok = await ping_db()

    dependencies = {
        "database": "ok" if db_ok else "fail",
    }

    # 하나라도 실패면 503
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
