"""FastAPI 의존성 주입용 AsyncSession.

각 HTTP 요청마다 새 세션을 만들고, 응답 후 자동으로 정리.

Spring 의 @Transactional 또는 EntityManager 자동 주입과 비슷.

사용 예 (다음 세션):
    from fastapi import Depends
    from app.infra.db.session import get_session

    @router.get("/users/{user_id}")
    async def get_user(
        user_id: int,
        session: AsyncSession = Depends(get_session),
    ):
        return await session.get(User, user_id)
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.db.engine import get_engine

# 세션 팩토리 (엔진 가져와서 세션 만드는 공장)
# AsyncSession 자체보다 가볍게 매 요청마다 생성 가능
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """세션 팩토리 얻기.

    엔진이 만들어진 다음에 호출 가능.
    """
    global _sessionmaker

    if _sessionmaker is None:
        engine = get_engine()
        _sessionmaker = async_sessionmaker(
            engine,
            class_=AsyncSession,
            # 세션이 commit/rollback 후에도 객체에 접근 가능
            # (lazy loading 없이 사용할 거라 OK)
            expire_on_commit=False,
            # autoflush=False 가 더 명시적이고 안전한 패턴
            autoflush=False,
        )

    return _sessionmaker


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 의존성 주입.

    매 요청마다 새 세션 생성 + 사용 후 자동 정리.
    예외 발생 시 자동 rollback.

    사용:
        async def my_endpoint(session: AsyncSession = Depends(get_session)):
            ...
    """
    sessionmaker = get_sessionmaker()

    async with sessionmaker() as session:
        try:
            yield session
            # 명시적 commit 안 함 — 각 엔드포인트가 직접 결정
            # (다음 세션에 패턴 정리)
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
