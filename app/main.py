"""FastAPI 애플리케이션 엔트리포인트.

실행:
  uv run uvicorn app.main:app --reload

또는 Docker에서:
  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """애플리케이션 시작/종료 시 실행되는 코드.

    시작 시: DB 연결, 캐시 초기화 등
    종료 시: 연결 정리

    다음 세션에서 DB pool, Redis 연결 추가 예정.
    """
    # ── Startup ──────────────────────────────────────────
    setup_logging()
    settings = get_settings()
    logger.info(
        "app_starting",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )

    yield  # ← 애플리케이션이 요청을 받는 동안 여기서 대기

    # ── Shutdown ─────────────────────────────────────────
    logger.info("app_shutting_down")


def create_app() -> FastAPI:
    """FastAPI 인스턴스 생성 (Factory 패턴).

    테스트에서 다른 설정으로 앱을 만들기 쉬워집니다.
    """
    settings = get_settings()

    app = FastAPI(
        title="Clog API",
        description="클라이밍 기록 + 커뮤니티 서비스",
        version=settings.app_version,
        # prod에서는 Swagger UI 비활성화 (보안)
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── 미들웨어 ─────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── 라우터 등록 ──────────────────────────────────────
    app.include_router(health.router)

    # ── 루트 엔드포인트 ──────────────────────────────────
    @app.get("/", tags=["root"])
    async def root() -> dict[str, str]:
        return {
            "message": "Hello, Clog! 🧗",
            "app": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "docs": "/docs" if not settings.is_production else "disabled in prod",
        }

    return app


# Uvicorn이 import할 인스턴스
app = create_app()
