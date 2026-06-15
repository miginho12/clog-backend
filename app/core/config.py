"""애플리케이션 설정."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── 기본 정보 ──
    app_name: str = "clog-backend"
    app_version: str = "0.1.0"
    environment: Literal["dev", "prod", "local"] = Field(default="local")

    # ── 서버 ──
    host: str = "0.0.0.0"
    port: int = 8000

    # ── 로깅 ──
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ── CORS ──
    cors_origins: list[str] = Field(default=["http://localhost:3000"])

    # ── 데이터베이스 ──
    db_host: str = Field(default="localhost")
    db_port: int = Field(default=5432)
    db_name: str = Field(default="clog_dev")
    db_user: str = Field(default="clog")
    db_password: str = Field(default="")

    db_pool_size: int = Field(default=5)
    db_pool_max_overflow: int = Field(default=5)
    db_pool_timeout: int = Field(default=30)
    db_echo: bool = Field(default=False)

    @computed_field  # type: ignore[misc]
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://"
            f"{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}"
            f"/{self.db_name}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def database_url_safe(self) -> str:
        masked = "***" if self.db_password else "(empty)"
        return (
            f"postgresql+asyncpg://"
            f"{self.db_user}:{masked}"
            f"@{self.db_host}:{self.db_port}"
            f"/{self.db_name}"
        )

    # ── JWT ──
    jwt_private_key: str = Field(default="")
    jwt_private_key_path: str = Field(default="")
    jwt_public_key: str = Field(default="")
    jwt_public_key_path: str = Field(default="")

    jwt_access_token_expire_minutes: int = Field(default=60)
    jwt_refresh_token_expire_days: int = Field(default=7)

    jwt_issuer: str = Field(default="clog-backend")
    jwt_algorithm: Literal["RS256", "HS256"] = Field(default="RS256")

    # ── Redis ──
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379)
    redis_password: str = Field(default="")
    redis_db: int = Field(default=0)
    redis_max_connections: int = Field(default=20)
    redis_socket_timeout: int = Field(default=5)
    redis_connect_timeout: int = Field(default=5)

    @computed_field  # type: ignore[misc]
    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @computed_field  # type: ignore[misc]
    @property
    def redis_url_safe(self) -> str:
        if self.redis_password:
            return f"redis://:***@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # ── Kakao OAuth (⭐ Day 12 추가) ──
    kakao_client_id: str = Field(default="", description="카카오 REST API 키")
    kakao_client_secret: str = Field(
        default="", description="카카오 Client Secret (선택, 등록 시 사용)"
    )
    kakao_redirect_uri: str = Field(
        default="http://localhost:8000/auth/kakao/callback",
        description="카카오에 등록된 Redirect URI",
    )

    # 카카오 API 엔드포인트 (변경 가능성 매우 낮음)
    kakao_authorize_url: str = "https://kauth.kakao.com/oauth/authorize"
    kakao_token_url: str = "https://kauth.kakao.com/oauth/token"
    kakao_user_info_url: str = "https://kapi.kakao.com/v2/user/me"

    # OAuth state 의 Redis TTL (CSRF 방어)
    oauth_state_ttl_seconds: int = Field(default=300, description="state 유효 시간 (초)")

    # ── 메타 ──
    @property
    def is_production(self) -> bool:
        return self.environment == "prod"

    @property
    def is_local(self) -> bool:
        return self.environment == "local"

    def get_jwt_private_key(self) -> str:
        if self.jwt_private_key:
            return self.jwt_private_key.replace("\\n", "\n")
        if self.jwt_private_key_path:
            return Path(self.jwt_private_key_path).read_text()
        raise ValueError(
            "JWT private key not configured. "
            "Set JWT_PRIVATE_KEY (content) or JWT_PRIVATE_KEY_PATH (file)."
        )

    def get_jwt_public_key(self) -> str:
        if self.jwt_public_key:
            return self.jwt_public_key.replace("\\n", "\n")
        if self.jwt_public_key_path:
            return Path(self.jwt_public_key_path).read_text()
        raise ValueError(
            "JWT public key not configured. "
            "Set JWT_PUBLIC_KEY (content) or JWT_PUBLIC_KEY_PATH (file)."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
