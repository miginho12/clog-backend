"""admin 콘텐츠 삭제 E2E 검증 (rollback — 실제 변경 없음).

Step 1(게시물/댓글 admin 삭제 우회)의 동작을 실 DB 세션으로 검증한다.

실행 (dev DB 포트포워딩 등, Alembic 돌릴 때와 같은 환경에서):
    uv run python scripts/verify_admin_delete.py

동작 원리:
    바깥 트랜잭션을 열고 세션을 join_transaction_mode="create_savepoint" 로
    붙인다. 서비스가 내부에서 부르는 session.commit() 은 SAVEPOINT 만
    release 하고, 바깥 트랜잭션은 건드리지 않는다. 마지막에 바깥 트랜잭션을
    통째로 rollback 하므로 fixture 를 포함해 DB 에 아무것도 남지 않는다.
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401 — 전체 모델 로드(FK 참조 등록)
from app.domain.climbing.exceptions import (
    ClimbingLogForbidden,
    ClimbingLogNotFound,
)
from app.domain.climbing.repository import ClimbingRepository
from app.domain.climbing.service import ClimbingService
from app.domain.comment_likes.repository import CommentLikeRepository
from app.domain.comments.exceptions import CommentForbidden
from app.domain.comments.repository import CommentRepository
from app.domain.comments.service import CommentService
from app.domain.likes.repository import LikeRepository
from app.domain.notifications.repository import NotificationRepository
from app.domain.notifications.service import NotificationService
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


def make_user(nickname: str, *, is_admin: bool = False) -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"verify_{uid}@clog.test",
        nickname=f"{nickname}_{uid}",
        auth_provider="local",
        email_verified=True,
        is_admin=is_admin,
    )


async def run(session: AsyncSession) -> None:
    climb_repo = ClimbingRepository(session)
    climb_svc = ClimbingService(
        session=session,
        repository=climb_repo,
        like_repo=LikeRepository(session),
        comment_repo=CommentRepository(session),
        comment_like_repo=CommentLikeRepository(session),
    )
    comment_repo = CommentRepository(session)
    comment_svc = CommentService(
        session=session,
        repository=comment_repo,
        climbing_repo=climb_repo,
        like_repo=CommentLikeRepository(session),
        notification_service=NotificationService(
            session, NotificationRepository(session)
        ),
    )

    owner = make_user("owner")
    admin = make_user("admin", is_admin=True)
    bystander = make_user("bystander")
    session.add_all([owner, admin, bystander])
    await session.flush()

    # ── 게시물 삭제 ──────────────────────────────────────────
    print("\n[게시물 삭제]")
    log_a = await climb_repo.create(
        user_id=owner.id, grade_raw="V3", grade_system="v_scale"
    )
    await session.flush()

    # 비소유자 일반유저 → 차단
    try:
        await climb_svc.delete_log(
            log_id=log_a.id, user_id=bystander.id, is_admin=bystander.is_admin
        )
        check("비소유자 일반유저 게시물 삭제 차단(403)", False)
    except ClimbingLogForbidden:
        check("비소유자 일반유저 게시물 삭제 차단(403)", True)

    await session.refresh(log_a)
    check("차단 후 게시물 생존(deleted_at is None)", log_a.deleted_at is None)

    # admin → 남의 글 삭제 성공
    await climb_svc.delete_log(
        log_id=log_a.id, user_id=admin.id, is_admin=admin.is_admin
    )
    await session.refresh(log_a)
    check("admin 이 남의 게시물 삭제(deleted_at set)", log_a.deleted_at is not None)

    # 이미 삭제된 글 재삭제 → NotFound (get_by_id 가 deleted 필터)
    try:
        await climb_svc.delete_log(
            log_id=log_a.id, user_id=admin.id, is_admin=True
        )
        check("삭제된 게시물 재삭제 시 NotFound", False)
    except ClimbingLogNotFound:
        check("삭제된 게시물 재삭제 시 NotFound", True)

    # 본인이 본인 글 삭제 → 성공(회귀)
    log_own = await climb_repo.create(
        user_id=owner.id, grade_raw="빨강", grade_system="color"
    )
    await session.flush()
    await climb_svc.delete_log(
        log_id=log_own.id, user_id=owner.id, is_admin=False
    )
    await session.refresh(log_own)
    check("본인 게시물 삭제 성공(회귀)", log_own.deleted_at is not None)

    # ── 댓글 삭제 (+ 대댓글 cascade) ─────────────────────────
    print("\n[댓글 삭제]")
    log_b = await climb_repo.create(
        user_id=owner.id, grade_raw="V2", grade_system="v_scale"
    )
    await session.flush()
    top = await comment_repo.create(
        user_id=owner.id, log_id=log_b.id, content="top", parent_id=None
    )
    await session.flush()
    reply = await comment_repo.create(
        user_id=bystander.id, log_id=log_b.id, content="reply", parent_id=top.id
    )
    await session.flush()

    # 비소유자 일반유저 → 차단
    try:
        await comment_svc.delete_comment(
            comment_id=top.id, user_id=bystander.id, is_admin=bystander.is_admin
        )
        check("비소유자 일반유저 댓글 삭제 차단(403)", False)
    except CommentForbidden:
        check("비소유자 일반유저 댓글 삭제 차단(403)", True)

    # admin → 남의 최상위 댓글 삭제 + 대댓글 cascade
    await comment_svc.delete_comment(
        comment_id=top.id, user_id=admin.id, is_admin=admin.is_admin
    )
    await session.refresh(top)
    await session.refresh(reply)
    check("admin 이 남의 최상위 댓글 삭제(deleted_at set)", top.deleted_at is not None)
    check("최상위 삭제 시 대댓글 cascade(deleted_at set)", reply.deleted_at is not None)

    # 본인이 본인 댓글 삭제 → 성공(회귀)
    c_own = await comment_repo.create(
        user_id=owner.id, log_id=log_b.id, content="own", parent_id=None
    )
    await session.flush()
    await comment_svc.delete_comment(
        comment_id=c_own.id, user_id=owner.id, is_admin=False
    )
    await session.refresh(c_own)
    check("본인 댓글 삭제 성공(회귀)", c_own.deleted_at is not None)


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
            await outer.rollback()  # fixture 포함 전부 되돌림 (DB 무변경)
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
