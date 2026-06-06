"""헬스체크 엔드포인트.

K8s probe 패턴:
- /health/live  → Liveness:  "프로세스가 살아있는가" (재시작 트리거)
- /health/ready → Readiness: "트래픽 받을 준비 됐는가" (Service에서 제외)

ArgoCD는 Pod의 Ready 상태로 Application의 Healthy를 판정합니다.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, status
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    """헬스체크 응답."""

    status: str
    app: str
    version: str
    environment: str
    timestamp: str


@router.get(
    "/live",
    status_code=status.HTTP_200_OK,
    summary="Liveness probe",
    response_model=HealthResponse,
)
async def liveness() -> HealthResponse:
    """프로세스 생존 여부.

    이 응답이 실패하면 K8s가 Pod을 재시작합니다.
    단순히 'FastAPI가 응답하는가' 만 확인.
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
    status_code=status.HTTP_200_OK,
    summary="Readiness probe",
    response_model=HealthResponse,
)
async def readiness() -> HealthResponse:
    """트래픽 수신 준비 여부.

    여기서 DB/Redis 연결 등을 확인합니다.
    실패 시 K8s가 Service의 endpoint에서 제외 → 트래픽 미라우팅.

    다음 세션에서 DB 연결 체크 추가 예정.
    """
    settings = get_settings()
    # TODO: DB ping, Redis ping 등 추가
    return HealthResponse(
        status="ready",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        timestamp=datetime.now(UTC).isoformat(),
    )
