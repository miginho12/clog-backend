"""MinIO orphan 미디어 청소(reconcile) 검증 (rollback — 실제 변경 없음).

collect_referenced_keys 가 media_url 뿐 아니라 thumbnail_url 도 보존
집합에 포함하는지 검증한다. 이걸 빼먹으면 영상 게시물의 정지 썸네일이
매 reconcile 실행마다 orphan 으로 오인되어 삭제된다(실제 발생한 회귀).

MinIO 접근 없이 collect_referenced_keys 만 단위 검증 — extract_object_key
는 순수 문자열 파싱이라 실제 버킷 없이도 검증 가능하다.

실행:
    uv run python -m scripts.verify_media_reconcile

savepoint 패턴으로 서비스 내부 commit 을 savepoint 로 가두고, 마지막에
바깥 트랜잭션을 rollback 하므로 DB 에 아무것도 남지 않는다.
"""

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401 — 전체 모델 로드(FK 참조 등록)
from app.core.config import get_settings
from app.domain.climbing.models import ClimbingLog
from app.domain.media.service import MediaService
from app.domain.users.models import User
from app.infra.db.engine import close_engine, init_engine
from app.scripts.media_reconcile import RETENTION_DAYS, collect_referenced_keys

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

_results: list[bool] = []


def check(name: str, ok: bool) -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}")


def media_url(bucket: str, key: str) -> str:
    return f"https://climb-log.com/{bucket}/{key}"


def make_user() -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"reconcile_{uid}@clog.test",
        nickname=f"reconcile_{uid}",
        auth_provider="local",
        email_verified=True,
    )


def make_log(
    user: User,
    bucket: str,
    *,
    media_key: str | None,
    thumb_key: str | None,
    deleted_at: datetime | None = None,
) -> ClimbingLog:
    return ClimbingLog(
        user_id=user.id,
        grade_raw="V3",
        grade_system="v_scale",
        media_type="video",
        media_status="done",
        media_url=media_url(bucket, media_key) if media_key else None,
        thumbnail_url=media_url(bucket, thumb_key) if thumb_key else None,
        deleted_at=deleted_at,
    )


async def run(session: AsyncSession) -> None:
    settings = get_settings()
    media = MediaService(settings, internal=True)
    bucket = settings.minio_bucket

    user = make_user()
    session.add(user)
    await session.flush()

    old_cutoff = datetime.now(UTC) - timedelta(days=30)  # RETENTION_DAYS(2) 훌쩍 지남

    active = make_log(user, bucket, media_key="u1/video.mp4", thumb_key="u1/thumb.jpg")
    grace_deleted = make_log(
        user,
        bucket,
        media_key="u1/video-grace.mp4",
        thumb_key="u1/thumb-grace.jpg",
        deleted_at=datetime.now(UTC),
    )
    old_deleted = make_log(
        user,
        bucket,
        media_key="u1/video-old.mp4",
        thumb_key="u1/thumb-old.jpg",
        deleted_at=old_cutoff,
    )
    session.add_all([active, grace_deleted, old_deleted])
    await session.flush()
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)

    print("\n[collect_referenced_keys]")
    referenced = await collect_referenced_keys(session, media, cutoff)

    check(
        "활성 게시물의 media_url 키가 보존 집합에 포함됨",
        "u1/video.mp4" in referenced,
    )
    check(
        "활성 게시물의 thumbnail_url 키가 보존 집합에 포함됨 (회귀 지점)",
        "u1/thumb.jpg" in referenced,
    )
    check(
        "유예기간 내 soft-delete 게시물의 thumbnail_url도 보존됨",
        "u1/thumb-grace.jpg" in referenced,
    )
    check(
        "유예기간 지난 soft-delete 게시물의 media_url은 보존 집합에서 빠짐",
        "u1/video-old.mp4" not in referenced,
    )
    check(
        "유예기간 지난 soft-delete 게시물의 thumbnail_url도 함께 빠짐",
        "u1/thumb-old.jpg" not in referenced,
    )


async def main() -> int:
    engine = init_engine()
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
            autoflush=False,
        )
        try:
            await run(session)
        finally:
            await session.close()
            await outer.rollback()  # fixture 포함 전부 되돌림 (DB 무변경)
    await close_engine()

    total = len(_results)
    passed = sum(_results)
    print(f"\n{'─' * 40}")
    if passed == total:
        print(f"{GREEN}ALL PASS{RESET}  ({passed}/{total})  — DB 변경 없음(rollback)")
        return 0
    print(f"{RED}FAILED{RESET}  ({passed}/{total})")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
