"""구조화 로깅 (structlog).

local: 사람 읽기 쉬운 컬러 출력
prod/dev: JSON (Loki/ELK 호환)

면접 자산:
"로그를 JSON으로 통일해 Grafana Loki 같은 로그 집계 시스템에
바로 연동 가능한 구조로 만들었습니다."
"""

import logging
import sys

import structlog

from app.core.config import get_settings


def setup_logging() -> None:
    """애플리케이션 시작 시 한 번 호출."""
    settings = get_settings()

    # 표준 로깅의 기본 레벨/핸들러 설정
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.log_level,
    )

    # structlog 프로세서 체인
    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    # 환경별 출력 포맷
    if settings.is_local:
        # 로컬: 컬러 + 사람 읽기 좋은 포맷
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    else:
        # dev/prod: JSON
        processors.extend([
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ])

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """로거 인스턴스 반환.

    사용:
        from app.core.logging import get_logger
        logger = get_logger(__name__)
        logger.info("user_login", user_id=123, ip="192.168.1.1")
    """
    return structlog.get_logger(name)
