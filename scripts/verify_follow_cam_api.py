"""들찍(팔로우캠) 업로드 API 검증 (rollback — 실제 변경 없음).

Step ①(climbing_logs.video_mode / follow_target + 작성 스키마 검증)을 검증한다.

★ 선행: video_mode/follow_target 컬럼 마이그레이션이 적용돼 있어야 한다.
    uv run alembic upgrade head
실행:
    uv run python -m scripts.verify_follow_cam_api

media_url 은 일부러 우리 버킷 형식이 아닌 URL 을 쓴다 → extract_object_key 가 None 이라
용량/길이 사후 검증(MinIO stat, ffprobe)이 건너뛰어져 외부 의존 없이 돈다.
savepoint 패턴으로 서비스 내부 commit 을 savepoint 로 가두고, 마지막에
바깥 트랜잭션을 rollback 하므로 DB 에 아무것도 남지 않는다.
"""

import asyncio
import sys
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401 — 전체 모델 로드(FK 참조 등록)
from app.core.config import get_settings
from app.domain.climbing.models import ClimbingLog
from app.domain.climbing.repository import ClimbingRepository
from app.domain.climbing.schemas import ClimbingLogCreate
from app.domain.climbing.service import ClimbingService
from app.domain.comment_likes.repository import CommentLikeRepository
from app.domain.comments.repository import CommentRepository
from app.domain.likes.repository import LikeRepository
from app.domain.media.service import MediaService
from app.domain.users.models import User
from app.infra.db.engine import close_engine, init_engine

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

# 우리 버킷 형식이 아닌 URL → 용량/길이 사후 검증 건너뜀 (위 docstring 참고)
VIDEO_URL = "https://example.test/not-our-bucket/climb.mp4"
IMAGE_URL = "https://example.test/not-our-bucket/climb.jpg"

_results: list[bool] = []


def check(name: str, ok: bool) -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}")


def base(**extra: Any) -> dict[str, Any]:
    return {"grade_raw": "V4", **extra}


def rejects(name: str, payload: dict[str, Any]) -> None:
    try:
        ClimbingLogCreate(**payload)
        check(name, False)
    except ValidationError:
        check(name, True)


async def run(session: AsyncSession) -> None:
    settings = get_settings()
    service = ClimbingService(
        session,
        ClimbingRepository(session),
        LikeRepository(session),
        CommentRepository(session),
        CommentLikeRepository(session),
        media_service=MediaService(settings),
        settings=settings,
    )
    uid = uuid.uuid4().hex[:8]
    user = User(
        email=f"follow_{uid}@clog.test",
        nickname=f"follow_{uid}",
        auth_provider="local",
        email_verified=True,
    )
    session.add(user)
    await session.flush()

    print("\n[작성 스키마 검증]")
    p = ClimbingLogCreate(**base())
    check("필드 미지정 → video_mode=normal, follow_target=None",
          p.video_mode == "normal" and p.follow_target is None)

    p = ClimbingLogCreate(**base(media_type="video", media_url=VIDEO_URL, video_mode="follow",
                                 follow_target={"t": 3.2, "x": 0.85, "y": 0.45}))
    check("들찍 + 영상 + 탭 위치 → 허용",
          p.video_mode == "follow" and p.follow_target is not None and p.follow_target.x == 0.85)

    p = ClimbingLogCreate(**base(media_type="video", media_url=VIDEO_URL, video_mode="follow"))
    check("들찍 + 영상 + 탭 없음(자동 선정) → 허용", p.follow_target is None)

    rejects("들찍 + 이미지 → 거부",
            base(media_type="image", media_url=IMAGE_URL, video_mode="follow"))
    rejects("들찍 + 미디어 없음 → 거부", base(video_mode="follow"))
    rejects("normal 모드에 follow_target → 거부",
            base(media_type="video", media_url=VIDEO_URL,
                 follow_target={"t": 1, "x": 0.5, "y": 0.5}))
    rejects("좌표 범위 밖(x=1.2) → 거부",
            base(media_type="video", media_url=VIDEO_URL, video_mode="follow",
                 follow_target={"t": 1, "x": 1.2, "y": 0.5}))
    rejects("음수 시각(t=-1) → 거부",
            base(media_type="video", media_url=VIDEO_URL, video_mode="follow",
                 follow_target={"t": -1, "x": 0.5, "y": 0.5}))

    print("\n[서비스 → DB 저장 (라우트와 동일하게 model_dump 전달)]")
    payload = ClimbingLogCreate(**base(media_type="video", media_url=VIDEO_URL, video_mode="follow",
                                       follow_target={"t": 3.2, "x": 0.85, "y": 0.45}))
    log = await service.create_log(user_id=user.id, data=payload.model_dump())
    row = (await session.execute(select(ClimbingLog).where(ClimbingLog.id == log.id))).scalar_one()
    await session.refresh(row)  # DB 왕복 값으로 확인 (JSONB 직렬화 포함)
    check("들찍 저장 → video_mode=follow, follow_target JSONB 왕복 일치",
          row.video_mode == "follow" and row.follow_target == {"t": 3.2, "x": 0.85, "y": 0.45})
    check("들찍 영상도 기존 파이프라인 진입 → media_status=processing, 원본 보관",
          row.media_status == "processing" and row.original_media_url == VIDEO_URL
          and row.media_url is None)

    payload = ClimbingLogCreate(**base(media_type="image", media_url=IMAGE_URL))
    log = await service.create_log(user_id=user.id, data=payload.model_dump())
    await session.refresh(log)
    check("일반 이미지 게시물 → video_mode=normal, follow_target=None (기존 동작 유지)",
          log.video_mode == "normal" and log.follow_target is None)


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
