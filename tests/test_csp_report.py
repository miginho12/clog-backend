"""CSP 위반 보고 엔드포인트 테스트 (감사 M3).

신뢰할 수 없는 입력(브라우저·아무나) → 항상 204, 예외 없음, 로그에 쿼리/프래그먼트가 남지 않음.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.routes.csp import _clean_url, _normalize
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


LEGACY = {
    "csp-report": {
        "document-uri": "https://climb-log.com/auth/callback?code=SECRET#access_token=SECRET",
        "violated-directive": "img-src",
        "effective-directive": "img-src",
        "blocked-uri": "https://evil.example/x.png?token=SECRET",
        "disposition": "report",
    }
}
REPORTING_API = [
    {
        "type": "csp-violation",
        "body": {
            "documentURL": "https://climb-log.com/feed",
            "effectiveDirective": "script-src-elem",
            "blockedURL": "inline",
            "disposition": "report",
        },
    },
    {"type": "deprecation", "body": {"id": "x"}},
]


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (json.dumps(LEGACY), "application/csp-report"),
        (json.dumps(REPORTING_API), "application/reports+json"),
        ("not json", "application/csp-report"),
        (json.dumps({"csp-report": "문자열"}), "application/json"),
        (json.dumps([1, 2, {"type": "csp-violation", "body": "x"}]), "application/json"),
        ("{}", "application/json"),
        ("x" * (17 * 1024), "application/csp-report"),  # 크기 제한 초과
    ],
)
def test_always_204(client: TestClient, body: str, content_type: str) -> None:
    r = client.post("/csp-report", content=body, headers={"Content-Type": content_type})
    assert r.status_code == 204
    assert r.content == b""


def test_url_query_and_fragment_stripped() -> None:
    n = _normalize(LEGACY["csp-report"])
    assert n["document"] == "https://climb-log.com/auth/callback"
    assert n["blocked"] == "https://evil.example/x.png"
    assert "SECRET" not in json.dumps(n)
    assert n["directive"] == "img-src"


def test_reporting_api_fields_and_keywords() -> None:
    n = _normalize(REPORTING_API[0]["body"])
    assert n["directive"] == "script-src-elem"
    assert n["blocked"] == "inline"  # 키워드는 그대로
    assert _clean_url(None) is None
    assert len(_clean_url("https://a.example/" + "p" * 500) or "") == 200
