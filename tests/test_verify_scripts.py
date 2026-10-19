"""scripts/verify_*.py 를 CI 에서 돌리기 위한 pytest 래퍼 (ADR-072).

기능 검증은 verify 스크립트(ALL PASS + 종료코드 0)로 해 왔지만 CI 는 pytest 만
돌려서, 실제로 CI 가 지키는 건 헬스체크뿐이었다. 스크립트를 자동 수집해
서브프로세스로 실행하고 종료코드 0 을 확인한다 → 새 스크립트도 자동 편입.

외부 준비물이 필요한 스크립트는 LOCAL_ONLY 에 사유와 함께 둔다 (기본 skip).
로컬에서 준비물을 갖춘 뒤 함께 돌리려면:
  VERIFY_LOCAL_ONLY=1 uv run pytest tests/test_verify_scripts.py -v

실행: uv run pytest tests/test_verify_scripts.py -v
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted(p.stem for p in (ROOT / "scripts").glob("verify_*.py"))

# 스크립트 이름 → CI 에서 돌릴 수 없는 이유
LOCAL_ONLY = {
    "verify_followcam": "샘플 영상(../poc-follow-cam/samples) 필요 — 사용자 영상이라 저장소에 없음",
    "verify_followcam_worker": "샘플 영상(../poc-follow-cam/samples) 필요 — 사용자 영상이라 저장소에 없음",
    "verify_multipart_upload": "임시 MinIO(127.0.0.1:19000) 필요",
}
RUN_LOCAL_ONLY = os.environ.get("VERIFY_LOCAL_ONLY") == "1"

# 가장 느린 스크립트(followcam ~90s, Pi 기준)의 2배 이상
TIMEOUT_S = 300


def test_scripts_collected() -> None:
    """glob 경로가 틀려 0개 수집 → 전부 통과처럼 보이는 일을 막는다."""
    assert SCRIPTS, "scripts/verify_*.py 를 하나도 찾지 못함"
    unknown = set(LOCAL_ONLY) - set(SCRIPTS)
    assert not unknown, f"LOCAL_ONLY 에 없는 스크립트: {unknown}"


@pytest.mark.parametrize("name", SCRIPTS)
def test_verify_script(name: str) -> None:
    if name in LOCAL_ONLY and not RUN_LOCAL_ONLY:
        pytest.skip(LOCAL_ONLY[name])

    result = subprocess.run(
        [sys.executable, "-m", f"scripts.{name}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
    )
    # 실패 시 어떤 항목이 FAIL 인지 바로 보이도록 출력 끝부분을 붙인다
    output = (result.stdout + result.stderr)[-3000:]
    assert result.returncode == 0, f"{name} 실패 (exit {result.returncode})\n{output}"
