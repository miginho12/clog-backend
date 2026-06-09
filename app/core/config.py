"""애플리케이션 설정.

환경변수에서 자동으로 값을 읽어 타입 검증까지 한 번에 처리.
.env 파일 또는 시스템 환경변수 모두 지원.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """애플리케이션 전역 설정.

    환경변수 우선순위:
    1. 시스템 환경변수 (K8s ConfigMap/Secret)
    2. .env 파일 (로컬 개발)
    3. 기본값 (아래 Field default)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── 기본 정보 ──
    app_name: str = "clog-backend"
    app_version: str = "0.1.0"
    environment: Literal["dev", "prod", "local"] = Field(
        default="local",
        description="배포 환경 (dev/prod/local)",
    )

    # ── 서버 ──
    host: str = "0.0.0.0"
    port: int = 8000

    # ── 로깅 ──
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ── CORS ──
    cors_origins: list[str] = Field(
        default=["http://localhost:3000"],
        description="허용할 프론트엔드 origin",
    )

    # ── 데이터베이스 ──
    # 각 필드 분리: ConfigMap (Host/Port/Name/User) + Secret (Password)
    # Pydantic이 환경변수에서 자동으로 채움
    db_host: str = Field(default="localhost", description="PostgreSQL 호스트")
    db_port: int = Field(default=5432, description="PostgreSQL 포트")
    db_name: str = Field(default="clog_dev", description="데이터베이스 이름")
    db_user: str = Field(default="clog", description="데이터베이스 사용자")
    db_password: str = Field(default="", description="데이터베이스 비밀번호 (Secret)")

    # DB Connection Pool
    db_pool_size: int = Field(default=5, description="기본 풀 크기 (열려있는 연결 수)")
    db_pool_max_overflow: int = Field(
        default=5, description="추가로 만들 수 있는 연결 수 (peak 트래픽 대응)"
    )
    db_pool_timeout: int = Field(
        default=30, description="풀에서 연결 받기 대기 시간 (초)"
    )
    db_echo: bool = Field(default=False, description="SQL 쿼리 로깅 (dev 디버깅용)")

    @computed_field  # type: ignore[misc]
    @property
    def database_url(self) -> str:
        """SQLAlchemy 가 사용할 DB URL.

        예: postgresql+asyncpg://clog:password@postgres:5432/clog_dev
        """
        return (
            f"postgresql+asyncpg://"
            f"{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}"
            f"/{self.db_name}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def database_url_safe(self) -> str:
        """비밀번호 마스킹된 URL (로그용)."""
        masked = "***" if self.db_password else "(empty)"
        return (
            f"postgresql+asyncpg://"
            f"{self.db_user}:{masked}"
            f"@{self.db_host}:{self.db_port}"
            f"/{self.db_name}"
        )

    # ── 메타 ──
    @property
    def is_production(self) -> bool:
        return self.environment == "prod"

    @property
    def is_local(self) -> bool:
        return self.environment == "local"


@lru_cache
def get_settings() -> Settings:
    """싱글톤 설정 인스턴스.

    `lru_cache`로 모듈 로드 시 한 번만 생성 → 모든 호출이 같은 인스턴스.
    """
    return Settings()
