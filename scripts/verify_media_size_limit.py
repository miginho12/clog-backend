"""업로드 용량 상한(media_max_image_bytes/media_max_video_bytes) 강제 적용
검증 (rollback — 실제 변경 없음).

보안 QA: presigned PUT 은 자체적으로 업로드 용량 조건을 걸 수 없어서
media_max_image_bytes/media_max_video_bytes 설정이 그동안 실제로는
강제되지 않고 있었다(설정만 있고 참조하는 코드가 없었음) — 트랜스코딩
워커가 과도하게 큰 영상을 받아 OOM 나던 사고(2026-07-20)를 악의적으로
재현할 수 있는 DoS 벡터였다.

ClimbingService.create_log 에 사후 검증(_enforce_media_size_limit)을
추가해, 업로드된 객체의 실제 용량이 상한을 넘으면 객체를 지우고 게시물
생성 자체를 거부하도록 했다. MinIO 는 실제로 붙이지 않고(외부 인프라
의존 없이 검증하기 위해) MediaService 자리에 스텁을 주입한다.

실행:
    uv run python -m scripts.verify_media_size_limit
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.core.config import Settings
from app.domain.climbing.repository import ClimbingRepository
from app.domain.climbing.service import ClimbingService
from app.domain.comment_likes.repository import CommentLikeRepository
from app.domain.comments.repository import CommentRepository
from app.domain.likes.repository import LikeRepository
from app.domain.media.service import MediaError
from app.domain.users.models import User
from app.infra.db.engine import close_engine, init_engine

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
_results: list[bool] = []


def check(name: str, ok: bool) -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}")


def make_user(nick: str) -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"medialimit_{uid}@clog.test",
        nickname=f"{nick}_{uid}",
        auth_provider="local",
        email_verified=True,
        is_public=True,
    )


class FakeMediaService:
    """MinIO 없이 크기 검증 로직만 확인하기 위한 스텁.

    object_key -> size 매핑을 미리 정해두고, extract_object_key 는
    실제 구현과 동일한 규칙("/{bucket}/" 뒤 부분)으로 흉내낸다.
    """

    def __init__(self, bucket: str, sizes: dict[str, int]):
        self.bucket = bucket
        self.sizes = sizes
        self.deleted: list[str] = []

    def extract_object_key(self, media_url: str) -> str | None:
        marker = f"/{self.bucket}/"
        idx = media_url.find(marker)
        if idx == -1:
            return None
        return media_url[idx + len(marker) :].split("?", 1)[0] or None

    def get_object_size(self, object_key: str) -> int | None:
        return self.sizes.get(object_key)

    def delete_object(self, object_key: str) -> None:
        self.deleted.append(object_key)


async def run(session: AsyncSession) -> None:
    crepo = ClimbingRepository(session)
    user = make_user("author")
    session.add(user)
    await session.flush()

    settings = Settings(
        media_max_image_bytes=10 * 1024 * 1024,  # 10MB
        media_max_video_bytes=100 * 1024 * 1024,  # 100MB
    )
    bucket = "clog-media"

    async def count_logs() -> int:
        from sqlalchemy import select

        from app.domain.climbing.models import ClimbingLog

        result = await session.execute(
            select(ClimbingLog).where(ClimbingLog.user_id == user.id)
        )
        return len(result.scalars().all())

    # ── 1) 상한 이내 이미지 — 정상 생성 ──
    small_key = f"{user.id}/{uuid.uuid4().hex}.jpg"
    media = FakeMediaService(bucket, {small_key: 5 * 1024 * 1024})
    service = ClimbingService(
        session=session,
        repository=crepo,
        like_repo=LikeRepository(session),
        comment_repo=CommentRepository(session),
        comment_like_repo=CommentLikeRepository(session),
        media_service=media,
        settings=settings,
    )
    log = await service.create_log(
        user_id=user.id,
        data={
            "grade_raw": "V4",
            "grade_system": "v_scale",
            "gym_name": "테스트짐",
            "categories": [],
            "attempts": 1,
            "is_success": True,
            "visibility": "public",
            "media_type": "image",
            "media_url": f"https://minio.internal/{bucket}/{small_key}",
        },
    )
    check("상한 이내 이미지는 정상 생성됨", log.id is not None)
    check("상한 이내면 객체를 지우지 않음", media.deleted == [])

    # ── 2) 상한 초과 이미지 — 거부 + 객체 삭제 ──
    big_key = f"{user.id}/{uuid.uuid4().hex}.jpg"
    media2 = FakeMediaService(bucket, {big_key: 50 * 1024 * 1024})  # 10MB 상한 초과
    service2 = ClimbingService(
        session=session,
        repository=crepo,
        like_repo=LikeRepository(session),
        comment_repo=CommentRepository(session),
        comment_like_repo=CommentLikeRepository(session),
        media_service=media2,
        settings=settings,
    )
    before = await count_logs()
    raised = False
    try:
        await service2.create_log(
            user_id=user.id,
            data={
                "grade_raw": "V4",
                "grade_system": "v_scale",
                "gym_name": "테스트짐",
                "categories": [],
                "attempts": 1,
                "is_success": True,
                "visibility": "public",
                "media_type": "image",
                "media_url": f"https://minio.internal/{bucket}/{big_key}",
            },
        )
    except MediaError as e:
        raised = e.code == "media_too_large"
    after = await count_logs()
    check("상한 초과 이미지는 MediaError(media_too_large)로 거부됨", raised)
    check("거부 시 게시물이 DB에 남지 않음", after == before)
    check("거부 시 MinIO 객체를 삭제함", media2.deleted == [big_key])

    # ── 3) 상한 초과 영상 — 트랜스코딩 큐 진입 전에 거부 ──
    big_video_key = f"{user.id}/{uuid.uuid4().hex}.mp4"
    media3 = FakeMediaService(
        bucket, {big_video_key: 200 * 1024 * 1024}
    )  # 100MB 상한 초과
    service3 = ClimbingService(
        session=session,
        repository=crepo,
        like_repo=LikeRepository(session),
        comment_repo=CommentRepository(session),
        comment_like_repo=CommentLikeRepository(session),
        media_service=media3,
        settings=settings,
    )
    before = await count_logs()
    raised = False
    try:
        await service3.create_log(
            user_id=user.id,
            data={
                "grade_raw": "V4",
                "grade_system": "v_scale",
                "gym_name": "테스트짐",
                "categories": [],
                "attempts": 1,
                "is_success": True,
                "visibility": "public",
                "media_type": "video",
                "media_url": f"https://minio.internal/{bucket}/{big_video_key}",
            },
        )
    except MediaError as e:
        raised = e.code == "media_too_large"
    after = await count_logs()
    check("상한 초과 영상도 거부됨(트랜스코딩 큐 진입 전 차단)", raised)
    check("영상 거부 시에도 게시물이 DB에 남지 않음", after == before)

    # ── 4) 우리 버킷 URL 형식이 아니면 검사 생략(하위 호환) ──
    media4 = FakeMediaService(bucket, {})
    service4 = ClimbingService(
        session=session,
        repository=crepo,
        like_repo=LikeRepository(session),
        comment_repo=CommentRepository(session),
        comment_like_repo=CommentLikeRepository(session),
        media_service=media4,
        settings=settings,
    )
    log2 = await service4.create_log(
        user_id=user.id,
        data={
            "grade_raw": "V4",
            "grade_system": "v_scale",
            "gym_name": "테스트짐",
            "categories": [],
            "attempts": 1,
            "is_success": True,
            "visibility": "public",
            "media_type": None,
            "media_url": None,
        },
    )
    check("미디어 없는 게시물은 검사 없이 정상 생성됨", log2.id is not None)


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
