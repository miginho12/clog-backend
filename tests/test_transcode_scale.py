"""영상 압축·썸네일 크기 — 긴 변 상한만 적용하고 원본보다 키우지 않는다.

이전 필터(scale=w=MAX:h=MAX:force_original_aspect_ratio=decrease)는 MAX×MAX 상자에
'맞춰' 늘리기도 해서, 작은 원본(864x1072)이 1032x1280 으로 커졌다(ffmpeg 5.1·7.1 동일).
실제 ffmpeg 로 합성 영상을 만들어 결과 해상도를 확인한다(ffmpeg 없으면 skip).
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.domain.media.transcode_service import TranscodeService, extract_video_thumbnail

# 로컬에 ffmpeg 가 없으면 skip, CI(GitHub Actions 가 CI=true 설정)는 설치 단계가 있으니 skip 하지 않고 실패시킨다
pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None and os.environ.get("CI") != "true", reason="ffmpeg 없음"
)


def _make_src(path: Path, w: int, h: int) -> None:
    # ffv1: 홀수 해상도도 그대로 담을 수 있는 무손실 코덱 (H.264 는 짝수만)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=10:duration=0.5",
         "-c:v", "ffv1", str(path)],
        check=True,
    )


def _dims(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"])


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
