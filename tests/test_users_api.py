"""User API 테스트.

실행: uv run pytest tests/test_users_api.py -v

⚠️ 실제 PostgreSQL 필요 (port-forward 켜진 상태).
이건 통합 테스트. 다음 세션에 unit 테스트 분리.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.infra.db import close_engine, init_engine
from app.main import app


@pytest.fixture(autouse=True)
async def setup_db():
    init_engine()
    yield
    await close_engine()


def _unique_payload() -> dict:
    """매 테스트마다 고유한 payload."""
    suffix = uuid.uuid4().hex[:8]
    return {
        "email": f"test_{suffix}@example.com",
        "nickname": f"nick_{suffix}",
        "auth_provider": "kakao",
        "auth_provider_id": f"kakao_{suffix}",
    }


def test_create_user_success():
    """회원 생성 성공."""
    with TestClient(app) as client:
        payload = _unique_payload()
        response = client.post("/users", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["email"] == payload["email"]
    assert data["nickname"] == payload["nickname"]
    assert data["auth_provider"] == "kakao"
    assert "id" in data
    assert "created_at" in data
    # 민감 필드는 응답에 없어야
    assert "auth_provider_id" not in data
    assert "deleted_at" not in data


def test_create_user_email_already_exists():
    """이메일 중복 시 409."""
    with TestClient(app) as client:
        payload = _unique_payload()

        # 첫 생성
        r1 = client.post("/users", json=payload)
        assert r1.status_code == 201

        # 같은 이메일 + 다른 정보로 시도
        payload2 = _unique_payload()
        payload2["email"] = payload["email"]  # 이메일만 같게
        r2 = client.post("/users", json=payload2)

    assert r2.status_code == 409
    error = r2.json()["error"]
    assert error["code"] == "EMAIL_ALREADY_EXISTS"


def test_create_user_invalid_email():
    """잘못된 이메일 형식 → 422."""
    with TestClient(app) as client:
        payload = _unique_payload()
        payload["email"] = "not-an-email"
        response = client.post("/users", json=payload)

    assert response.status_code == 422


def test_create_user_nickname_too_short():
    """닉네임이 너무 짧으면 422."""
    with TestClient(app) as client:
        payload = _unique_payload()
        payload["nickname"] = "a"  # 1글자
        response = client.post("/users", json=payload)

    assert response.status_code == 422


def test_get_user_success():
    """단일 조회."""
    with TestClient(app) as client:
        # 생성
        created = client.post("/users", json=_unique_payload())
        user_id = created.json()["id"]

        # 조회
        response = client.get(f"/users/{user_id}")

    assert response.status_code == 200
    assert response.json()["id"] == user_id


def test_get_user_not_found():
    """존재하지 않는 ID → 404."""
    random_id = str(uuid.uuid4())
    with TestClient(app) as client:
        response = client.get(f"/users/{random_id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "USER_NOT_FOUND"


def test_list_users():
    """목록 조회."""
    with TestClient(app) as client:
        # 사용자 몇 명 생성
        for _ in range(3):
            client.post("/users", json=_unique_payload())

        response = client.get("/users?page=1&page_size=10")

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert data["page"] == 1
    assert len(data["items"]) <= 10
