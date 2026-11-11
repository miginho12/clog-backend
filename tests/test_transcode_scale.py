"""영상 압축·썸네일 크기 — 긴 변 상한만 적용하고 원본보다 키우지 않는다.

이전 필터(scale=w=MAX:h=MAX:force_original_aspect_ratio=decrease)는 MAX×MAX 상자에
'맞춰' 늘리기도 해서, 작은 원본(864x1072)이 1032x1280 으로 커졌다(ffmpeg 5.1·7.1 동일).
실제 ffmpeg 로 합성 영상을 만들어 결과 해상도를 확인한다.

ffmpeg: 시스템에 있으면 그것(Pi·운영 이미지와 같은 7.1), 없으면 dev 의존성 imageio-ffmpeg 의
정적 바이너리를 ``ffmpeg`` 이름으로 PATH 에 연결(CI — GitHub 러너엔 ffmpeg 가 없고 apt 는
ubuntu.com 미러 타임아웃으로 16분 멈춘 적 있음, 2026-11-12). 해상도는 OpenCV 로 잰다(ffprobe 불필요).
"""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import cv2
import pytest

from app.core.config import get_settings
from app.domain.media.transcode_service import TranscodeService, extract_video_thumbnail


@pytest.fixture(scope="module", autouse=True)
def ffmpeg_on_path(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    if shutil.which("ffmpeg"):
        yield
        return
    try:
        import imageio_ffmpeg
    except ImportError:  # pragma: no cover — dev 의존성 미설치
        pytest.skip("ffmpeg 없음(시스템·imageio-ffmpeg 둘 다)")
    bindir = tmp_path_factory.mktemp("ffmpeg-bin")
    (bindir / "ffmpeg").symlink_to(imageio_ffmpeg.get_ffmpeg_exe())
    old = os.environ["PATH"]
    os.environ["PATH"] = f"{bindir}{os.pathsep}{old}"
    try:
        yield
    finally:
        os.environ["PATH"] = old


def _make_src(path: Path, w: int, h: int) -> None:
    # ffv1: 홀수 해상도도 그대로 담을 수 있는 무손실 코덱 (H.264 는 짝수만)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=10:duration=0.5",
         "-c:v", "ffv1", str(path)],
        check=True,
    )


def _dims(path: Path) -> tuple[int, int]:
    if path.suffix == ".jpg":
        img = cv2.imread(str(path))
        assert img is not None, f"이미지 읽기 실패: {path}"
        return int(img.shape[1]), int(img.shape[0])
    cap = cv2.VideoCapture(str(path))
    try:
        ok, frame = cap.read()
        assert ok, f"영상 읽기 실패: {path}"
        return int(frame.shape[1]), int(frame.shape[0])
    finally:
        cap.release()


# 기본 설정: 압축 긴 변 1280, 썸네일 긴 변 480
@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ((864, 1072), (864, 1072)),  # 상한보다 작음 → 그대로 (버그: 1032x1280)
        ((640, 360), (640, 360)),  # 작은 가로 → 그대로
        ((1080, 1920), (720, 1280)),  # 세로 폰 영상 → 긴 변 1280
        ((1920, 1080), (1280, 720)),  # 가로 → 긴 변 1280
        ((3840, 2160), (1280, 720)),  # 4K
        ((1280, 720), (1280, 720)),  # 딱 상한
        ((641, 481), (640, 480)),  # 홀수 → 짝수로 내림(H.264)
    ],
)
async def test_transcode_never_upscales(tmp_path: Path, src: tuple[int, int], expected: tuple[int, int]) -> None:
    settings = get_settings()
    assert settings.transcode_max_dimension == 1280
    s = tmp_path / "src.mkv"
    d = tmp_path / "out.mp4"
    _make_src(s, *src)
    ts = TranscodeService.__new__(TranscodeService)  # _run_ffmpeg 는 settings 만 사용
    ts.settings = settings
    await ts._run_ffmpeg(str(s), str(d))
    assert _dims(d) == expected


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ((320, 240), (320, 240)),  # 상한(480)보다 작음 → 그대로 (버그: 480x360)
        ((1080, 1920), (270, 480)),
        ((1920, 1080), (480, 270)),
    ],
)
async def test_thumbnail_never_upscales(tmp_path: Path, src: tuple[int, int], expected: tuple[int, int]) -> None:
    settings = get_settings()
    assert settings.thumbnail_max_dimension == 480
    s = tmp_path / "src.mkv"
    d = tmp_path / "thumb.jpg"
    _make_src(s, *src)
    await extract_video_thumbnail(settings, str(s), str(d))
    assert _dims(d) == expected
