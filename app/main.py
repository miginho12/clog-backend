"""FastAPI 애플리케이션 엔트리포인트."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

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

    # JWT 키 로드 검증
    try:
        settings.get_jwt_private_key()
        settings.get_jwt_public_key()
        logger.info("jwt_keys_loaded")
    except (ValueError, FileNotFoundError) as e:
        logger.error("jwt_keys_failed", error=str(e))
        raise

    # ⭐ Redis 초기화 (Day 11B 추가)
    init_redis()

    # DB 초기화
    init_engine()

    yield

    # ── Shutdown ──
    logger.info("app_shutting_down")
    await close_engine()
    await close_redis()  # ⭐ Day 11B 추가


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
