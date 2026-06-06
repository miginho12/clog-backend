"""헬스체크 엔드포인트 테스트.

실행:
  uv run pytest -v
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root() -> None:
    """루트 엔드포인트가 환영 메시지를 반환."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "Hello, Clog!" in data["message"]
    assert data["app"] == "clog-backend"


def test_liveness() -> None:
    """Liveness probe는 200을 반환."""
    response = client.get("/health/live")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "alive"


def test_readiness() -> None:
    """Readiness probe는 200을 반환."""
    response = client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
