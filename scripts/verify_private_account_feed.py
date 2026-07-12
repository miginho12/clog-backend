"""비공개 계정 글 피드 노출 정책 검증 (rollback — 실제 변경 없음).

비공개 계정(is_public=False)의 visibility=public 글은:
- 비로그인 피드: 안 보임
- 타인 로그인 피드: 안 보임
- 본인 피드: 보임
공개 계정(is_public=True)의 public 글은 모두에게 보임 (회귀 없음 확인).

실행:
    uv run python -m scripts.verify_private_account_feed
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.domain.climbing.repository import ClimbingRepository
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


def make_user(nickname, *, is_public=True) -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"pf_{uid}@clog.test",
        nickname=f"{nickname}_{uid}",
        auth_provider="local",
        email_verified=True,
        is_public=is_public,
    )


async def run(session: AsyncSession) -> None:
    crepo = ClimbingRepository(session)

    priv = make_user("private", is_public=False)
    pub = make_user("public", is_public=True)
    viewer = make_user("viewer", is_public=True)
    session.add_all([priv, pub, viewer])
    await session.flush()

    async def add_log(user, vis="public"):
        log = await crepo.create(
            user_id=user.id, grade_raw="V4", grade_system="v_scale",
            gym_name="짐", categories=[], attempts=1, is_success=True,
            visibility=vis,
        )
        await session.flush()
        return log.id

    priv_log = await add_log(priv)   # 비공개 계정의 public 글
    pub_log = await add_log(pub)     # 공개 계정의 public 글

    def ids(rows):
        return {r.id for r in rows}

    print("\n[비로그인 피드]")
    rows, _ = await crepo.list_feed(viewer_id=None)
    check("비공개 계정 글 안 보임", priv_log not in ids(rows))
    check("공개 계정 글 보임 (회귀 없음)", pub_log in ids(rows))

    print("\n[타인 로그인 피드]")
    rows, _ = await crepo.list_feed(viewer_id=viewer.id)
    check("비공개 계정 글 안 보임", priv_log not in ids(rows))
    check("공개 계정 글 보임", pub_log in ids(rows))

    print("\n[본인 피드]")
    rows, _ = await crepo.list_feed(viewer_id=priv.id)
    check("본인은 자기 글 보임", priv_log in ids(rows))

    print("\n[author_id 필터로 비공개 계정 조회 — 타인]")
    rows, _ = await crepo.list_feed(viewer_id=viewer.id, author_id=priv.id)
    check("타인이 비공개 계정 글 목록 조회 → 0건", priv_log not in ids(rows))

    print("\n[author_id 필터 — 본인]")
    rows, _ = await crepo.list_feed(viewer_id=priv.id, author_id=priv.id)
    check("본인이 자기 글 목록 조회 → 보임", priv_log in ids(rows))


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
            await outer.rollback()
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
