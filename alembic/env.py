"""Alembic 환경 설정 (async 버전).

⭐ 이 파일이 Alembic 의 핵심:
1. 우리 앱의 설정 (DATABASE_URL) 읽어오기
2. Base.metadata 가져오기 (autogenerate 가 비교할 대상)
3. async engine 으로 마이그레이션 실행

일반 Alembic 의 env.py 는 동기 환경 가정 → 우리는 async 라 수정 필요.
이게 가장 함정 많은 부분.
"""

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ─── 1. 우리 앱 import 할 수 있게 path 추가 ───
# alembic 디렉토리에서 한 단계 위 (프로젝트 루트) 가 import path 에 있어야
# from app.xxx 가능
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ─── 2. 우리 앱의 설정 + 모델 import ───
from app.core.config import get_settings  # noqa: E402

# 이 import 가 핵심:
# models.py 가 모든 모델을 import 하므로 → Base.metadata 가 채워짐
from app.infra.db.models import Base  # noqa: E402

# ─── 3. Alembic 설정 ───
config = context.config

# DATABASE_URL 을 환경변수 (또는 .env) 에서 동적으로 주입
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# 로깅
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# autogenerate 가 비교할 metadata
target_metadata = Base.metadata


# ─── 4. Offline 모드 (DB 없이 SQL 만 생성) ───
def run_migrations_offline() -> None:
    """Offline 모드: 실제 DB 연결 없이 SQL 만 생성.

    실행: alembic upgrade head --sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# ─── 5. Online 모드 (실제 DB 연결, async) ───
def do_run_migrations(connection: Connection) -> None:
    """동기 함수로 마이그레이션 실행 (connection 은 이미 async 라서 OK)."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # 이 옵션들이 autogenerate 의 품질을 좌우
        compare_type=True,           # 컬럼 타입 변경 감지
        compare_server_default=True,  # default 값 변경 감지
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """async engine 으로 마이그레이션 실행."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # 마이그레이션은 풀 안 씀
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Online 모드 진입점."""
    asyncio.run(run_async_migrations())


# ─── 6. 모드 결정 ───
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
