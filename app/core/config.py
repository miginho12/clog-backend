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

    # ── CORS (Day 14 ⭐ 강화) ──
    # 환경별 허용 origin
    # local:  로컬 프론트엔드 개발용
    # dev:    K8s + Tailscale Funnel 환경
    # prod:   향후 운영 도메인
    cors_origins: list[str] = Field(
        default=[
            "http://localhost:3000",         # Next.js 로컬 개발
            "https://clog.tail099985.ts.net",  # Tailscale Funnel
        ]
    )

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

    # ── 트랜스코딩 워커 (ARQ) ──
    # 작업 큐 전용 Redis DB (기존 캐시/세션과 분리)
    arq_redis_db: int = Field(default=1)
    # ffmpeg 트랜스코딩 프리셋 (벤치마크 근거: 720p CRF23)
    # 긴 변을 이 값 이하로 (비율 유지). 세로 영상도 안 짤림
    transcode_max_dimension: int = Field(default=1280)
    transcode_crf: int = Field(default=23)
    transcode_preset: str = Field(default="veryfast")
    transcode_audio_bitrate: str = Field(default="128k")
    # 워커 동시 처리 수 (라파이 부담 제한)
    transcode_max_jobs: int = Field(default=1)
    # 트랜스코딩 타임아웃 (초)
    transcode_timeout: int = Field(default=600)

    @computed_field  # type: ignore[misc]
    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @computed_field  # type: ignore[misc]
    @property
    def arq_redis_url(self) -> str:
        """ARQ 작업 큐용 Redis URL (전용 DB)."""
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.arq_redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.arq_redis_db}"

    @computed_field  # type: ignore[misc]
    @property
    def redis_url_safe(self) -> str:
        if self.redis_password:
            return f"redis://:***@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # ── Kakao OAuth ──
    kakao_client_id: str = Field(default="")
    kakao_client_secret: str = Field(default="")
    kakao_redirect_uri: str = Field(
        default="http://localhost:8000/auth/kakao/callback"
    )
    # 카카오 콜백 처리 후 사용자를 돌려보낼 프론트엔드 주소
    # 백엔드가 토큰을 fragment로 붙여 {frontend_url}/auth/callback 로 302
    frontend_url: str = Field(default="http://localhost:5173")

    kakao_authorize_url: str = "https://kauth.kakao.com/oauth/authorize"
    kakao_token_url: str = "https://kauth.kakao.com/oauth/token"
    kakao_user_info_url: str = "https://kapi.kakao.com/v2/user/me"

    # ── MinIO (오브젝트 스토리지 / 미디어 업로드) ──
    # 내부: 백엔드가 presigned 발급/관리에 쓰는 클러스터 내부 엔드포인트
    minio_endpoint: str = Field(default="minio.dev.svc.cluster.local:9000")
    # 외부: presigned URL 호스트 (브라우저가 닿는 주소). 비면 endpoint 로 폴백
    minio_public_endpoint: str = Field(default="")
    minio_access_key: str = Field(default="")
    minio_secret_key: str = Field(default="")
    minio_bucket: str = Field(default="clog-media")
    # 내부 통신은 http (TLS 종단은 외부 노출 계층에서)
    minio_secure: bool = Field(default=False)
    # presigned 유효시간(초) + 업로드 제한
    minio_presign_expiry: int = Field(default=300)  # 5분
    media_max_image_bytes: int = Field(default=10 * 1024 * 1024)   # 10MB
    media_max_video_bytes: int = Field(default=100 * 1024 * 1024)  # 100MB (1분 영상 여유)

    oauth_state_ttl_seconds: int = Field(default=300)

    # ── 메타 ──
    @property
    def is_production(self) -> bool:
        return self.environment == "prod"

    @property
    def is_local(self) -> bool:
        return self.environment == "local"

    @property
    def is_simulation_login_enabled(self) -> bool:
        """⭐ Day 14: /auth/login 시뮬레이션 엔드포인트 활성화 여부.

        local 환경에서만 활성화 (개발 편의).
        dev/prod 에선 비활성 (보안).
        """
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
