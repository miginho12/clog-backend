"""FastAPI 애플리케이션 엔트리포인트."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.exception_handlers import register_exception_handlers
from app.api.routes import auth, health, users
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.infra.db import close_engine, init_engine
from app.infra.redis import close_redis, init_redis

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    settings = get_settings()

    logger.info(
        "app_starting",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        jwt_algorithm=settings.jwt_algorithm,
    )

    # JWT 키 검증
    try:
        settings.get_jwt_private_key()
        settings.get_jwt_public_key()
        logger.info("jwt_keys_loaded")
    except (ValueError, FileNotFoundError) as e:
        logger.error("jwt_keys_failed", error=str(e))
        raise

    # Redis 초기화
    init_redis()

    # DB 초기화
    init_engine()

    # HTTP 클라이언트 초기화 (⭐ Day 12 - 카카오 API 호출용)
    # 앱 라이프사이클 동안 단일 클라이언트 재사용 (연결 풀)
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    app.state.http_client = http_client
    logger.info("http_client_initialized")

    yield

    # ── Shutdown ──
    logger.info("app_shutting_down")
    await http_client.aclose()
    await close_engine()
    await close_redis()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Clog API",
        description="클라이밍 기록 + 커뮤니티 서비스",
        version=settings.app_version,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(users.router)
    app.include_router(auth.router)

    register_exception_handlers(app)

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
