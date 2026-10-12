"""들찍 워커 분기 검증 (rollback — DB 변경 없음, MinIO 는 가짜로 대체).

Step ③(TranscodeService.transcode 의 video_mode 분기)을 검증한다.
- follow 성공: done + 결과/썸네일 업로드 + 원본 삭제 + media_ready 알림 + 출력이 크롭본
- normal 회귀: 기존 ffmpeg 압축 경로 유지 (출력 ≈ 원본 축소본)
- follow 실패(깨진 원본): failed + 원본 유지 + media_failed 알림
- follow 타임아웃: failed + 들찍/ffmpeg 프로세스가 남지 않음 (프로세스 그룹 kill)

로컬엔 MinIO 가 없어(docker-compose 는 postgres/redis 만) 다운로드/업로드/삭제만
기록하는 FakeMedia 로 대체한다. followcam 하위 프로세스와 ffmpeg 는 실제로 실행.

★ 선행: uv run alembic upgrade head / 샘플 영상(FOLLOWCAM_SAMPLES, 기본 ../poc-follow-cam/samples)
실행:
    uv run python -m scripts.verify_followcam_worker
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401 — 전체 모델 로드(FK 참조 등록)
from app.core.config import get_settings
from app.domain.climbing.models import ClimbingLog
from app.domain.climbing.repository import ClimbingRepository
from app.domain.media.transcode_service import TranscodeService
from app.domain.notifications.models import Notification
from app.domain.notifications.repository import NotificationRepository
from app.domain.notifications.service import NotificationService
from app.domain.users.models import User
from app.infra.db.engine import close_engine, init_engine

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

SAMPLES = Path(os.environ.get("FOLLOWCAM_SAMPLES", "../poc-follow-cam/samples"))
SOLO = SAMPLES / "3dac066b.mp4"  # 720x1280, 혼자 등반 38s
BUCKET = "/clog-media/"

_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f"  ({detail})" if detail else ""))


class FakeMedia:
    """MediaService 대체: object_key → 로컬 파일. 업로드/삭제는 기록만."""

    def __init__(self, store: Path, sources: dict[str, Path]):
        self.store = store
        self.sources = sources
        self.uploaded: dict[str, Path] = {}
        self.deleted: list[str] = []

    def extract_object_key(self, url: str) -> str | None:
        i = url.find(BUCKET)
        return url[i + len(BUCKET):] if i != -1 else None

    def download_to_file(self, key: str, dest: str) -> None:
        shutil.copy(self.sources[key], dest)

    def upload_file(self, key: str, src: str, content_type: str) -> str:
        kept = self.store / key.replace("/", "_")
        shutil.copy(src, kept)  # tmpdir 는 transcode 끝나면 지워지므로 보관
        self.uploaded[key] = kept
        return f"http://fake{BUCKET}{key}"

    def delete_object(self, key: str) -> None:
        self.deleted.append(key)


def frame_at(path: Path, t: float, size: tuple[int, int]) -> np.ndarray:
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, f = cap.read()
    cap.release()
    assert ok, f"cannot read frame {path} @ {t}s"
    return cv2.resize(f, size).astype(np.int16)


def diff_from_original(out: Path, t: float = 10.0) -> float:
    """출력 프레임과 원본(같은 크기로 축소) 프레임의 평균 절대 차이 (0~255)."""
    a = frame_at(out, t, (360, 640))
    b = frame_at(SOLO, t, (360, 640))
    return float(np.abs(a - b).mean())


def followcam_procs() -> list[str]:
    r = subprocess.run(["pgrep", "-af", "app.domain.media.followcam"], capture_output=True, text=True)
    return [ln for ln in r.stdout.splitlines() if "pgrep" not in ln]


async def make_log(session: AsyncSession, user: User, key: str, **extra) -> ClimbingLog:
    log = ClimbingLog(
        user_id=user.id, grade_raw="V4", media_type="video",
        original_media_url=f"http://fake{BUCKET}{key}", media_status="processing", **extra,
    )
    session.add(log)
    await session.flush()
    return log


async def notif_types(session: AsyncSession, log_id: uuid.UUID) -> list[str]:
    rows = await session.execute(select(Notification.type).where(Notification.climbing_log_id == log_id))
    return list(rows.scalars())


async def run(session: AsyncSession, tmp: Path) -> None:
    uid = uuid.uuid4().hex[:8]
    user = User(email=f"fcw_{uid}@clog.test", nickname=f"fcw_{uid}", auth_provider="local",
                email_verified=True)
    session.add(user)
    await session.flush()

    broken = tmp / "broken.mp4"
    broken.write_bytes(b"not a video")
    sources = {f"{user.id}/solo.mp4": SOLO, f"{user.id}/broken.mp4": broken}
    media = FakeMedia(tmp, sources)
    settings = get_settings()

    def service(s=settings) -> TranscodeService:
        return TranscodeService(
            s, media, ClimbingRepository(session),  # type: ignore[arg-type]
            NotificationService(session, NotificationRepository(session)),
        )

    print("\n[들찍 성공]")
    log = await make_log(session, user, f"{user.id}/solo.mp4", video_mode="follow",
                         follow_target={"t": 10.0, "x": 0.6, "y": 0.4})
    await service().transcode(log.id)
    await session.refresh(log)
    check("media_status=done, media_url=업로드된 결과", log.media_status == "done"
          and log.media_url is not None and log.media_url.startswith("http://fake"))
    check("썸네일 생성/업로드", log.thumbnail_url is not None)
    check("원본 삭제", f"{user.id}/solo.mp4" in media.deleted)
    check("media_ready 알림", await notif_types(session, log.id) == ["media_ready"])
    out = media.uploaded[log.media_url.split(BUCKET, 1)[1]]
    d_follow = diff_from_original(out)
    check("출력이 크롭(확대)본 — 원본 축소본과 크게 다름", d_follow > 20, f"diff={d_follow:.1f}")

    print("\n[일반 모드 회귀]")
    media.deleted.clear()
    log = await make_log(session, user, f"{user.id}/solo.mp4")  # video_mode 기본 normal
    await service().transcode(log.id)
    await session.refresh(log)
    check("video_mode 기본값 normal → done", log.video_mode == "normal" and log.media_status == "done")
    out = media.uploaded[log.media_url.split(BUCKET, 1)[1]]
    d_normal = diff_from_original(out)
    check("출력 ≈ 원본 축소본 (기존 ffmpeg 압축 경로)", d_normal < 10, f"diff={d_normal:.1f}")

    print("\n[들찍 실패 — 깨진 원본]")
    media.deleted.clear()
    log = await make_log(session, user, f"{user.id}/broken.mp4", video_mode="follow")
    await service().transcode(log.id)
    await session.refresh(log)
    check("media_status=failed (일반 압축으로 대체하지 않음)", log.media_status == "failed")
    check("원본 유지 (삭제 안 함)", media.deleted == [])
    check("media_failed 알림", await notif_types(session, log.id) == ["media_failed"])

    print("\n[들찍 타임아웃 — 프로세스 그룹 kill]")
    log = await make_log(session, user, f"{user.id}/solo.mp4", video_mode="follow")
    # 12s: 검출(~5s)이 끝나고 렌더링(ffmpeg 자식 실행 중) 도중에 kill 되도록
    short = settings.model_copy(update={"transcode_timeout": 12})
    await service(short).transcode(log.id)
    await session.refresh(log)
    check("렌더링 도중 타임아웃 → failed", log.media_status == "failed")
    await asyncio.sleep(1)  # 커널이 kill 된 프로세스를 정리할 시간
    left = followcam_procs()
    check("들찍 프로세스 잔존 없음", left == [], "; ".join(left)[:200])
    # transcode() 의 출력 파일명으로 찾는다 (자체 tempdir 를 써서 경로로는 못 찾음).
    # 주의: Pi 호스트에선 클러스터 컨테이너 프로세스도 보여, dev 워커가 동시에
    # 압축 중이면 오탐할 수 있다 (그 경우 재실행).
    r = subprocess.run(["pgrep", "-af", r"ffmpeg.*compressed(\.video)?\.mp4"],
                       capture_output=True, text=True)
    left = [ln for ln in r.stdout.splitlines() if "pgrep" not in ln]
    check("들찍 내부 ffmpeg 잔존 없음", left == [], "; ".join(left)[:200])


async def main() -> int:
    if not SOLO.exists():
        print(f"{RED}샘플 없음{RESET}: {SOLO} (FOLLOWCAM_SAMPLES 로 지정)")
        return 1
    engine = init_engine()
    with tempfile.TemporaryDirectory() as tmp:
        async with engine.connect() as conn:
            outer = await conn.begin()
            session = AsyncSession(
                bind=conn,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
                autoflush=False,
            )
            try:
                await run(session, Path(tmp))
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
