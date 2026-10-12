"""들찍(팔로우캠) — 삼각대로 찍은 영상을 '들고 따라가며 찍은' 것처럼 크롭 렌더링.

흐름 (워커가 하위 프로세스로 실행: python -m app.domain.media.followcam):
1. detect_motion: 영상 전체의 픽셀별 중앙값으로 '클라이머 없는 배경'을 만들고,
   배경과 다른 영역(움직이는 사람)을 후보 박스로 뽑는다.
2. plan_camera: 후보 중 클라이머를 골라 샘플별 목표(중심, 줌)를 정하고,
   양방향 가우시안으로 스무딩해 프레임별 크롭 창을 만든다.
3. render: 원본 해상도에서 크롭 → 출력 크기로 축소 → libx264 (영상만),
   이후 원본 오디오를 copy mux.

설계 근거 (poc-follow-cam PoC, 2026-10-12 실측):
- MediaPipe Pose 는 클라이밍 자세/작은 인물에서 검출률 16~43% → 부적합.
  배경 차분은 샘플 6개 98.9~100%, 3ms/frame. 업로드 후 처리라 영상 전체를
  볼 수 있어 MOG2 대신 중앙값 배경 → 클라이머가 쉬어도 배경에 흡수되지 않음.
- 하위 프로세스로 돌리는 이유: 타임아웃 시 kill 가능(스레드는 못 멈춤),
  종료 시 opencv/프레임 메모리가 OS 에 반환, 워커 이벤트 루프를 막지 않음.
- 오디오를 인코딩과 같은 ffmpeg 의 두 번째 입력으로 열면 원본이 앞서 읽혀
  쌓여 메모리가 213MB → 610MB (60s 1080p HEVC) → 영상만 인코딩 후 별도 mux.
"""

import argparse
import json
import math
import resource
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

# ── 검출 ──
SAMPLE_FPS = 10.0  # 위치만 필요하므로 초당 10장이면 충분 (사이는 보간)
PROC_LONG = 360  # 검출용 축소 해상도(긴 변)
BG_SAMPLES = 120  # 배경 중앙값에 쓸 최대 샘플 수
DIFF_THRESH = 30  # 배경과의 채널 최대 차이 임계값 (0~255)
MIN_AREA_RATIO = 0.002  # 처리 해상도 대비 최소 blob 면적
MAX_CANDIDATES = 5  # 샘플당 보관할 후보 수 (면적 큰 순)

# ── 대상 선정 ──
APPROACH_AREA = 0.15  # 박스 면적이 프레임의 15% 초과 → 카메라로 걸어오는 사람 (와이드)
# 영상 전체에서 거의 같은 박스가 이 시간 분량 이상 등장하면 정적 잔여물.
# 실측: 잔여물 4.5~5.9s / 쉬는 클라이머 2.2s (sample.mp4) → 그 사이 3.5s
STATIC_WINDOW_S = 3.5
STATIC_IOU = 0.97
STATIC_MAX_AREA = 0.02  # 정적 판정은 작은 blob 에만 (가까이 있는 사람 오판 방지)
HOLD_S = 1.0  # 미검출 시 마지막 위치 유지 시간 → 초과 시 와이드
SEED_SEARCH_S = 1.0  # 탭 시점에 후보가 없으면 앞뒤로 찾을 범위

# ── 카메라 ──
ZOOM_MAX = 2.0
MARGIN_W, MARGIN_H = 1.6, 1.4  # 크롭 창이 박스보다 이만큼 커야 함 (팔다리 여유)
SIGMA_POS_S, SIGMA_ZOOM_S = 0.5, 1.0  # 양방향 가우시안 스무딩 강도 (초)

# ── 출력 비율 (피드 규격) ──
# 피드(프론트 AutoPlayVideo)는 영상 비율을 [4:5, 1.91:1] 로 clamp 하고 object-top 으로 잘라 보여준다.
# 원본 비율로 렌더하면 세로(9:16) 들찍 영상은 피드에서 아래 30% 가 또 잘려 클라이머 하체가 잘렸다
# (PoC 비교: 10%↑ 잘린 장면 12% → 피드 비율로 구도 0~3%). → 출력을 같은 범위로 맞춰 카메라가 그 틀로 구도.
# ⚠ 프론트 AutoPlayVideo 의 MIN_RATIO / MAX_RATIO 와 함께 바꿀 것.
FEED_MIN_RATIO, FEED_MAX_RATIO = 4 / 5, 1.91

Box = tuple[int, int, int, int]


@dataclass
class Detection:
    bbox: Box  # 원본 좌표 (x1, y1, x2, y2)
    center: tuple[float, float]


@dataclass
class MotionResult:
    width: int
    height: int
    fps: float
    n_frames: int
    sample_frames: list[int]  # 샘플의 원본 프레임 번호
    dets: list[list[Detection]]  # 샘플별 후보 (면적 큰 순)


@dataclass
class Target:
    cx: float
    cy: float
    zoom: float
    state: str  # track | hold | wide | approach


@dataclass
class CameraPath:
    x0: np.ndarray
    y0: np.ndarray
    cw: np.ndarray
    ch: np.ndarray
    states: list[str]


class FollowCamError(Exception):
    """들찍 처리 실패."""


# ─────────────────────────────────────────
#  1. 검출
# ─────────────────────────────────────────
def detect_motion(src: str, sample_fps: float = SAMPLE_FPS) -> MotionResult:
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise FollowCamError(f"cannot open video: {src}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(fps / sample_fps))

    small: list[np.ndarray] = []
    sample_frames: list[int] = []
    w = h = 0
    idx = 0
    while cap.grab():
        if idx % step == 0:
            ok, f = cap.retrieve()
            if not ok:
                break
            if not small:
                # 회전 메타데이터가 있는 휴대폰 영상은 CAP_PROP 크기가 회전 전 값일 수
                # 있어, 실제 디코딩된(자동 회전 적용된) 프레임에서 크기를 읽는다.
                h, w = f.shape[:2]
                scale = PROC_LONG / max(w, h)
                pw, ph = round(w * scale), round(h * scale)
            small.append(cv2.resize(f, (pw, ph), interpolation=cv2.INTER_AREA))
            sample_frames.append(idx)
        idx += 1
    cap.release()
    if len(small) < 2:
        raise FollowCamError("video too short or undecodable")

    sel = np.linspace(0, len(small) - 1, min(BG_SAMPLES, len(small))).astype(int)
    bg = np.median(np.stack([small[i] for i in sel]), axis=0).astype(np.uint8)

    min_area = int(pw * ph * MIN_AREA_RATIO)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    inv = 1 / scale
    dets: list[list[Detection]] = []
    for f in small:
        diff = cv2.absdiff(f, bg).max(axis=2)  # 채널 중 최대 차이 (색 변화도 잡도록)
        blurred = cv2.GaussianBlur(diff, (5, 5), 0)
        mask = (blurred > DIFF_THRESH).astype(np.uint8) * 255
        opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        fg = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel, iterations=2)
        n, _, stats, cents = cv2.connectedComponentsWithStats(fg)
        cands = []
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            if area >= min_area:
                cands.append((int(area), Detection(
                    bbox=(round(x * inv), round(y * inv), round((x + bw) * inv), round((y + bh) * inv)),
                    center=(float(cents[i][0] * inv), float(cents[i][1] * inv)),
                )))
        cands.sort(key=lambda c: -c[0])
        dets.append([d for _, d in cands[:MAX_CANDIDATES]])

    return MotionResult(w, h, fps, idx, sample_frames, dets)


# ─────────────────────────────────────────
#  2. 대상 선정 + 카메라 경로
# ─────────────────────────────────────────
def _iou(a: Box, b: Box) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def _area(b: Box, w: int, h: int) -> float:
    return (b[2] - b[0]) * (b[3] - b[1]) / (w * h)


def _static_flags(m: MotionResult, win: int) -> list[list[bool]]:
    """양방향 정적 판정: 영상 전체에서 거의 같은 박스가 win 샘플 이상 등장하는 작은 blob.

    과거만 보면 사람이 잔여물을 가린 구간 때문에 판정이 끊긴다(PoC 404b91f5).
    업로드 후 처리라 영상 전체를 볼 수 있으므로 시점 무관하게 등장 횟수로 판정한다.
    """
    flags = []
    for row in m.dets:
        out = []
        for d in row:
            if _area(d.bbox, m.width, m.height) > STATIC_MAX_AREA:
                out.append(False)
                continue
            seen = sum(1 for r in m.dets if any(_iou(d.bbox, e.bbox) >= STATIC_IOU for e in r))
            out.append(seen >= win)
        flags.append(out)
    return flags


def _plan(
    rows: list[list[Detection]],
    m: MotionResult,
    zoom_max: float,
    hold_n: int,
    prev: tuple[float, float] | None,
    base: tuple[float, float],
) -> list[Target]:
    """샘플 순서대로 클라이머를 따라가며 목표를 정한다 (역방향은 뒤집은 rows 를 넘김).

    prev: 직전 클라이머 위치. None 이면 첫 후보는 면적 최대(자동 선정).
    base: 줌 1배 크롭 창 크기 (출력 비율로 원본에 들어가는 최대 영역). 줌/여유 계산 기준.
    """
    w, h = m.width, m.height
    base_w, base_h = base
    wide = Target(w / 2, h / 2, 1.0, "wide")
    out: list[Target] = []
    last_track: Target | None = None
    miss = 0
    for cands in rows:
        if cands and prev is not None:
            anchor = prev  # 람다가 Optional 이 아닌 값을 캡처하도록 고정
            det = min(cands, key=lambda d: math.dist(d.center, anchor))
        elif cands:
            det = max(cands, key=lambda d: _area(d.bbox, w, h))
        else:
            det = None

        if det is not None and _area(det.bbox, w, h) > APPROACH_AREA:
            # 카메라로 걸어오는(지나가는) 사람 → 와이드.
            # prev 는 유지: 지나가는 사람이 따라가던 클라이머의 기억을 지우면 안 됨
            out.append(Target(w / 2, h / 2, 1.0, "approach"))
            miss = 0
            continue
        if det is None:
            miss += 1
            if last_track is not None and miss <= hold_n:
                out.append(Target(last_track.cx, last_track.cy, last_track.zoom, "hold"))
            else:
                out.append(wide)
            continue

        miss = 0
        x1, y1, x2, y2 = det.bbox
        bw, bh = max(1, x2 - x1), max(1, y2 - y1)
        # 줌 = 최대 zoom_max, 단 박스(+여유)가 크롭 창에 들어가도록 제한
        z = max(1.0, min(zoom_max, base_w / (bw * MARGIN_W), base_h / (bh * MARGIN_H)))
        prev = det.center
        last_track = Target((x1 + x2) / 2, (y1 + y2) / 2, z, "track")
        out.append(last_track)
    return out


def _find_seed(
    m: MotionResult, flags: list[list[bool]], tap: dict[str, float]
) -> tuple[int, tuple[float, float]] | None:
    """탭(t, x, y)에 해당하는 샘플과 그 시점의 클라이머 위치."""
    duration = m.n_frames / m.fps
    t = min(max(0.0, tap["t"]), duration)
    px, py = tap["x"] * m.width, tap["y"] * m.height
    times = np.array(m.sample_frames) / m.fps
    center = int(np.argmin(np.abs(times - t)))
    search = round(SEED_SEARCH_S * SAMPLE_FPS)
    # 탭 시점에 후보가 없으면(그 순간 가만히 있었음 등) 가까운 샘플부터 찾는다
    for off in sorted(range(-search, search + 1), key=abs):
        i = center + off
        if not 0 <= i < len(m.dets):
            continue
        cands = [
            d for d, s in zip(m.dets[i], flags[i], strict=True)
            if not s and _area(d.bbox, m.width, m.height) <= APPROACH_AREA
        ]
        if cands:
            det = min(cands, key=lambda d: math.dist(d.center, (px, py)))
            return i, det.center
    return None


def _smooth(x: np.ndarray, sigma: float) -> np.ndarray:
    """양방향(비인과) 가우시안 — 미래 프레임도 보므로 지연 없이 부드럽게 따라감."""
    if sigma <= 0:
        return x
    r = int(3 * sigma)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    return np.convolve(np.pad(x, r, mode="edge"), k, mode="valid")


def plan_camera(
    m: MotionResult,
    follow_target: dict[str, float] | None = None,
    zoom_max: float = ZOOM_MAX,
    aspect: float | None = None,
) -> CameraPath:
    """샘플별 목표 → 전 프레임 보간 → 스무딩 → 프레임 안으로 클램프한 크롭 창.

    follow_target 이 있으면 그 시점·위치의 사람을 시작점으로 앞/뒤 양방향 추적,
    없거나 그 근처에 후보가 없으면 처음부터 자동 선정(면적 최대로 시작).
    aspect: 크롭 창(=출력) 비율. None 이면 원본 비율 (기존 동작과 동일).
    """
    w, h = m.width, m.height
    if aspect is None:
        base = (float(w), float(h))
    else:
        base_w = min(float(w), h * aspect)
        base = (base_w, base_w / aspect)

    sample_dt = 1 / SAMPLE_FPS
    flags = _static_flags(m, round(STATIC_WINDOW_S / sample_dt))
    rows = [
        [d for d, s in zip(r, f, strict=True) if not s]
        for r, f in zip(m.dets, flags, strict=True)
    ]
    hold_n = round(HOLD_S / sample_dt)

    seed = _find_seed(m, flags, follow_target) if follow_target else None
    if seed is None:
        targets = _plan(rows, m, zoom_max, hold_n, None, base)
    else:
        i, c = seed
        fwd = _plan(rows[i:], m, zoom_max, hold_n, c, base)
        bwd = _plan(rows[: i + 1][::-1], m, zoom_max, hold_n, c, base)[::-1]
        targets = bwd[:-1] + fwd  # 시드 샘플은 fwd 쪽 사용

    t = np.arange(m.n_frames)
    cx = np.interp(t, m.sample_frames, [p.cx for p in targets])
    cy = np.interp(t, m.sample_frames, [p.cy for p in targets])
    z = np.interp(t, m.sample_frames, [p.zoom for p in targets])
    cx = _smooth(cx, SIGMA_POS_S * m.fps)
    cy = _smooth(cy, SIGMA_POS_S * m.fps)
    z = np.clip(_smooth(z, SIGMA_ZOOM_S * m.fps), 1.0, None)

    cw, ch = base[0] / z, base[1] / z
    x0 = np.clip(cx - cw / 2, 0, w - cw)
    y0 = np.clip(cy - ch / 2, 0, h - ch)
    return CameraPath(x0, y0, cw, ch, [p.state for p in targets])


# ─────────────────────────────────────────
#  3. 렌더링
# ─────────────────────────────────────────
def output_size(w: int, h: int, max_dim: int) -> tuple[int, int]:
    """출력 (가로, 세로).

    원본 비율이 피드 범위 [FEED_MIN_RATIO, FEED_MAX_RATIO] 안이면 기존처럼 원본 비율·긴 변 max_dim.
    범위 밖(세로 9:16 등)이면 피드 비율로 바꾸되 픽셀 수는 원본 비율 출력과 같게 유지한다
    (확대한 원본 영역이 이미 그보다 작아 해상도를 키워도 화질 이득이 없고, 파일 크기도 유지).
    """
    s = min(1.0, max_dim / max(w, h))
    ow, oh = round(w * s) // 2 * 2, round(h * s) // 2 * 2  # H.264 짝수 제약
    ratio = w / h
    target = min(FEED_MAX_RATIO, max(FEED_MIN_RATIO, ratio))
    if target == ratio:
        return ow, oh
    # 16 배수 정렬(인코더 매크로블록) 후, 반올림으로 범위를 벗어나지 않게 보정
    nh = round(math.sqrt(ow * oh / target) / 16) * 16
    nw = round(nh * target / 16) * 16
    while nw / nh < FEED_MIN_RATIO:
        nw += 16
    while nw / nh > FEED_MAX_RATIO:
        nw -= 16
    return nw, nh


def render(
    src: str,
    dst: str,
    m: MotionResult,
    path: CameraPath,
    *,
    out_size: tuple[int, int],
    preset: str,
    crf: int,
    audio_bitrate: str,
) -> None:
    ow, oh = out_size
    video_only = str(Path(dst).with_suffix(".video.mp4"))

    enc = subprocess.Popen(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{ow}x{oh}",
            "-r", f"{m.fps}", "-i", "-",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
            video_only,
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert enc.stdin is not None and enc.stderr is not None
    cap = cv2.VideoCapture(src)
    last = m.n_frames - 1
    i = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            k = min(i, last)
            xa, ya = round(path.x0[k]), round(path.y0[k])
            xb, yb = round(path.x0[k] + path.cw[k]), round(path.y0[k] + path.ch[k])
            out = cv2.resize(frame[ya:yb, xa:xb], (ow, oh), interpolation=cv2.INTER_CUBIC)
            enc.stdin.write(out.tobytes())
            i += 1
    except BrokenPipeError:
        pass  # ffmpeg 가 먼저 죽음 → 아래에서 stderr 로 원인 보고
    finally:
        cap.release()
        enc.stdin.close()
    # communicate() 는 닫힌 stdin 을 flush 하려다 ValueError → stderr 직접 읽기.
    # -v error 라 stderr 가 파이프 버퍼를 채울 만큼 커지지 않아 교착 위험 없음.
    err = enc.stderr.read()
    enc.wait()
    if enc.returncode != 0:
        raise FollowCamError(f"encode failed: {err.decode(errors='replace')[-500:]}")

    mux = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-i", video_only, "-i", src,
            "-map", "0:v", "-map", "1:a?", "-c:v", "copy", "-c:a", "aac", "-b:a", audio_bitrate,
            "-shortest", "-movflags", "+faststart", dst,
        ],
        capture_output=True,
    )
    Path(video_only).unlink(missing_ok=True)
    if mux.returncode != 0:
        raise FollowCamError(f"mux failed: {mux.stderr.decode(errors='replace')[-500:]}")


def run(
    src: str,
    dst: str,
    *,
    follow_target: dict[str, float] | None,
    max_dim: int,
    preset: str,
    crf: int,
    audio_bitrate: str,
) -> dict[str, object]:
    t0 = time.perf_counter()
    m = detect_motion(src)
    t1 = time.perf_counter()
    ow, oh = output_size(m.width, m.height, max_dim)
    # 출력 비율이 원본과 같으면(피드 범위 안) None → 기존과 동일한 원본 비율 구도
    aspect = None if abs(ow / oh - m.width / m.height) < 0.01 else ow / oh
    path = plan_camera(m, follow_target, aspect=aspect)
    render(src, dst, m, path, out_size=(ow, oh), preset=preset, crf=crf, audio_bitrate=audio_bitrate)
    t2 = time.perf_counter()
    return {
        "size": [m.width, m.height],
        "out_size": [ow, oh],
        "frames": m.n_frames,
        "seeded": follow_target is not None,
        "states": {s: path.states.count(s) for s in sorted(set(path.states))},
        "detect_s": round(t1 - t0, 1),
        "render_s": round(t2 - t1, 1),
        # Linux ru_maxrss 단위 KB
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
        "ffmpeg_peak_rss_mb": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024),
    }


def main() -> int:
    """워커가 하위 프로세스로 호출. 결과 통계는 stdout 마지막 줄 JSON."""
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--target", help='JSON {"t": 초, "x": 0~1, "y": 0~1}')
    ap.add_argument("--max-dim", type=int, required=True)
    ap.add_argument("--preset", required=True)
    ap.add_argument("--crf", type=int, required=True)
    ap.add_argument("--audio-bitrate", required=True)
    args = ap.parse_args()
    try:
        stats = run(
            args.src, args.dst,
            follow_target=json.loads(args.target) if args.target else None,
            max_dim=args.max_dim, preset=args.preset, crf=args.crf,
            audio_bitrate=args.audio_bitrate,
        )
    except FollowCamError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
