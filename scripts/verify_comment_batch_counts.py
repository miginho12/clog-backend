"""댓글 배치 집계 검증 (rollback — 실제 변경 없음).

감사 L1 B-2(SQLAlchemy 2.1): Row 타입이 정확해지면서 reply_counts_by_parents 의 결과 조립을
tuples() + None 필터로 바꿨다. 이 함수와 같은 형태의 count_by_logs 가 피드·댓글 화면의
댓글 수/대댓글 수 숫자를 만들므로, 삭제 제외·0건 키 생략·빈 입력을 실제 DB 로 확인한다.

실행:
    uv run python -m scripts.verify_comment_batch_counts
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.domain.climbing.repository import ClimbingRepository
from app.domain.comments.repository import CommentRepository
from app.domain.users.models import User
from app.infra.db.engine import close_engine, init_engine

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f"  ({detail})" if detail else ""))


async def run(session: AsyncSession) -> None:
    uid = uuid.uuid4().hex[:8]
    user = User(email=f"cnt_{uid}@clog.test", nickname=f"cnt_{uid}", auth_provider="local", email_verified=True)
    session.add(user)
    await session.flush()

    crepo = ClimbingRepository(session)
    repo = CommentRepository(session)
    logs = []
    for _ in range(3):
        logs.append(await crepo.create(
            user_id=user.id, grade_raw="V3", grade_system="v_scale", gym_name="테스트짐",
            categories=[], attempts=1, is_success=True, visibility="public",
        ))
    log_a, log_b, log_c = logs  # c 는 댓글 없음

    async def add(log_id: uuid.UUID, parent_id: uuid.UUID | None = None) -> uuid.UUID:
        c = await repo.create(user_id=user.id, log_id=log_id, content="x", parent_id=parent_id)
        return c.id

    p1 = await add(log_a.id)
    p2 = await add(log_a.id)
    p3 = await add(log_b.id)  # 대댓글 없는 부모
    for _ in range(3):
        await add(log_a.id, p1)
    r_deleted = await add(log_a.id, p2)
    await add(log_a.id, p2)
    deleted_reply = await repo.get_by_id(r_deleted)
    assert deleted_reply is not None
    await repo.soft_delete(deleted_reply)

    print("\n[reply_counts_by_parents]")
    rc = await repo.reply_counts_by_parents([p1, p2, p3])
    check("p1 대댓글 3", rc.get(p1) == 3, f"{rc.get(p1)}")
    check("p2 삭제된 대댓글 제외 → 1", rc.get(p2) == 1, f"{rc.get(p2)}")
    check("대댓글 없는 p3 는 키 없음", p3 not in rc, f"{rc}")
    check("키는 전부 UUID(None 없음)", all(isinstance(k, uuid.UUID) for k in rc))
    check("빈 입력 → {}", await repo.reply_counts_by_parents([]) == {})

    print("\n[count_by_logs]")
    cl = await repo.count_by_logs([log_a.id, log_b.id, log_c.id])
    # log_a: 부모 2 + p1 대댓글 3 + p2 대댓글 2 중 1 삭제 = 6
    check("log_a 댓글 6(대댓글 포함·삭제 제외)", cl.get(log_a.id) == 6, f"{cl.get(log_a.id)}")
    check("log_b 댓글 1", cl.get(log_b.id) == 1, f"{cl.get(log_b.id)}")
    check("댓글 없는 log_c 는 키 없음", log_c.id not in cl)
    check("빈 입력 → {}", await repo.count_by_logs([]) == {})


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
