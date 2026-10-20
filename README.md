# 🧗 Clog Backend

> 클라이밍 기록 서비스 **Clog(Climb + Log)** 의 백엔드 API.
> 암장마다 제각각인 색깔 난이도를 암장별 보정치로 환산해 하나의 점수로 비교하고, 영상으로 공략(베타)을 공유합니다.

**서비스** [climb-log.com](https://climb-log.com) · **Frontend** [clog-frontend](https://github.com/miginho12/clog-frontend) · **Infra** [gitops](https://github.com/miginho12/gitops)

기획부터 설계·개발·배포·운영까지 1인으로 진행했고, 라즈베리파이 5 위의 k3s 클러스터에서 실제 사용자가 쓰는 prod 환경을 직접 운영하고 있습니다. 설계 결정은 70건 이상의 ADR로 기록했습니다.

---

## 🛠 기술 스택

| 영역 | 기술 |
|---|---|
| API | Python 3.12, FastAPI, Pydantic v2 |
| DB | PostgreSQL 16, SQLAlchemy 2.0 (async), Alembic |
| 캐시·큐 | Redis 7 — ARQ 작업 큐, Refresh 토큰 저장, rate limit |
| 스토리지 | MinIO (S3 호환, presigned URL 직접 업로드) |
| 워커 | ARQ + ffmpeg (영상 트랜스코딩·썸네일), OpenCV (들찍 렌더링) |
| 인증 | JWT RS256 (Access + Refresh), 카카오 OAuth 2.0, 이메일 인증 |
| 품질 | pytest, ruff, mypy strict, pip-audit |
| 관측성 | structlog JSON 로그 → Loki, `/metrics` → Prometheus |
| 배포 | Docker 멀티스테이지 (amd64·arm64), GitHub Actions → GitOps → ArgoCD (k3s) |

---

## 🏗 아키텍처

```
사용자 ─ Cloudflare Tunnel ─ nginx(프론트) ─┬─ /api ─────▶ FastAPI ──┬─ PostgreSQL
                                            │                       ├─ Redis ── ARQ 워커 (ffmpeg·들찍)
                                            └─ /clog-media ▶ MinIO ◀─┴──────────┘
                                                              ▲
                     Kubernetes CronJob: 고아 파일 정리(매일) · 멈춘 작업 정리(15분)
```

- 영상은 API를 거치지 않고 presigned URL로 MinIO에 **직접** 올라가고, API는 경로와 처리 상태만 DB에 저장합니다.
- 트랜스코딩은 별도 워커 Pod가 Redis 큐에서 꺼내 처리하고, 상태를 `processing → done / failed`로 바꿉니다.
- API와 워커는 **같은 이미지**를 쓰고 실행 명령만 다릅니다 (`uvicorn` / `arq`).

---

## ✨ 주요 기능

- **등반 기록**: CRUD, 피드(팔로우·공개 범위 반영), 암장·태그·작성자 필터
- **그레이드 산정**: V스케일·색깔 난이도를 별도 트랙으로 점수화. 하루 점수(그날 완등 상위 3개 평균, 1트 보너스) 중 최근 운동일의 상위 평균으로 현재 실력을 표시하고, 암장별 보정치로 다른 암장 기록도 하나의 점수로 비교
- **미디어**: presigned 업로드(영상은 분할 업로드), 트랜스코딩·썸네일, 60초 길이 제한, **들찍**(삼각대 영상을 클라이머를 따라가며 찍은 것처럼 크롭 렌더링)
- **소셜**: 승인제 팔로우(비공개 계정), 좋아요, 대댓글·고정 댓글, 알림
- **인증**: 카카오 OAuth + 자체 회원가입, 이메일 인증, 비밀번호 재설정, 회원 탈퇴
- **관리자**: 콘텐츠 삭제, 암장 등급 체계·보정치 관리(변경 이력 포함), 사용자 차단

---

## 🔧 운영하며 해결한 문제

### 1. 처리 중 상태로 멈춘 게시물 — 워커 OOM
사용자 영상이 피드에 나타나지 않았습니다. Pod 재시작 이력 → 호스트 커널 로그(`dmesg`) → Redis 작업 큐 순으로 추적해, ffmpeg가 메모리 한도를 넘어 **워커 프로세스째 OOM으로 종료**되는 바람에 실패 처리 코드조차 실행되지 못했다는 것을 확인했습니다.

- 워커 메모리 한도 상향(GitOps) + 작업 재처리로 복구
- 예외 처리로는 막을 수 없는 실패에 대비해 **멈춘 작업 정리 배치** 추가 — [`app/scripts/media_stale_reconcile.py`](app/scripts/media_stale_reconcile.py)
  - 15분마다 `processing` 상태로 60분 넘게 멈춘 건을 `failed`로 바꾸고 사용자에게 알림
  - 60분 = 작업 제한 시간(약 17분) × 최대 시도(2회)에 여유를 둔 값
  - dry-run으로 28일간 오탐 0건을 확인한 뒤 적용
- 프론트에만 있던 60초 제한을 백엔드에서도 ffprobe로 검증해, API 직접 호출로 긴 영상이 들어오던 구멍을 차단

### 2. DB와 스토리지의 불일치 — 야간 대조 배치
DB와 객체 스토리지는 하나의 트랜잭션으로 묶을 수 없어, 삭제·교체된 영상 파일이 스토리지에 남았습니다. 게시물은 soft delete라 복구 가능하므로 즉시 삭제 대신 **유예 + 주기적 대조**를 택했습니다 — [`app/scripts/media_reconcile.py`](app/scripts/media_reconcile.py)

- 보존 대상 = 활성 게시물의 파일 ∪ 삭제 후 2일 이내 게시물의 파일 ∪ 처리 미완료 영상의 원본
- 만든 지 24시간이 안 된 파일은 건너뜀 (업로드 직후·처리 중 파일 보호)
- 기본은 dry-run, `--apply`일 때만 삭제. 파일별 삭제가 실패해도 배치는 계속 진행

### 3. 와이파이에서만 끊기던 영상 업로드 — 분할 업로드
폰 + 와이파이에서 대용량 영상이 시작 후 35~40초 만에 끊겼습니다. 로그를 시각으로 맞추고 경로별로 재현해 10개 가설을 지운 결과 서버가 아닌 사용자 네트워크 구간의 문제였고, 영상은 크기와 상관없이 **16MiB 조각 분할 업로드**(조각별 재시도·이어 올리기)로 전환했습니다. 서버는 조각 개수·크기를 검증한 뒤에만 합쳐, 깨진 영상이 게시되지 않게 합니다.

### 4. 피드 조회 — 실행계획 기반 인덱스
SQLAlchemy가 실제로 보내는 피드 SQL을 그대로 추출해 `EXPLAIN ANALYZE`로 분석했습니다. 피드를 열 때마다 전체를 읽고 정렬한 뒤 21건만 반환하고 있어, 정렬 순서에 맞춘 **부분 복합 인덱스**를 추가했습니다.

```sql
CREATE INDEX ix_climbing_logs_feed_order
  ON climbing_logs (climbed_at DESC, created_at DESC)
  WHERE deleted_at IS NULL;
```

합성 데이터 10만 건 기준 기본 피드 **46.8ms → 0.34ms** (15회 측정 중앙값). 아주 드문 태그 필터만 약 4ms 느려지는 트레이드오프는 ADR에 남겼습니다.

### 5. 회원 탈퇴 — 개인정보는 지우고 관계는 지키기
사용자 행은 soft delete로 남겨 기록·댓글의 참조 무결성을 유지하고, 이메일·닉네임은 복원 불가능한 값으로 익명화합니다. 탈퇴와 동시에 Refresh 토큰을 전부 무효화하고, 팔로우 관계는 양방향 삭제합니다.

---

## ✅ 검증과 CI

기능을 구현할 때마다 `scripts/verify_<기능>.py` 검증 스크립트를 만들어 항목별 PASS/FAIL을 확인합니다. 스크립트는 savepoint 안에서 실행한 뒤 롤백해 **DB에 아무것도 남기지 않습니다**.

- 검증 스크립트 39개 중 36개를 `tests/test_verify_scripts.py`가 자동 수집해 pytest로 실행 → 새 스크립트는 만들기만 하면 CI에 편입
- 실제 스토리지·샘플 영상이 필요한 3개는 로컬에서 실행 (`VERIFY_LOCAL_ONLY=1`)
- mypy strict 오류 128개 → 0개 (로직 변경 없이 기존 검증 전부 통과)
- pip-audit로 의존성 취약점 8건 중 7건 패치, 나머지 1건은 미사용 확인 후 예외 처리

**CI 파이프라인** (`.github/workflows/docker-build.yml`)

```
ruff → mypy → pip-audit → (임시 JWT 키·Postgres·Redis) alembic upgrade → pytest
  → Docker buildx (linux/amd64 + arm64) → ghcr.io, sha-<커밋> 태그
  → gitops 레포 values-dev.yaml 태그 자동 갱신 → ArgoCD dev 자동 동기화
  → dev 검증 후 같은 태그를 prod로 승격 (수동 동기화)
```

실패하면 Discord로 알림을 보냅니다. 마이그레이션은 배포와 분리해 검증 후 별도로 적용합니다.

---

## 📁 디렉토리 구조

```
clog-backend/
├── app/
│   ├── main.py            # FastAPI 앱, 미들웨어, /metrics
│   ├── worker.py          # ARQ 워커 (영상 트랜스코딩)
│   ├── api/
│   │   ├── routes/        # HTTP 경계만 담당 (도메인 예외는 던지고 끝)
│   │   └── exception_handlers.py   # 도메인 예외 → HTTP 응답 변환
│   ├── domain/            # 도메인별 models · repository · service · schemas · exceptions
│   │   ├── auth/  users/  follows/  climbing/  grade/
│   │   └── media/  comments/  likes/  notifications/
│   ├── core/              # 설정, JWT, 비밀번호, 로깅, rate limit
│   ├── infra/             # DB 엔진, Redis, 카카오 클라이언트
│   └── scripts/           # CronJob 배치 (고아 파일 정리, 멈춘 작업 정리 등)
├── alembic/versions/      # 마이그레이션
├── scripts/verify_*.py    # 기능별 검증 스크립트
├── tests/                 # pytest (검증 스크립트 래퍼 포함)
├── Dockerfile             # 멀티스테이지, 비루트 실행, HEALTHCHECK
└── docker-compose.yml     # 로컬 전용 Postgres + Redis
```

**레이어 원칙**: Repository는 DB 접근만(flush까지), Service가 도메인 규칙과 트랜잭션 경계(commit)를 책임지고, Route는 HTTP 변환만 합니다.

---

## 🚀 로컬 실행

사전 요구사항: [uv](https://docs.astral.sh/uv/), Docker

```bash
uv sync                            # 의존성 설치 (Python 자동 설치)
cp .env.example .env               # 환경변수 (DB·Redis·카카오 등)
docker-compose up -d               # 로컬 전용 Postgres 16 + Redis 7

# JWT 서명용 RSA 키 생성 (.env.example 기본 경로: ./jwt-keys/*.pem, gitignore 대상)
mkdir -p jwt-keys
openssl genrsa -out jwt-keys/private.pem 2048
openssl rsa -in jwt-keys/private.pem -pubout -out jwt-keys/public.pem

uv run alembic upgrade head        # 스키마 적용
uv run uvicorn app.main:app --reload --port 8080
# → http://localhost:8080/docs (Swagger UI)
```

```bash
uv run pytest -v                   # 단위 테스트 + 검증 스크립트
uv run ruff check .                # 린트
uv run mypy app                    # 타입 체크 (strict)
```

---

## 🩺 운영 엔드포인트

| 엔드포인트 | 용도 |
|---|---|
| `GET /health/live` | Liveness probe — 프로세스 생존 |
| `GET /health/ready` | Readiness probe — DB·Redis 연결 확인 후 트래픽 수신 |
| `GET /metrics` | Prometheus 스크레이프 |
