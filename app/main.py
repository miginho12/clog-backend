"""FastAPI 애플리케이션 엔트리포인트."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.middleware import SlowAPIMiddleware
from starlette_prometheus import metrics, PrometheusMiddleware

from app.api.exception_handlers import register_exception_handlers
from app.api.routes import auth, climbing, comment_likes, comments, grade, gym_grade_systems, health, likes, media, users
from app.core.config import get_settings
from app.core.logging import setup_logging, get_logger
from app.core.rate_limit import limiter
from app.infra.db.engine import close_engine, get_engine, init_engine
from app.infra.redis import close_redis, get_redis, init_redis

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """앱 시작/종료 시 리소스 관리."""
    settings = get_settings()
    setup_logging()

    logger.info(
        "app_starting",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        simulation_login_enabled=settings.is_simulation_login_enabled,
        cors_origins=settings.cors_origins,
    )

    # 리소스 초기화
    init_engine()
    init_redis()

    # httpx (카카오 OAuth)
    httpx_client = httpx.AsyncClient(timeout=10.0)
    app.state.http_client = httpx_client

    logger.info("app_started")

    yield

    logger.info("app_shutting_down")
    await httpx_client.aclose()
    await close_redis()
    await close_engine()
    logger.info("app_shutdown_complete")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    # ── Rate Limiter (Day 14 ⭐) ──
    # slowapi 의 limiter 인스턴스를 앱 state 에 등록
    # SlowAPIMiddleware 가 자동으로 각 요청 검사
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    # ⭐ Day 15: Prometheus metrics
    app.add_middleware(PrometheusMiddleware)
    app.add_route("/metrics", metrics)

    # ── CORS (Day 14 ⭐ 강화) ──
    # 명시된 origin 만 허용 (Day 13 까지 allow_origins=["*"] 였다면 강화)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        max_age=600,  # preflight 캐시 10분
    )

    # ── 예외 핸들러 ──
    register_exception_handlers(app)

    # ── 라우터 ──
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(climbing.router)
    app.include_router(grade.router)
    app.include_router(gym_grade_systems.router)
    app.include_router(media.router)
    app.include_router(likes.router)
    app.include_router(comments.router)
    app.include_router(comment_likes.router)

    # ── 루트 ──
    @app.get("/", tags=["root"])
    async def root() -> dict[str, str]:
        return {
            "message": "Hello, Clog! 🧗",
            "app": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "docs": "/docs",
        }

    return app


app = create_app()
