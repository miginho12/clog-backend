"""카카오 프로필 이미지 https 검증 (2026-11-12, 감사 M3 부채) — rollback, DB 변경 없음.

1) KakaoOAuthClient.fetch_user_info 가 /v2/user/me 에 secure_resource=true 를 보낸다(가짜 transport, 외부 호출 없음)
2) get_profile_image() 의 http → https 방어 변환이 그대로다
3) 마이그레이션 6a4fc0e704e9 의 SQL 이 http://*.kakaocdn.net 만 https 로 바꾸고, 다른 주소·https·NULL
   은 건드리지 않으며, 두 번 돌려도 같다(savepoint 안에서 실행 후 바깥 트랜잭션 rollback)

실행:
    uv run python -m scripts.verify_kakao_profile_https
"""

import asyncio
import importlib.util
import pathlib
import sys
import uuid

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401 — 전체 모델 로드(FK 참조 등록)
from app.domain.auth.kakao_schemas import KakaoUserInfo
from app.domain.users.models import User
from app.infra.db.engine import close_engine, init_engine
from app.infra.kakao.client import KakaoOAuthClient

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
_results: list[bool] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f"  ({extra})" if extra else ""))


def load_migration():
    root = pathlib.Path(__file__).resolve().parent.parent
    path = next((root / "alembic" / "versions").glob("*6a4fc0e704e9*.py"))
    spec = importlib.util.spec_from_file_location("mig_6a4fc0e704e9", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def check_client() -> None:
    print("\n[1] 카카오 사용자 정보 요청")
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"id": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await KakaoOAuthClient(http).fetch_user_info("fake-token")
    req = seen[0] if seen else None
    check("요청 1회", len(seen) == 1)
    check(
        "secure_resource=true 쿼리",
        req is not None and req.url.params.get("secure_resource") == "true",
        str(req.url.params) if req else "",
    )
    check(
        "인증 헤더 유지",
        req is not None and req.headers.get("authorization") == "Bearer fake-token",
    )


def check_schema_defense() -> None:
    print("\n[2] get_profile_image 방어 변환")

    def img(url: str | None) -> str | None:
        return KakaoUserInfo.model_validate(
            {"id": 1, "kakao_account": {"profile": {"profile_image_url": url}}}
        ).get_profile_image()

    check(
        "http → https", img("http://k.kakaocdn.net/dn/a.jpg") == "https://k.kakaocdn.net/dn/a.jpg"
    )
    check(
        "https 그대로", img("https://k.kakaocdn.net/dn/a.jpg") == "https://k.kakaocdn.net/dn/a.jpg"
    )
    check("없음 → None", img(None) is None)


CASES = [  # (저장된 값, 마이그레이션 후 기대값)
    ("http://k.kakaocdn.net/dn/a/img_640x640.jpg", "https://k.kakaocdn.net/dn/a/img_640x640.jpg"),
    (
        "http://img1.kakaocdn.net/thumb/R640x640/b.jpg",
        "https://img1.kakaocdn.net/thumb/R640x640/b.jpg",
    ),
    ("http://kakaocdn.net/c.jpg", "https://kakaocdn.net/c.jpg"),
    ("https://k.kakaocdn.net/dn/d.jpg", "https://k.kakaocdn.net/dn/d.jpg"),  # 이미 https
    (
        "http://evil.example.com/kakaocdn.net/e.jpg",
        "http://evil.example.com/kakaocdn.net/e.jpg",
    ),  # 경로에만
    (
        "http://k.kakaocdn.net.evil.example.com/f.jpg",
        "http://k.kakaocdn.net.evil.example.com/f.jpg",
    ),  # 접미 위장
    ("http://localhost/clog-media/g.jpg", "http://localhost/clog-media/g.jpg"),  # 카카오 아님
    (None, None),
]


async def check_migration(session: AsyncSession) -> None:
    print("\n[3] 마이그레이션 6a4fc0e704e9 SQL")
    mig = load_migration()
    tag = uuid.uuid4().hex[:8]
    users = [
        User(
            email=f"kp_{tag}_{i}@example.com",
            nickname=f"kp_{tag}_{i}",
            auth_provider="kakao",
            email_verified=True,
            profile_image_url=before,
        )
        for i, (before, _) in enumerate(CASES)
    ]
    session.add_all(users)
    await session.flush()
    ids = [u.id for u in users]
    before_updated = {u.id: u.updated_at for u in users}

    await session.execute(text(mig.SQL), {"pattern": mig.KAKAO_HTTP})
    session.expire_all()
    rows = {
        r.id: r for r in (await session.execute(select(User).where(User.id.in_(ids)))).scalars()
    }
    for (before, expected), uid in zip(CASES, ids, strict=True):
        got = rows[uid].profile_image_url
        check(
            f"{before!s:52} → {'변환' if before != expected else '그대로'}",
            got == expected,
            f"실제 {got}",
        )
    check("updated_at 안 바뀜", all(rows[i].updated_at == before_updated[i] for i in ids))

    second = await session.execute(
        text(mig.SQL + " AND id = ANY(:ids)"), {"pattern": mig.KAKAO_HTTP, "ids": ids}
    )
    check("두 번째 실행은 0행(멱등)", second.rowcount == 0, f"{second.rowcount}행")


async def main() -> int:
    await check_client()
    check_schema_defense()
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
            await check_migration(session)
        finally:
            await session.close()
            await outer.rollback()  # 테스트 사용자·UPDATE 전부 되돌림 (DB 무변경)
    await close_engine()

    total, passed = len(_results), sum(_results)
    print(f"\n{'─' * 40}")
    if passed == total:
        print(f"{GREEN}ALL PASS{RESET}  ({passed}/{total})  — DB 변경 없음(rollback)")
        return 0
    print(f"{RED}FAILED{RESET}  ({passed}/{total})")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
