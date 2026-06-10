"""Redis 클라이언트 관리.

DB engine.py 와 같은 라이프사이클 패턴:
- 앱 시작 시 init_redis() → 연결 풀 생성
- 종료 시 close_redis() → 깨끗하게 정리

redis.asyncio (5.0+) 의 native async 사용.
"""

from redis.asyncio import Redis, from_url
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_redis_client: Redis | None = None


def init_redis() -> Redis:
    """Redis 클라이언트 생성 (시작 시 1회).

    실제 연결은 첫 명령 때 만들어짐 (lazy).
    """
    global _redis_client

    if _redis_client is not None:
        logger.warning("redis_already_initialized")
        return _redis_client

    settings = get_settings()

    logger.info(
        "creating_redis_client",
        url=settings.redis_url_safe,
        max_connections=settings.redis_max_connections,
    )

    _redis_client = from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,         # str 로 자동 변환 (bytes 안 다룸)
        max_connections=settings.redis_max_connections,
        socket_timeout=settings.redis_socket_timeout,
        socket_connect_timeout=settings.redis_connect_timeout,
        # 연결 재시도
        retry_on_error=[ConnectionError, TimeoutError],
        # 풀에서 꺼내기 전 헬스체크
        health_check_interval=30,
    )

    return _redis_client


async def close_redis() -> None:
    """Redis 연결 정리 (종료 시)."""
    global _redis_client

    if _redis_client is None:
        return

    logger.info("closing_redis_client")
    await _redis_client.aclose()
    _redis_client = None


def get_redis() -> Redis:
    """Redis 클라이언트 반환.

    init_redis() 호출 전이면 RuntimeError.
    """
    if _redis_client is None:
        raise RuntimeError(
            "Redis client not initialized. "
            "Make sure init_redis() is called during app startup."
        )
    return _redis_client


async def ping_redis() -> bool:
    """Redis 연결 확인 (헬스체크용)."""
    try:
        client = get_redis()
        result = await client.ping()
        return result is True
    except RedisError as e:
        logger.error("redis_ping_failed", error=str(e))
        return False
    except Exception as e:
        logger.error("redis_ping_unexpected_error", error=str(e), exc_info=True)
        return False
