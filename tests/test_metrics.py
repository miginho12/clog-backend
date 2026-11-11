"""HTTP 메트릭 미들웨어 테스트 (감사 L1 B / ADR-093).

FastAPI 0.137+ 의 라우터 트리(_IncludedRouter)에서도
- API 가 500 이 되지 않고
- path_template 라벨이 실제 URL 이 아니라 템플릿으로 모이며(카디널리티)
- 알림 룰이 쓰는 메트릭(responses·processing_time·exceptions)이 그대로 기록되는지 확인한다.
"""

import uuid
from types import ModuleType

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app import main
from app.core.metrics import UNMATCHED, PrometheusMiddleware, metrics
from app.main import app


def _value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_included_router_route_uses_template(client):
    template = "/climbing-logs/{log_id}"
    before = _value("starlette_requests_total", method="GET", path_template=template)
    log_id = str(uuid.uuid4())

    res = client.get(f"/climbing-logs/{log_id}")

    assert res.status_code != 500  # 0.137+ 에서 starlette-prometheus 는 여기서 500
    assert _value("starlette_requests_total", method="GET", path_template=template) == before + 1
    assert _value(
        "starlette_responses_total", method="GET", path_template=template, status_code=str(res.status_code)
    ) >= 1
    exposed = client.get("/metrics").text
    assert log_id not in exposed  # 실제 URL 이 라벨로 새지 않는다


def test_health_route_label_matches_alert_rule_exclusion(client):
    # 알림 룰은 path_template!~"/metrics|/health/.*" 로 헬스체크를 뺀다 → 템플릿이 그 형태여야 한다
    before = _value("starlette_requests_total", method="GET", path_template="/health/live")
    assert client.get("/health/live").status_code == 200
    assert _value("starlette_requests_total", method="GET", path_template="/health/live") == before + 1


def test_metrics_endpoint_labels_itself_and_exposes_format(client):
    res = client.get("/metrics")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    res = client.get("/metrics")
    assert 'starlette_responses_total{method="GET",path_template="/metrics",status_code="200"}' in res.text


def test_unmatched_path_collapses_to_single_label(client):
    before = _value("starlette_requests_total", method="GET", path_template=UNMATCHED)
    for _ in range(3):
        assert client.get(f"/scanner-{uuid.uuid4()}").status_code == 404
    assert _value("starlette_requests_total", method="GET", path_template=UNMATCHED) == before + 3


def test_unhandled_exception_and_in_progress():
    # 처리되지 않은 예외: 템플릿 라벨 + exception_type 기록, 500 응답 집계, 진행 중 게이지 원복
    router = APIRouter(prefix="/items")  # 우리 앱과 같은 구조: prefix 는 APIRouter 생성자에

    @router.get("/{item_id}/boom")
    async def boom(item_id: str) -> None:
        raise RuntimeError("boom")

    test_app = FastAPI()
    test_app.add_middleware(PrometheusMiddleware)
    test_app.add_route("/metrics", metrics)
    test_app.include_router(router)
    template = "/items/{item_id}/boom"
    exc = {"method": "GET", "path_template": template, "exception_type": "RuntimeError"}
    res500 = {"method": "GET", "path_template": template, "status_code": "500"}
    before_exc = _value("starlette_exceptions_total", **exc)
    before_500 = _value("starlette_responses_total", **res500)

    with TestClient(test_app, raise_server_exceptions=False) as c:
        assert c.get("/items/abc/boom").status_code == 500

    assert _value("starlette_exceptions_total", **exc) == before_exc + 1
    assert _value("starlette_responses_total", **res500) == before_500 + 1
    assert _value("starlette_requests_in_progress", method="GET") == 0


def test_route_paths_are_full_paths():
    # app.core.metrics 는 scope["route"].path 를 라벨로 쓴다 → 이 값이 실제 전체 경로여야 한다.
    # include_router(prefix=...) 나 라우터 중첩을 쓰면 바깥 prefix 가 빠져 이 테스트가 실패한다
    # → 그때는 prefix 를 APIRouter(prefix=...) 로 옮기거나 metrics.path_template 을 고칠 것.
    routers = [
        obj
        for module in vars(main).values()
        if isinstance(module, ModuleType) and module.__name__.startswith("app.api.routes.")
        for obj in vars(module).values()
        if isinstance(obj, APIRouter)
    ]
    route_paths = {r.path for router in routers for r in router.routes if isinstance(r, APIRoute)}
    route_paths |= {r.path for r in app.routes if isinstance(r, APIRoute)}  # app 에 직접 붙인 "/"
    openapi_paths = set(app.openapi()["paths"])

    assert len(routers) >= 14
    assert openapi_paths, "openapi 경로가 비어 있음"
    assert openapi_paths <= route_paths, f"route.path 에 없는 전체 경로(prefix 누락): {openapi_paths - route_paths}"
