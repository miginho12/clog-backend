"""FastAPI 애플리케이션 엔트리포인트."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.exception_handlers import register_exception_handlers
from app.api.routes import health, users
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.infra.db import close_engine, init_engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """앱 시작/종료 라이프사이클."""
    # ── Startup ──
    setup_logging()
    settings = get_settings()

    logger.info(
        "app_starting",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )
    init_engine()

    yield

    # ── Shutdown ──
    logger.info("app_shutting_down")
    await close_engine()


def create_app() -> FastAPI:
    """FastAPI 인스턴스 생성."""
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

    # ── 라우터 ──
    app.include_router(health.router)
    app.include_router(users.router)  # ⭐ 추가

    # ── 예외 핸들러 ──
    register_exception_handlers(app)  # ⭐ 추가

    # ── 루트 ──
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


app = create_app()
