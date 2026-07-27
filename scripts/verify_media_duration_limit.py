"""영상 길이 상한(media_max_video_duration_seconds) 강제 적용 검증
(rollback — 실제 변경 없음).

용량 상한(media_max_video_bytes)만으론 비트레이트에 따라 저화질
장시간 영상이 한도 안에 들어올 수 있어(2026-07-27), ffprobe 로 실제
길이를 별도 검증하는 ClimbingService._enforce_video_duration_limit 을
검증한다. 클라이밍 시도는 아무리 길어도 2분 안팎이라 60초로 제한.

ffprobe 를 실제로 띄우지 않고(외부 인프라 의존 없이 검증하기 위해)
MediaService 자리에 스텁을 주입한다 — get_video_duration 이 실제로는
ffprobe 서브프로세스를 실행하지만, 여기선 미리 정해둔 값을 반환한다.

실행:
    uv run python -m scripts.verify_media_duration_limit
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
        email=f"duration_{uid}@clog.test",
        nickname=f"{nick}_{uid}",
        auth_provider="local",
        email_verified=True,
        is_public=True,
    )


class FakeMediaService:
    """MinIO/ffprobe 없이 길이 검증 로직만 확인하기 위한 스텁."""

    def __init__(self, bucket: str, sizes: dict[str, int], durations: dict[str, float | None]):
        self.bucket = bucket
        self.sizes = sizes
        self.durations = durations
        self.deleted: list[str] = []
        self.duration_calls: list[str] = []

    def extract_object_key(self, media_url: str) -> str | None:
        marker = f"/{self.bucket}/"
        idx = media_url.find(marker)
        if idx == -1:
            return None
        return media_url[idx + len(marker):].split("?", 1)[0] or None

    def get_object_size(self, object_key: str) -> int | None:
        return self.sizes.get(object_key)

    async def get_video_duration(self, object_key: str) -> float | None:
        self.duration_calls.append(object_key)
        return self.durations.get(object_key)

    def delete_object(self, object_key: str) -> None:
        self.deleted.append(object_key)


def base_data(media_type: str | None, media_url: str | None) -> dict:
    return {
        "grade_raw": "V4",
        "grade_system": "v_scale",
        "gym_name": "테스트짐",
        "categories": [],
        "attempts": 1,
        "is_success": True,
        "visibility": "public",
        "media_type": media_type,
        "media_url": media_url,
    }


async def run(session: AsyncSession) -> None:
    crepo = ClimbingRepository(session)
    user = make_user("author")
    session.add(user)
    await session.flush()

    settings = Settings(
        media_max_image_bytes=10 * 1024 * 1024,
        media_max_video_bytes=100 * 1024 * 1024,
        media_max_video_duration_seconds=60,
    )
    bucket = "clog-media"

    async def count_logs() -> int:
        from sqlalchemy import select

        from app.domain.climbing.models import ClimbingLog

        result = await session.execute(
            select(ClimbingLog).where(ClimbingLog.user_id == user.id)
        )
        return len(result.scalars().all())

    def make_service(media: FakeMediaService) -> ClimbingService:
        return ClimbingService(
            session=session,
            repository=crepo,
            like_repo=LikeRepository(session),
            comment_repo=CommentRepository(session),
            comment_like_repo=CommentLikeRepository(session),
            media_service=media,  # type: ignore[arg-type]
            settings=settings,
        )

    # ── 1) 상한 이내 영상(60초 이하) — 정상 생성 ──
    ok_key = f"{user.id}/{uuid.uuid4().hex}.mp4"
    media1 = FakeMediaService(
        bucket, {ok_key: 5 * 1024 * 1024}, {ok_key: 45.2}
    )
    log = await make_service(media1).create_log(
        user_id=user.id,
        data=base_data("video", f"https://minio.internal/{bucket}/{ok_key}"),
    )
    check("60초 이하 영상은 정상 생성됨", log.id is not None)
    check("상한 이내면 객체를 지우지 않음", media1.deleted == [])

    # ── 2) 상한 초과 영상(61초) — 거부 + 객체 삭제 ──
    long_key = f"{user.id}/{uuid.uuid4().hex}.mp4"
    media2 = FakeMediaService(
        bucket, {long_key: 5 * 1024 * 1024}, {long_key: 61.0}
    )
    before = await count_logs()
    raised = False
    try:
        await make_service(media2).create_log(
            user_id=user.id,
            data=base_data("video", f"https://minio.internal/{bucket}/{long_key}"),
        )
    except MediaError as e:
        raised = e.code == "media_too_long"
    after = await count_logs()
    check("61초 영상은 MediaError(media_too_long)로 거부됨", raised)
    check("거부 시 게시물이 DB에 남지 않음", after == before)
    check("거부 시 MinIO 객체를 삭제함", media2.deleted == [long_key])

    # ── 3) 정확히 경계값(60초) — 초과 아님, 통과 ──
    boundary_key = f"{user.id}/{uuid.uuid4().hex}.mp4"
    media3 = FakeMediaService(
        bucket, {boundary_key: 5 * 1024 * 1024}, {boundary_key: 60.0}
    )
    log3 = await make_service(media3).create_log(
        user_id=user.id,
        data=base_data("video", f"https://minio.internal/{bucket}/{boundary_key}"),
    )
    check("정확히 60.0초는 초과가 아니라 통과함", log3.id is not None)

    # ── 4) 저용량이지만 장시간(비트레이트 낮음) — 용량 상한은 통과해도 길이로 걸림 ──
    lowbitrate_key = f"{user.id}/{uuid.uuid4().hex}.mp4"
    media4 = FakeMediaService(
        bucket, {lowbitrate_key: 8 * 1024 * 1024}, {lowbitrate_key: 300.0}
    )
    raised = False
    try:
        await make_service(media4).create_log(
            user_id=user.id,
            data=base_data(
                "video", f"https://minio.internal/{bucket}/{lowbitrate_key}"
            ),
        )
    except MediaError as e:
        raised = e.code == "media_too_long"
    check(
        "용량은 작아도(8MB) 5분짜리면 길이 상한에 걸림 "
        "(용량만으론 못 잡는 케이스)",
        raised,
    )

    # ── 5) 이미지는 길이 검증 대상 아님 ──
    img_key = f"{user.id}/{uuid.uuid4().hex}.jpg"
    media5 = FakeMediaService(bucket, {img_key: 1 * 1024 * 1024}, {})
    log5 = await make_service(media5).create_log(
        user_id=user.id,
        data=base_data("image", f"https://minio.internal/{bucket}/{img_key}"),
    )
    check("이미지는 정상 생성됨", log5.id is not None)
    check("이미지는 get_video_duration 자체를 호출 안 함", media5.duration_calls == [])

    # ── 6) ffprobe 실패(None 반환) — 정상 업로드를 막지 않음(fail-open) ──
    unknown_key = f"{user.id}/{uuid.uuid4().hex}.mp4"
    media6 = FakeMediaService(
        bucket, {unknown_key: 5 * 1024 * 1024}, {unknown_key: None}
    )
    log6 = await make_service(media6).create_log(
        user_id=user.id,
        data=base_data("video", f"https://minio.internal/{bucket}/{unknown_key}"),
    )
    check("ffprobe 실패(None) 시엔 검증을 건너뛰고 정상 생성됨", log6.id is not None)


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
