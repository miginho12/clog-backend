"""SQLAlchemy 2.0 async engine 관리.

엔진은 *"DB 연결 풀의 관리자"*. 앱이 시작할 때 한 번 생성하고,
종료할 때 정리하는 무거운 객체.

Spring 의 EntityManagerFactory / DataSource 와 비슷한 역할.
"""


from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 모듈 레벨 전역 변수
# lifespan 에서 init_engine() 으로 채우고, close_engine() 으로 정리.
_engine: AsyncEngine | None = None


def init_engine() -> AsyncEngine:
    """애플리케이션 시작 시 호출. 엔진을 한 번만 생성.

    이미 만들어졌으면 그것을 반환.
    """
    global _engine

    if _engine is not None:
        logger.warning("engine_already_initialized")
        return _engine

    settings = get_settings()

    logger.info(
        "creating_db_engine",
        url=settings.database_url_safe,
        pool_size=settings.db_pool_size,
        pool_max_overflow=settings.db_pool_max_overflow,
    )

    _engine = create_async_engine(
        settings.database_url,
        # ── 연결 풀 설정 ──
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        # 풀 연결이 1시간 후 자동 재생성 (오래된 연결로 인한 에러 방지)
        pool_recycle=3600,
        # 풀에서 꺼내기 전 연결이 살아있는지 확인 (안전)
        pool_pre_ping=True,
        # ── 로깅 ──
        echo=settings.db_echo,
        # ── 기타 ──
        # 비동기 환경에서 만든 connection 을 다른 task 에서 못 쓰게
        future=True,
    )

    return _engine


async def close_engine() -> None:
    """애플리케이션 종료 시 호출. 모든 연결을 깨끗하게 닫음."""
    global _engine

    if _engine is None:
        return

    logger.info("closing_db_engine")
    await _engine.dispose()
    _engine = None


def get_engine() -> AsyncEngine:
    """현재 살아있는 엔진을 가져옴.

    init_engine() 호출 전이면 RuntimeError.
    보통 init_engine() 은 lifespan 에서 자동 호출됨.
    """
    if _engine is None:
        raise RuntimeError(
            "Database engine not initialized. "
            "Make sure init_engine() is called during app startup (lifespan)."
        )
    return _engine


async def ping_db() -> bool:
    """DB 연결 확인.

    /health/ready 에서 호출.
    SELECT 1 을 실행해서 DB 가 실제로 응답하는지 검증.

    Returns:
        True: DB 정상 응답
        False: 연결 실패 또는 응답 없음
    """
    from sqlalchemy import text

    engine = get_engine()

    try:
        # 풀에서 연결 빌려와서 즉시 반납
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            row = result.scalar_one_or_none()
            return row == 1
    except Exception as e:
        logger.error("db_ping_failed", error=str(e), exc_info=True)
        return False
