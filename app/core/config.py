"""애플리케이션 설정.

환경변수에서 자동으로 값을 읽어 타입 검증까지 한 번에 처리.
.env 파일 또는 시스템 환경변수 모두 지원.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """애플리케이션 전역 설정.

    환경변수 우선순위:
    1. 시스템 환경변수 (Kubernetes ConfigMap/Secret)
    2. .env 파일 (로컬 개발)
    3. 기본값 (아래 Field default)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # 정의되지 않은 환경변수는 무시
    )

    # ── 기본 정보 ─────────────────────────────────────────────
    app_name: str = "clog-backend"
    app_version: str = "0.1.0"
    environment: Literal["dev", "prod", "local"] = Field(
        default="local",
        description="배포 환경 (dev/prod/local)",
    )

    # ── 서버 ──────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    # ── 로깅 ──────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ── CORS ──────────────────────────────────────────────────
    cors_origins: list[str] = Field(
        default=["http://localhost:3000"],
        description="허용할 프론트엔드 origin (쉼표 구분)",
    )

    # ── 데이터베이스 (다음 세션에 활성화) ─────────────────────
    # database_url: str = "postgresql+asyncpg://clog:clog@localhost:5432/clog"

    # ── 메타 ──────────────────────────────────────────────────
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
