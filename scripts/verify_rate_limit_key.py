"""rate limit 키 함수가 인증된 요청에서 실제로 user_id 를 쓰는지 검증
(DB 미사용 — in-process 단위 검증).

보안 QA: app/core/rate_limit.py 의 _key_func 는 "인증된 사용자는 user_id
기반(IP 우회 방지)"라고 문서화돼 있었지만, request.state.user_id 를
채우는 코드가 어디에도 없어 실제로는 인증 여부와 무관하게 항상 IP 키로
떨어지고 있었다. get_current_user/get_optional_user_id 가 검증 통과 시
request.state.user_id 를 채우도록 고쳤다 — 이 스크립트는 그 결과값과
rate_limit._key_func 가 그 값을 실제로 읽어 쓰는지를 함께 확인한다.

실행:
    uv run python -m scripts.verify_rate_limit_key
"""

import asyncio
import sys
import uuid
from dataclasses import dataclass

from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.api.dependencies import get_current_user, get_optional_user_id
from app.core.rate_limit import _key_func
from app.core.security import create_access_token

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
_results: list[bool] = []


def check(name: str, ok: bool) -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}")


def make_request() -> Request:
    return Request(scope={"type": "http", "headers": [], "method": "GET", "path": "/"})


@dataclass
class FakeUser:
    id: uuid.UUID
    is_banned: bool = False


class FakeUserRepo:
    """DB 없이 get_current_user 를 통과시키기 위한 스텁."""

    def __init__(self, user: FakeUser):
        self.user = user

    async def get_by_id_active(self, user_id):
        if user_id == self.user.id:
            return self.user
        return None


async def run() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    repo = FakeUserRepo(FakeUser(id=user_id))

    # ── get_current_user: 인증 통과 후 request.state.user_id 채움 ──
    request = make_request()
    check(
        "인증 전에는 rate limit 키가 IP 기반",
        _key_func(request).startswith("ip:"),
    )
    user = await get_current_user(request, creds, repo)
    check("get_current_user 가 올바른 사용자를 반환", user.id == user_id)
    check(
        "인증 통과 후 request.state.user_id 가 채워짐",
        getattr(request.state, "user_id", None) == str(user_id),
    )
    check(
        "인증 후에는 rate limit 키가 user_id 기반(IP 우회 방지)",
        _key_func(request) == f"user:{user_id}",
    )

    # ── get_optional_user_id: 유효 토큰이면 동일하게 동작 ──
    request2 = make_request()
    returned_id = await get_optional_user_id(request2, creds)
    check("get_optional_user_id 가 올바른 user_id 반환", returned_id == user_id)
    check(
        "선택적 인증에서도 request.state.user_id 가 채워짐",
        _key_func(request2) == f"user:{user_id}",
    )

    # ── 토큰 없는 요청은 여전히 IP 기반이어야 함 ──
    request3 = make_request()
    returned_none = await get_optional_user_id(request3, None)
    check("토큰 없는 선택적 인증은 None 반환", returned_none is None)
    check(
        "토큰 없으면 rate limit 키가 여전히 IP 기반",
        _key_func(request3).startswith("ip:"),
    )


async def main() -> int:
    await run()
    total = len(_results)
    passed = sum(_results)
    print(f"\n{'─' * 40}")
    if passed == total:
        print(f"{GREEN}ALL PASS{RESET}  ({passed}/{total})  — DB 미사용")
        return 0
    print(f"{RED}FAILED{RESET}  ({passed}/{total})")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
