"""FastAPI 애플리케이션 엔트리포인트.

실행:
  uv run uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.infra.db import close_engine, init_engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """애플리케이션 시작/종료 시 실행되는 코드.

    시작 시: 로깅, DB 엔진 생성
    종료 시: DB 엔진 정리
    """
    # ── Startup ──
    setup_logging()
    settings = get_settings()

    logger.info(
        "app_starting",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )

    # DB 엔진 생성 (실제 연결은 첫 쿼리 때 만들어짐)
    init_engine()

    yield  # ← 앱이 요청을 받는 중

    # ── Shutdown ──
    logger.info("app_shutting_down")
    await close_engine()


def create_app() -> FastAPI:
    """FastAPI 인스턴스 생성 (Factory 패턴)."""
    settings = get_settings()

    app = FastAPI(
        title="Clog API",
        description="클라이밍 기록 + 커뮤니티 서비스",
        version=settings.app_version,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── 미들웨어 ──
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── 라우터 등록 ──
    app.include_router(health.router)

    # ── 루트 엔드포인트 ──
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


# Uvicorn 이 import 할 인스턴스
app = create_app()
