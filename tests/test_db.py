"""DB 연결 + readiness 엔드포인트 테스트.

실행: uv run pytest tests/test_db.py -v

⚠️ 이 테스트는 실제 PostgreSQL 이 필요해요.
로컬에서:
  kubectl port-forward -n dev svc/postgres 5432:5432
또는 docker compose 로 띄운 PostgreSQL.
"""

import pytest
from fastapi.testclient import TestClient

from app.infra.db import close_engine, init_engine, ping_db
from app.main import app


@pytest.fixture(autouse=True)
async def setup_db():
    """각 테스트 전후로 엔진 생성/정리."""
    init_engine()
    yield
    await close_engine()


async def test_ping_db_returns_true_when_db_alive():
    """DB 가 살아있으면 ping_db() 가 True 반환."""
    result = await ping_db()
    assert result is True


def test_readiness_endpoint_returns_200_when_all_ok():
    """모든 의존성 정상이면 /health/ready 가 200 반환."""
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["dependencies"]["database"] == "ok"


def test_liveness_endpoint_unaffected_by_db():
    """liveness 는 DB 와 무관하게 항상 200."""
    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"
