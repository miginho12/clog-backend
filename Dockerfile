# syntax=docker/dockerfile:1.7
# ─────────────────────────────────────────────────────────────
# Clog Backend — 멀티스테이지 빌드
# ─────────────────────────────────────────────────────────────
# Stage 1 (builder): uv로 의존성 설치
# Stage 2 (runtime): 최소 이미지에 결과물만 복사
#
# 면접 자산: "멀티스테이지로 이미지 크기를 ~150MB로 최소화,
# non-root user로 컨테이너 보안 강화"
# ─────────────────────────────────────────────────────────────

# ============== Stage 1: Builder ==============
FROM python:3.12-slim-bookworm AS builder

# uv 설치 (Rust 기반, 빠른 패키지 매니저)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# uv 최적화 환경변수
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# 의존성만 먼저 설치 (Docker 레이어 캐싱 최대화)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# 애플리케이션 코드 + DB 마이그레이션 파일 복사
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# 프로젝트 자체 설치 (개발 의존성 제외)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# ============== Stage 2: Runtime ==============
FROM python:3.12-slim-bookworm AS runtime

# 런타임에 필요한 최소 패키지만
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user 생성 (보안)
RUN groupadd --gid 1000 clog && \
    useradd --uid 1000 --gid clog --shell /bin/bash --create-home clog

# Builder에서 만든 venv 복사
COPY --from=builder --chown=clog:clog /opt/venv /opt/venv
COPY --from=builder --chown=clog:clog /app /app

# venv 활성화
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
USER clog

# K8s probe용 헬스체크 (선택: K8s probe가 별도로 검사함)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

EXPOSE 8000

# Production: --reload 없음, --workers는 K8s replicas로 처리
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--no-access-log"]
