# Alembic — DB 마이그레이션

> SQLAlchemy 2.0 async 환경에 맞춰 커스터마이징된 Alembic 설정.

## 디렉토리 구조

```
alembic/
├── README.md           # 이 파일
├── env.py              # ⭐ 핵심 - async 환경 처리
├── script.py.mako      # 마이그레이션 템플릿
└── versions/           # 마이그레이션 파일들
    └── (자동 생성됨)
```

## 사전 요구사항

`.env` 파일에 DB 정보 설정되어 있어야 함:
```
DB_HOST=localhost   # 또는 port-forward 대상
DB_PORT=5432
DB_NAME=clog_dev
DB_USER=clog
DB_PASSWORD=<진짜 비밀번호>
```

## 명령어

### 새 마이그레이션 생성 (autogenerate)

코드의 모델 변경을 감지해 SQL 자동 생성:

```bash
uv run alembic revision --autogenerate -m "add users table"
```

`alembic/versions/` 안에 `YYYY_MM_DD_HHMM-xxxxx_add_users_table.py` 생성됨.

⚠️ **자동 생성된 파일을 검토 후 적용**:
- autogenerate 가 완벽하지 않을 수 있음
- 컬럼 이름 변경 = drop + add 로 감지 (데이터 손실!)
- 검토 후 필요시 수동 수정

### 마이그레이션 실행 (DB 에 적용)

최신 버전까지:
```bash
uv run alembic upgrade head
```

한 단계만:
```bash
uv run alembic upgrade +1
```

특정 버전까지:
```bash
uv run alembic upgrade <revision_id>
```

### 롤백

한 단계 되돌리기:
```bash
uv run alembic downgrade -1
```

처음 상태로:
```bash
uv run alembic downgrade base
```

### 현재 버전 확인

```bash
uv run alembic current
```

### 마이그레이션 히스토리

```bash
uv run alembic history --verbose
```

### SQL 만 보기 (실행 X)

```bash
uv run alembic upgrade head --sql
```

## K8s 환경에서 마이그레이션

라즈베리파이의 PostgreSQL 에 적용:

### 방법 A: 로컬에서 port-forward 사용

```bash
# 터미널 1
kubectl port-forward -n dev svc/postgres 5432:5432

# 터미널 2 (DB_HOST=localhost 로 .env 설정)
uv run alembic upgrade head
```

### 방법 B: clog-dev Pod 안에서 실행

```bash
# Pod 안에서 (DB_HOST 가 이미 K8s 내부 DNS)
kubectl exec -n dev <clog-pod-name> -it -- uv run alembic upgrade head
```

> 💡 **방법 B 가 운영 표준**
>
> 같은 환경에서 Pod 가 사용할 같은 DB 에 마이그레이션. 
> 다음 세션에 init container 로 자동화 예정.

### 방법 C: Job 으로 실행 (수동)

```bash
# 일회성 Job 생성
kubectl run alembic-migrate -n dev --rm -it --restart=Never \
  --image=ghcr.io/miginho12/clog-backend:latest \
  --env-from=configmapref:clog-dev \
  -- uv run alembic upgrade head
```

## 베스트 프랙티스

1. **autogenerate 결과는 항상 검토**: 데이터 손실 가능
2. **마이그레이션 파일은 git 에 커밋**: 다른 환경에서도 같은 순서로 적용
3. **이름은 명확하게**: `add_users_table`, `add_email_to_users` 같이
4. **여러 변경은 분리**: 한 마이그레이션 = 하나의 논리적 변경
5. **운영 적용 전 dev 에서 검증**: 데이터 있는 환경에서 한 번 더 안전

## 트러블슈팅

### `Target database is not up to date`

→ 다른 환경에서 만든 마이그레이션이 있을 수 있음
```bash
uv run alembic current
uv run alembic upgrade head
```

### `Can't locate revision identified by 'xxxxx'`

→ versions/ 폴더에 마이그레이션 파일 누락
```bash
ls alembic/versions/
# 필요한 파일이 있는지 확인
```

### autogenerate 가 변경 감지 못 함

→ models.py 에 새 모델 import 안 됨
```bash
cat app/infra/db/models.py
# 새 모델이 import 되어있는지 확인
```
