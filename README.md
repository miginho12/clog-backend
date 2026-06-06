# 🧗 Clog Backend

> 클라이밍 기록 + 커뮤니티 서비스의 백엔드 API.
> FastAPI + PostgreSQL + Redis, ArgoCD GitOps로 라즈베리파이 5 위에 배포.

## 🛠 기술 스택

| 영역 | 기술 |
|---|---|
| Framework | FastAPI (Python 3.12) + Pydantic v2 |
| Package manager | uv |
| Container | Docker (멀티스테이지, ARM64 멀티플랫폼) |
| Registry | GitHub Container Registry (ghcr.io) |
| CI | GitHub Actions |
| Deploy | Helm Chart + ArgoCD (별도 [gitops](https://github.com/miginho12/gitops) 레포) |
| Runtime | k3s on Raspberry Pi 5 |

## 📁 디렉토리 구조

```
clog-backend/
├── app/
│   ├── main.py              # FastAPI 엔트리포인트
│   ├── core/                # 설정, 로깅, 보안
│   ├── api/routes/          # 엔드포인트 (health, auth, ...)
│   ├── domain/              # 비즈니스 로직 (users, climbs, ...)
│   └── infra/               # 외부 연동 (DB, Redis, ...)
├── tests/
├── .github/workflows/       # CI
├── Dockerfile               # 멀티스테이지
├── pyproject.toml           # uv 메타데이터
└── uv.lock                  # 락파일 (커밋!)
```

> 도메인 분리 구조 — `core`(인프라 공통), `api`(HTTP 경계), `domain`(비즈니스), `infra`(외부 연동).

## 🚀 로컬 개발

### 사전 요구사항

```bash
# uv 설치 (macOS/Linux)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 실행

```bash
# 의존성 설치 (Python 자동 다운로드)
uv sync

# 환경변수 복사
cp .env.example .env

# 개발 서버 (auto-reload)
uv run uvicorn app.main:app --reload

# → http://localhost:8000
# → http://localhost:8000/docs (Swagger UI)
```

### 테스트

```bash
uv run pytest -v
```

### 린트 + 포맷

```bash
uv run ruff check app/      # 린트
uv run ruff format app/     # 포맷
uv run mypy app/            # 타입 체크
```

## 🐳 Docker 로컬 빌드

```bash
docker build -t clog-backend:local .
docker run -p 8000:8000 --rm clog-backend:local
```

## 📦 배포 흐름 (GitOps)

```
[로컬] git push origin main
      ↓
[GitHub Actions] Dockerfile 빌드 → ghcr.io/miginho12/clog-backend:latest push
      ↓
[gitops 레포 수동 업데이트] apps/clog/values-dev.yaml의 image.tag 변경 → push
      ↓
[ArgoCD] gitops 레포 폴링 (3분) → Helm 렌더 → 클러스터 적용
      ↓
[Discord] 배포 알림
      ↓
[Tailscale] https://100.112.91.43 또는 도메인으로 접근
```

> 다음 단계: `image-updater`를 도입해 step 3 자동화 예정.

## 🩺 헬스체크

| 엔드포인트 | 용도 |
|---|---|
| `GET /health/live` | K8s Liveness probe (재시작 트리거) |
| `GET /health/ready` | K8s Readiness probe (트래픽 라우팅) |

## 🔐 환경변수

`.env.example` 참조. 핵심:

| 변수 | 기본값 | 설명 |
|---|---|---|
| `ENVIRONMENT` | `local` | `local` / `dev` / `prod` |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | 허용 origin |

prod 환경에서는 K8s ConfigMap/Secret으로 주입.

## 🗺 로드맵

- [x] FastAPI 스캐폴딩 + 헬스체크
- [x] Dockerfile (멀티스테이지, ARM64)
- [x] GitHub Actions CI
- [ ] Helm Chart (gitops 레포)
- [ ] ArgoCD Application 등록
- [ ] PostgreSQL 연동 + Alembic 마이그레이션
- [ ] 카카오 OAuth + JWT 인증
- [ ] 등반 기록 CRUD
- [ ] 자동 그레이드 산정 API
