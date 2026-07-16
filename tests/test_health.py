"""헬스체크 엔드포인트 테스트.

실행:
  uv run pytest -v
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """lifespan(init_engine/init_redis)까지 실행되도록 context manager로 생성."""
    with TestClient(app) as c:
        yield c


def test_root(client: TestClient) -> None:
    """루트 엔드포인트가 환영 메시지를 반환."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "Hello, Clog!" in data["message"]
    assert data["app"] == "clog-backend"


def test_liveness(client: TestClient) -> None:
    """Liveness probe는 200을 반환."""
    response = client.get("/health/live")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "alive"


def test_readiness(client: TestClient) -> None:
    """Readiness probe는 200을 반환."""
    response = client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
