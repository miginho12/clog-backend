"""기록/댓글 수정 시 응답 직렬화 500 버그 수정 검증 (rollback — 실제 변경 없음).

버그: updated_at 컬럼이 onupdate=func.now() 라 UPDATE 후 expired 상태로 남는데,
서비스가 `session.refresh(obj, ["user"])` 처럼 attribute_names 를 지정하면
그 목록만 리프레시되고 updated_at 은 여전히 expired 로 남는다. 이후
Pydantic 이 응답 스키마로 변환하며 updated_at 을 읽으려 하면 SQLAlchemy가
암시적 lazy refresh 를 시도하는데, 이게 비동기 세션에서 즉시 await 되지
않는 컨텍스트라 MissingGreenlet 로 500 이 난다.
(2026-07-19: 기록 수정 시 해시태그 추가 후 저장 1번째는 500, 2번째는 성공하던 버그)

수정: refresh 호출에 "updated_at" 을 함께 포함시켜 두 속성 모두 리프레시.

검증 대상:
- ClimbingService.update_log
- CommentService.update_comment
- CommentService.set_pin

savepoint 패턴으로 서비스 내부 commit 을 savepoint 로 가두고, 마지막에
바깥 트랜잭션을 rollback 하므로 DB 에 아무것도 남지 않는다.

실행:
    uv run python -m scripts.verify_update_refresh_fix
"""

import asyncio
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401 — 전체 모델 로드(FK 참조 등록)
from app.core.password import hash_password
from app.domain.climbing.models import ClimbingLog
from app.domain.climbing.repository import ClimbingRepository
from app.domain.climbing.schemas import ClimbingLogResponse
from app.domain.climbing.service import ClimbingService
from app.domain.comment_likes.repository import CommentLikeRepository
from app.domain.comments.models import Comment
from app.domain.comments.repository import CommentRepository
from app.domain.comments.schemas import CommentResponse
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


def make_user(nickname: str) -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"refresh_{uid}@clog.test",
        nickname=f"{nickname}_{uid}",
        auth_provider="local",
        email_verified=True,
        password_hash=hash_password("TestPass1234!"),
    )


def make_log(user: User) -> ClimbingLog:
    return ClimbingLog(
        user_id=user.id,
        grade_raw="V3",
        grade_system="v_scale",
        gym_name="테스트 암장",
        categories=["오버행"],
        attempts=1,
        is_success=True,
        visibility="public",
    )


async def run(session: AsyncSession) -> None:
    climbing_repo = ClimbingRepository(session)
    like_repo = LikeRepository(session)
    comment_repo = CommentRepository(session)
    comment_like_repo = CommentLikeRepository(session)
    notif_repo = NotificationRepository(session)
    notif_service = NotificationService(session, notif_repo)

    user = make_user("author")
    session.add(user)
    await session.flush()

    log = make_log(user)
    session.add(log)
    await session.flush()

    climbing_service = ClimbingService(
        session, climbing_repo, like_repo, comment_repo, comment_like_repo
    )

    # ── ClimbingService.update_log ──
    updated_log = await climbing_service.update_log(
        log_id=log.id, user_id=user.id, data={"categories": ["오버행", "힐훅"]}
    )
    try:
        resp = ClimbingLogResponse.model_validate(updated_log)
        check(
            "update_log 응답 직렬화 (MissingGreenlet 없음)",
            resp.categories == ["오버행", "힐훅"],
        )
    except Exception as e:  # noqa: BLE001
        print(f"    예외: {e!r}")
        check("update_log 응답 직렬화 (MissingGreenlet 없음)", False)

    comment_service = CommentService(
        session, comment_repo, climbing_repo, comment_like_repo, notif_service
    )

    # ── CommentService.update_comment ──
    comment = await comment_service.create_comment(
        log_id=log.id, user_id=user.id, content="원본 댓글", parent_id=None
    )
    updated_comment = await comment_service.update_comment(
        comment_id=comment.id, user_id=user.id, content="수정된 댓글"
    )
    try:
        resp = CommentResponse.model_validate(updated_comment)
        check(
            "update_comment 응답 직렬화 (MissingGreenlet 없음)",
            resp.content == "수정된 댓글",
        )
    except Exception as e:  # noqa: BLE001
        print(f"    예외: {e!r}")
        check("update_comment 응답 직렬화 (MissingGreenlet 없음)", False)

    # ── CommentService.set_pin ──
    pinned_comment = await comment_service.set_pin(
        comment_id=comment.id, user_id=user.id, pinned=True
    )
    try:
        resp = CommentResponse.model_validate(pinned_comment)
        check(
            "set_pin 응답 직렬화 (MissingGreenlet 없음)",
            resp.is_pinned is True,
        )
    except Exception as e:  # noqa: BLE001
        print(f"    예외: {e!r}")
        check("set_pin 응답 직렬화 (MissingGreenlet 없음)", False)

    # ── 첫 시도부터 성공해야 한다(재시도 없이) — updated_at 이 실제로 리프레시됐는지 ──
    from sqlalchemy import inspect

    check(
        "update_log 후 updated_at 이 unloaded 로 남지 않음",
        "updated_at" not in inspect(updated_log).unloaded,
    )
    check(
        "update_comment 후 updated_at 이 unloaded 로 남지 않음",
        "updated_at" not in inspect(updated_comment).unloaded,
    )

    # ORM 상태 확인용 — 실제 DB에 남지 않는지 재확인(같은 세션 내 재조회)
    refetched = await session.execute(
        select(ClimbingLog).where(ClimbingLog.id == log.id)
    )
    check(
        "categories 수정 값이 세션 내 즉시 반영됨",
        refetched.scalar_one().categories == ["오버행", "힐훅"],
    )
    refetched_c = await session.execute(
        select(Comment).where(Comment.id == comment.id)
    )
    check(
        "content 수정 값이 세션 내 즉시 반영됨",
        refetched_c.scalar_one().content == "수정된 댓글",
    )


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
