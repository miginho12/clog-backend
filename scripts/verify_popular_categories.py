"""인기 태그 집계(GET /climbing-logs/meta/categories/popular) 검증 (rollback — 실제 변경 없음).

집계 대상: 공개 계정 + 공개 글만. 다음은 전부 제외되어야 함:
- 비공개 계정의 공개 글
- 공개 계정의 비공개 글
- 삭제된 글
카운트/정렬(내림차순)도 함께 검증.

실행:
    uv run python -m scripts.verify_popular_categories
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


def check(name, ok):
    _results.append(ok)
    print(f"  [{(GREEN+'PASS' if ok else RED+'FAIL')+RESET}] {name}")


def make_user(nick, *, is_public=True):
    uid = uuid.uuid4().hex[:8]
    return User(email=f"pc_{uid}@clog.test", nickname=f"{nick}_{uid}",
               auth_provider="local", email_verified=True, is_public=is_public)


async def run(session: AsyncSession):
    crepo = ClimbingRepository(session)

    pub = make_user("pub", is_public=True)
    priv_acct = make_user("privacct", is_public=False)
    session.add_all([pub, priv_acct])
    await session.flush()

    async def log(user_id, categories, *, visibility="public"):
        return await crepo.create(
            user_id=user_id, grade_raw="V4", grade_system="v_scale",
            gym_name="짐", categories=categories, attempts=1, is_success=True,
            visibility=visibility,
        )

    # 태그 사용 고유 마커 (다른 테스트 실행/기존 데이터와 안 섞이게)
    tag = f"검증태그{uuid.uuid4().hex[:6]}"
    tag_rare = f"희귀태그{uuid.uuid4().hex[:6]}"

    # 카운트되어야 함: 공개계정 + 공개글, tag 3회
    await log(pub.id, [tag], visibility="public")
    await log(pub.id, [tag], visibility="public")
    await log(pub.id, [tag, tag_rare], visibility="public")

    # 카운트되면 안 됨: 공개계정 + 비공개글
    deleted_log = None
    await log(pub.id, [tag], visibility="private")

    # 카운트되면 안 됨: 비공개계정 + 공개글
    await log(priv_acct.id, [tag], visibility="public")

    # 카운트되면 안 됨: 삭제된 글 (공개계정 + 공개글이었지만 soft delete)
    deleted_log = await log(pub.id, [tag], visibility="public")
    await crepo.soft_delete(deleted_log)

    rows = await crepo.count_popular_categories(limit=30)
    counts = dict(rows)

    print("\n[집계 대상 필터]")
    check(f"'{tag}' 카운트 == 3 (공개계정+공개글만)", counts.get(tag) == 3)
    check(f"'{tag_rare}' 카운트 == 1", counts.get(tag_rare) == 1)

    print("\n[정렬]")
    tags_in_order = [t for t, _ in rows]
    idx_tag = tags_in_order.index(tag) if tag in tags_in_order else -1
    idx_rare = tags_in_order.index(tag_rare) if tag_rare in tags_in_order else -1
    check("더 많이 쓰인 태그가 먼저 (내림차순)", 0 <= idx_tag < idx_rare)

    print("\n[limit]")
    limited = await crepo.count_popular_categories(limit=1)
    check("limit=1 → 결과 1개", len(limited) == 1)
    check("limit=1 → 가장 많이 쓰인 태그", limited[0][0] == tags_in_order[0])


async def main():
    engine = init_engine()
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint",
                               expire_on_commit=False, autoflush=False)
        try:
            await run(session)
        finally:
            await session.close()
            await outer.rollback()
    await close_engine()
    total, passed = len(_results), sum(_results)
    print(f"\n{'─'*40}")
    if passed == total:
        print(f"{GREEN}ALL PASS{RESET}  ({passed}/{total})  — DB 변경 없음(rollback)")
        return 0
    print(f"{RED}FAILED{RESET}  ({passed}/{total})")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
