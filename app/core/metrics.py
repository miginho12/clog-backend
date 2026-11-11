"""HTTP 요청 Prometheus 메트릭 (starlette-prometheus 대체, 감사 L1 B / ADR-093).

FastAPI 0.137 부터 ``app.routes`` 가 평면 목록이 아니라 트리(``_IncludedRouter``)라서,
최상위 route 를 순회하며 ``route.path`` 를 읽던 starlette-prometheus 0.10(2024-09 이후 방치)이
include 된 모든 API 에서 AttributeError → 500 을 냈다.

여기서는 경로 템플릿을 **라우팅이 끝난 뒤** ``scope["route"]`` 에서 읽는다(Starlette·FastAPI 가
매칭한 route 를 넣어 두는 표준 키). FastAPI 비공개 API 에 기대지 않는다.

⚠️ 한계: ``route.path`` 에는 **APIRouter(prefix=...) 로 준 자기 prefix 까지만** 들어 있다.
``include_router(r, prefix="/x")`` 나 라우터 중첩(라우터가 라우터를 include)을 쓰면 바깥 prefix 가
라벨에서 빠진다(예: ``/x/items/{id}`` → ``/items/{id}``). 전체 경로는 FastAPI 비공개 scope 에만 있어
쓰지 않았다. 대신 ``tests/test_metrics.py::test_route_paths_are_full_paths`` 가 이 가정
(include 시 prefix·중첩 없음)을 지킨다.

메트릭 이름·도움말·라벨은 starlette-prometheus 와 같게 유지해 gitops 알림 룰
(``starlette_responses_total``·``..._processing_time_seconds_bucket``·``starlette_exceptions_total``)을
그대로 쓴다. 차이는 두 가지:

- 매칭되지 않은 경로는 실제 URL 대신 ``UNMATCHED`` 하나로 모은다(스캐너 404 로 시계열이
  늘어나던 카디널리티 문제 제거).
- ``starlette_requests_in_progress`` 는 처리 전에는 경로를 알 수 없어 ``method`` 라벨만 둔다
  (2026-11-11 기준 룰·대시보드 사용처 0).
"""

import os
import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.multiprocess import MultiProcessCollector
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Match
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR

UNMATCHED = "<unmatched>"

REQUESTS = Counter(
    "starlette_requests_total", "Total count of requests by method and path.", ["method", "path_template"]
)
RESPONSES = Counter(
    "starlette_responses_total",
    "Total count of responses by method, path and status codes.",
    ["method", "path_template", "status_code"],
)
REQUESTS_PROCESSING_TIME = Histogram(
    "starlette_requests_processing_time_seconds",
    "Histogram of requests processing time by path (in seconds)",
    ["method", "path_template"],
)
EXCEPTIONS = Counter(
    "starlette_exceptions_total",
    "Total count of exceptions raised by path and exception type",
    ["method", "path_template", "exception_type"],
)
REQUESTS_IN_PROGRESS = Gauge(
    "starlette_requests_in_progress",
    "Gauge of requests by method currently being processed",
    ["method"],
)


def path_template(request: Request) -> str:
    """라우팅이 끝난 요청의 경로 템플릿(예: ``/climbing-logs/{log_id}``)."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    # add_route 로 붙인 최상위 Route(/metrics, /docs 등)는 scope["route"] 가 없을 수 있다
    # → 공개 API 인 matches() 로 최상위에서만 찾는다(_IncludedRouter 는 path 가 없어 건너뜀)
    routes: list[BaseRoute] = request.app.routes
    for candidate in routes:
        candidate_path = getattr(candidate, "path", None)
        if not isinstance(candidate_path, str):
            continue
        match, _ = candidate.matches(request.scope)
        if match == Match.FULL:
            return candidate_path
    return UNMATCHED


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        method = request.method
        status_code = HTTP_500_INTERNAL_SERVER_ERROR
        REQUESTS_IN_PROGRESS.labels(method=method).inc()
        before = time.perf_counter()
        try:
            response = await call_next(request)
        except BaseException as e:
            EXCEPTIONS.labels(method=method, path_template=path_template(request), exception_type=type(e).__name__).inc()
            raise
        else:
            status_code = response.status_code
            REQUESTS_PROCESSING_TIME.labels(method=method, path_template=path_template(request)).observe(
                time.perf_counter() - before
            )
            return response
        finally:
            template = path_template(request)
            REQUESTS.labels(method=method, path_template=template).inc()
            RESPONSES.labels(method=method, path_template=template, status_code=status_code).inc()
            REQUESTS_IN_PROGRESS.labels(method=method).dec()


def metrics(request: Request) -> Response:
    """Prometheus 노출 엔드포인트 (starlette_prometheus.view.metrics 와 같은 동작)."""
    if "prometheus_multiproc_dir" in os.environ:
        registry = CollectorRegistry()
        MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
    else:
        registry = REGISTRY
    return Response(generate_latest(registry), headers={"Content-Type": CONTENT_TYPE_LATEST})
