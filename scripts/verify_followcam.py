"""들찍(팔로우캠) 렌더링 모듈 검증 — DB 무관, 출력은 임시 폴더.

Step ②(app/domain/media/followcam.py)를 검증한다.
- 탭 시드: 탭한 사람을 따라가는지 (다인원 영상에서 대상 전환)
- 역방향 추적: 탭 시점 이전 구간도 같은 사람을 따라가는지
- 예외 입력: 영상 길이를 넘는 t / 사람 없는 곳 탭 → 실패 없이 처리
- 회전 메타데이터: 휴대폰 세로 영상(rotation) 출력 방향
- CLI 종단: 워커가 부르는 방식 그대로 (종료코드, JSON, 코덱/해상도/길이/오디오)

샘플 영상은 저장소에 없다 (사용자 영상). PoC 폴더를 기본값으로 쓴다:
    FOLLOWCAM_SAMPLES=../poc-follow-cam/samples (기본)
    sample.mp4 — 사용자(오른쪽 벽) + 다른 등반자(카키 바지, 왼쪽 벽) 동시 등장
    3dac066b.mp4 — 혼자 등반 (회전 테스트 원본)
실행:
    uv run python -m scripts.verify_followcam
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from app.domain.media import followcam as fc

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

SAMPLES = Path(os.environ.get("FOLLOWCAM_SAMPLES", "../poc-follow-cam/samples"))
MULTI = SAMPLES / "sample.mp4"  # 1080x1920
SOLO = SAMPLES / "3dac066b.mp4"  # 720x1280
# PoC 검출 결과에서 확인한 위치 (원본 1080x1920 기준 → 정규화)
TAP_USER = {"t": 15.0, "x": 1031 / 1080, "y": 1013 / 1920}  # 오른쪽 벽 사용자
TAP_OTHER = {"t": 18.0, "x": 126 / 1080, "y": 914 / 1920}  # 왼쪽 벽 카키 바지

_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f"  ({detail})" if detail else ""))


def center_x_ratio(m: fc.MotionResult, path: fc.CameraPath, t0: float, t1: float, pred) -> float:
    """[t0, t1] 구간 프레임 중 크롭 중심 x 가 pred 를 만족하는 비율."""
    cx = path.x0 + path.cw / 2
    a, b = int(t0 * m.fps), min(int(t1 * m.fps), len(cx))
    return float(np.mean([pred(x) for x in cx[a:b]]))


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height:stream_side_data=rotation:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def main() -> int:
    if not MULTI.exists() or not SOLO.exists():
        print(f"{RED}샘플 없음{RESET}: {SAMPLES} (FOLLOWCAM_SAMPLES 로 지정)")
        return 1

    print("\n[탭 시드 — 다인원 영상 sample.mp4]")
    m = fc.detect_motion(str(MULTI))
    w = m.width
    auto = fc.plan_camera(m)
    r = center_x_ratio(m, auto, 6, 28, lambda x: x > w * 0.6)
    check("탭 없음(자동) → 6~28s 오른쪽 사용자 (PoC 결과 유지)", r >= 0.9, f"{r:.0%}")

    user = fc.plan_camera(m, TAP_USER)
    r = center_x_ratio(m, user, 6, 28, lambda x: x > w * 0.6)
    check("15s 사용자 탭 → 6~28s 오른쪽 사용자 추적", r >= 0.9, f"{r:.0%}")
    r = center_x_ratio(m, user, 0, 3, lambda x: x < w * 0.5)
    check("15s 사용자 탭 → 역방향으로 0~3s 왼쪽 출발 구간까지 추적", r >= 0.9, f"{r:.0%}")

    other = fc.plan_camera(m, TAP_OTHER)
    r = center_x_ratio(m, other, 14, 25, lambda x: x < w * 0.35)
    check("18s 카키바지 탭 → 14~25s 왼쪽 다른 등반자 추적 (대상 전환)", r >= 0.9, f"{r:.0%}")

    print("\n[예외 입력]")
    try:
        p = fc.plan_camera(m, {"t": 999.0, "x": 0.5, "y": 0.5})
        check("영상 길이 넘는 t → 마지막 시점으로 보정, 실패 없음", len(p.x0) == m.n_frames)
    except Exception as e:  # noqa: BLE001 — 검증 목적상 모든 실패를 FAIL 로 기록
        check("영상 길이 넘는 t → 마지막 시점으로 보정, 실패 없음", False, repr(e))
    try:
        p = fc.plan_camera(m, {"t": 10.0, "x": 0.5, "y": 0.02})
        check("사람 없는 곳(천장) 탭 → 실패 없음", len(p.x0) == m.n_frames)
    except Exception as e:  # noqa: BLE001
        check("사람 없는 곳(천장) 탭 → 실패 없음", False, repr(e))

    print("\n[출력 규격 — 피드 비율 (AutoPlayVideo 4:5 ~ 1.91:1)]")
    pw, ph = fc.output_size(1080, 1920, 1280)
    check("세로 9:16 → 피드 범위 비율 + 픽셀 수 기존(720x1280)±5%",
          fc.FEED_MIN_RATIO <= pw / ph <= fc.FEED_MAX_RATIO
          and abs(pw * ph / (720 * 1280) - 1) <= 0.05 and pw % 2 == 0 and ph % 2 == 0,
          f"{pw}x{ph} ratio={pw / ph:.3f}")
    check("가로 16:9 / 정사각 1:1 → 기존과 동일 (원본 비율, 긴 변 1280)",
          fc.output_size(1920, 1080, 1280) == (1280, 720)
          and fc.output_size(1080, 1080, 1280) == (1080, 1080))
    p_new = fc.plan_camera(m, TAP_USER, aspect=pw / ph)
    ratios = p_new.cw / p_new.ch
    check("aspect 지정 → 모든 프레임 크롭 창 비율 = 출력 비율 (찌그러짐 없음)",
          bool(np.all(np.abs(ratios - pw / ph) < 1e-6)), f"{ratios.min():.4f}~{ratios.max():.4f}")
    p_old = fc.plan_camera(m, TAP_USER)
    p_none = fc.plan_camera(m, TAP_USER, aspect=None)
    check("aspect=None → 기존(원본 비율) 경로와 동일 (회귀 없음)",
          bool(np.array_equal(p_old.x0, p_none.x0) and np.array_equal(p_old.cw, p_none.cw)))

    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)

        print("\n[회전 메타데이터 (rotation=90)]")
        rot = tmpd / "rot.mp4"
        # -display_rotation 은 ffmpeg 6.0+ (호스트 7.1), 운영 이미지(bookworm)는 5.1 이라
        # 구식 rotate 메타데이터로 대체. 6.0+ 는 반대로 rotate 를 무시하므로 둘 다 시도.
        made = subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90",
                               "-i", str(SOLO), "-c", "copy", str(rot)], capture_output=True)
        if made.returncode != 0:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(SOLO), "-c", "copy",
                            "-metadata:s:v:0", "rotate=90", str(rot)], check=True)
        rot_v = next(s for s in probe(rot)["streams"] if s["codec_type"] == "video")
        rot_in = (rot_v.get("side_data_list") or [{}])[0].get("rotation", 0)
        check("회전 테스트 입력 생성 (rotation 메타데이터 존재)", rot_in in (90, -90, 270),
              f"rotation={rot_in}")
        dst = tmpd / "rot_out.mp4"
        fc.run(str(rot), str(dst), follow_target=None, max_dim=1280, preset="veryfast",
               crf=23, audio_bitrate="128k")
        v = next(s for s in probe(dst)["streams"] if s["codec_type"] == "video")
        rotation = (v.get("side_data_list") or [{}])[0].get("rotation", 0)
        check("세로 영상(720x1280)+rotation 90 → 화면 방향대로 가로 1280x720 출력",
              (v["width"], v["height"]) == (1280, 720) and rotation == 0,
              f"{v['width']}x{v['height']} rotation={rotation}")

        print("\n[CLI 종단 — 워커 호출 방식]")
        dst = tmpd / "cli_out.mp4"
        proc = subprocess.run(
            [sys.executable, "-m", "app.domain.media.followcam", str(MULTI), str(dst),
             "--target", json.dumps(TAP_USER), "--max-dim", "1280", "--preset", "veryfast",
             "--crf", "23", "--audio-bitrate", "128k"],
            capture_output=True, text=True,
        )
        check("종료 코드 0", proc.returncode == 0, proc.stderr[-200:])
        stats: dict = {}  # 파싱 실패 시에도 아래 검사가 NameError 대신 FAIL 로 기록되도록
        try:
            stats = json.loads(proc.stdout.strip().splitlines()[-1])
            check("stdout 마지막 줄 JSON 통계", stats.get("seeded") is True, json.dumps(stats))
        except (json.JSONDecodeError, IndexError):
            check("stdout 마지막 줄 JSON 통계", False, proc.stdout[-200:])
        info = probe(dst)
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        a = [s for s in info["streams"] if s["codec_type"] == "audio"]
        check(f"H.264, 피드 비율 출력 (1080x1920 → {pw}x{ph}), stats.out_size 일치",
              v["codec_name"] == "h264" and (v["width"], v["height"]) == (pw, ph)
              and stats.get("out_size") == [pw, ph],
              f"{v['codec_name']} {v['width']}x{v['height']}")
        check("원본 오디오 유지 (AAC)", len(a) == 1 and a[0]["codec_name"] == "aac")
        src_dur = float(probe(MULTI)["format"]["duration"])
        dur = float(info["format"]["duration"])
        check("길이 유지 (±0.2s)", abs(dur - src_dur) <= 0.2, f"{dur:.2f}s / 원본 {src_dur:.2f}s")
        check("임시 영상(.video.mp4) 정리됨", not any(tmpd.glob("*.video.mp4")))

        # 워커는 '실패 = 종료코드 1 + stderr 메시지' 계약으로 failed 처리한다
        broken = tmpd / "broken.mp4"
        broken.write_bytes(b"not a video")
        proc = subprocess.run(
            [sys.executable, "-m", "app.domain.media.followcam", str(broken), str(tmpd / "x.mp4"),
             "--max-dim", "1280", "--preset", "veryfast", "--crf", "23", "--audio-bitrate", "128k"],
            capture_output=True, text=True,
        )
        check("깨진 파일 → 종료코드 1 + stderr 원인 (트레이스백 아님)",
              proc.returncode == 1 and proc.stderr.strip() != "" and "Traceback" not in proc.stderr,
              proc.stderr.strip()[-120:])

    total = len(_results)
    passed = sum(_results)
    print(f"\n{'─' * 40}")
    if passed == total:
        print(f"{GREEN}ALL PASS{RESET}  ({passed}/{total})")
        return 0
    print(f"{RED}FAILED{RESET}  ({passed}/{total})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
