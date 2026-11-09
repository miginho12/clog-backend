"""CSP 위반 보고 수신 (감사 M3, 2026-11-09).

브라우저가 Content-Security-Policy(-Report-Only) 의 report-uri 로 보낸 위반 보고를
구조화 로그(csp_violation)로 남긴다 → Loki 에서 "강제 모드로 바꿔도 깨지는 게 없는지"를 본다.

- 인증 없음(브라우저가 자동으로 보냄) → rate limit + 본문 크기 제한으로 로그 폭주 방지.
  H3 이후 rate limit 키는 실제 클라이언트 IP.
- 본문은 신뢰하지 않는다: 필요한 필드만, 길이 제한, URL 은 쿼리·프래그먼트 제거(토큰·코드 유출 방지).
- 형식 두 가지 모두 받는다: 옛 report-uri({"csp-report": {...}}) / Reporting API([{type, body}]).
"""

import json
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response, status

from app.core.logging import get_logger
from app.core.rate_limit import RateLimits, limiter

logger = get_logger(__name__)

router = APIRouter(tags=["security"])

MAX_BODY_BYTES = 16 * 1024
MAX_REPORTS_PER_REQUEST = 10


def _clean_url(value: Any) -> str | None:
    """쿼리·프래그먼트를 떼고 200자로 자른다. 'inline'·'eval' 같은 키워드는 그대로."""
    if not isinstance(value, str) or not value:
        return None
    if "://" not in value:
        return value[:50]
    parts = urlsplit(value)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"[:200]


def _pick(report: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in report and report[k] not in (None, ""):
            return report[k]
    return None


def _normalize(report: dict[str, Any]) -> dict[str, Any]:
    # 옛 형식은 kebab-case(violated-directive), Reporting API 는 camelCase(effectiveDirective)
    return {
        "directive": _pick(report, "effective-directive", "effectiveDirective", "violated-directive"),
        "blocked": _clean_url(_pick(report, "blocked-uri", "blockedURL")),
        "document": _clean_url(_pick(report, "document-uri", "documentURL")),
        "source": _clean_url(_pick(report, "source-file", "sourceFile")),
        "line": _pick(report, "line-number", "lineNumber"),
        "disposition": _pick(report, "disposition"),
    }


@router.post("/csp-report", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
@limiter.limit(RateLimits.CSP_REPORT)
async def csp_report(request: Request) -> Response:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        logger.info("csp_report_too_large", size=len(body))
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    reports: list[dict[str, Any]] = []
    if isinstance(data, dict) and isinstance(data.get("csp-report"), dict):
        reports = [data["csp-report"]]
    elif isinstance(data, list):
        reports = [
            r["body"]
            for r in data[:MAX_REPORTS_PER_REQUEST]
            if isinstance(r, dict) and r.get("type") == "csp-violation" and isinstance(r.get("body"), dict)
        ]

    for r in reports:
        logger.warning("csp_violation", **_normalize(r))
    # 브라우저는 응답을 보지 않는다 — 항상 204
    return Response(status_code=status.HTTP_204_NO_CONTENT)
