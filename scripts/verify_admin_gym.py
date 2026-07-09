"""admin 암장 관리 E2E 검증 (rollback — 실제 변경 없음).

Step 2(암장 색체계 is_official 등록 + CRUD admin 우회)의 동작을 검증한다.

실행 (dev DB 환경, Alembic 돌릴 때와 동일):
    uv run python -m scripts.verify_admin_gym

savepoint 패턴으로 서비스 내부 commit 을 savepoint 로 가두고, 마지막에
바깥 트랜잭션을 rollback 하므로 DB 에 아무것도 남지 않는다.
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401 — 전체 모델 로드(FK 참조 등록)
from app.domain.grade.exceptions import GymGradeSystemForbidden
from app.domain.grade.repository import GradeRepository
from app.domain.grade.service import GradeService
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
        email=f"gym_{uid}@clog.test",
        nickname=f"{nickname}_{uid}",
        auth_provider="local",
        email_verified=True,
        is_admin=is_admin,
    )


def gym_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def expect_forbidden(coro, name: str) -> None:
    try:
        await coro
        check(name, False)
    except GymGradeSystemForbidden:
        check(name, True)


async def run(session: AsyncSession) -> None:
    repo = GradeRepository(session)
    svc = GradeService(session=session, repo=repo)

    owner = make_user("owner")
    admin = make_user("admin", is_admin=True)
    bystander = make_user("bystander")
    session.add_all([owner, admin, bystander])
    await session.flush()

    base = ["흰", "노", "검"]
    edited = ["흰", "노", "파", "검"]

    # ── 등록 (is_official) ───────────────────────────────────
    print("\n[암장 등록]")
    # 일반 사용자가 is_official=True 요청 → 강제 False
    s1 = await svc.create_gym_system(
        gym_name=gym_name("user_off"),
        color_order=base,
        user_id=owner.id,
        is_admin=False,
        is_official=True,
    )
    check("일반유저 is_official=True 요청 → 강제 False", s1.is_official is False)
    check("일반유저 등록분 created_by=본인", s1.created_by == owner.id)

    # admin 이 공식 암장 등록 → is_official=True
    s2 = await svc.create_gym_system(
        gym_name=gym_name("admin_off"),
        color_order=base,
        user_id=admin.id,
        is_admin=True,
        is_official=True,
    )
    check("admin 공식 암장 등록(is_official=True)", s2.is_official is True)
    check("admin 등록분 created_by=admin(감사)", s2.created_by == admin.id)

    # 일반 등록(비공식)
    s3 = await svc.create_gym_system(
        gym_name=gym_name("user_plain"),
        color_order=base,
        user_id=owner.id,
        is_admin=False,
        is_official=False,
    )
    check("일반 등록 is_official=False", s3.is_official is False)

    # ── 수정 (color_order) ──────────────────────────────────
    print("\n[암장 수정]")
    owner_gym = await repo.create(
        gym_name=gym_name("owner"),
        color_order=base,
        created_by=owner.id,
        is_official=False,
    )
    official_gym = await repo.create(  # 시드형 공식(created_by NULL)
        gym_name=gym_name("seed"),
        color_order=base,
        created_by=None,
        is_official=True,
    )
    await session.flush()

    # 비소유자 일반유저가 남의 등록분 수정 → 차단
    await expect_forbidden(
        svc.update_gym_system(
            system_id=owner_gym.id,
            color_order=edited,
            user_id=bystander.id,
            is_admin=False,
        ),
        "비소유자 일반유저 수정 차단(403)",
    )
    # 본인이 본인 등록분 수정 → 성공
    u1 = await svc.update_gym_system(
        system_id=owner_gym.id,
        color_order=edited,
        user_id=owner.id,
        is_admin=False,
    )
    check("본인 등록분 수정 성공(color_order 반영)", u1.color_order == edited)

    # 일반유저가 공식 암장 수정 → 차단
    await expect_forbidden(
        svc.update_gym_system(
            system_id=official_gym.id,
            color_order=edited,
            user_id=owner.id,
            is_admin=False,
        ),
        "일반유저 공식 암장 수정 차단(403)",
    )
    # admin 이 공식 암장 수정 → 성공
    u2 = await svc.update_gym_system(
        system_id=official_gym.id,
        color_order=edited,
        user_id=admin.id,
        is_admin=True,
    )
    check("admin 공식 암장 수정 성공", u2.color_order == edited)

    # admin 이 남의 등록분 수정 → 성공
    u3 = await svc.update_gym_system(
        system_id=owner_gym.id,
        color_order=base,
        user_id=admin.id,
        is_admin=True,
    )
    check("admin 이 남의 등록분 수정 성공", u3.color_order == base)

    # ── 삭제 ─────────────────────────────────────────────────
    print("\n[암장 삭제]")
    # 일반유저가 공식 암장 삭제 → 차단
    await expect_forbidden(
        svc.delete_gym_system(
            system_id=official_gym.id, user_id=owner.id, is_admin=False
        ),
        "일반유저 공식 암장 삭제 차단(403)",
    )
    # admin 이 공식 암장 삭제 → 성공
    await svc.delete_gym_system(
        system_id=official_gym.id, user_id=admin.id, is_admin=True
    )
    check(
        "admin 공식 암장 삭제 성공(조회 None)",
        await repo.get_by_id(official_gym.id) is None,
    )

    # 비소유자 일반유저가 남의 등록분 삭제 → 차단
    await expect_forbidden(
        svc.delete_gym_system(
            system_id=owner_gym.id, user_id=bystander.id, is_admin=False
        ),
        "비소유자 일반유저 삭제 차단(403)",
    )
    # admin 이 남의 등록분 삭제 → 성공
    await svc.delete_gym_system(
        system_id=owner_gym.id, user_id=admin.id, is_admin=True
    )
    check(
        "admin 이 남의 등록분 삭제 성공(조회 None)",
        await repo.get_by_id(owner_gym.id) is None,
    )

    # 본인이 본인 등록분 삭제 → 성공(회귀)
    own = await repo.create(
        gym_name=gym_name("own_del"),
        color_order=base,
        created_by=owner.id,
        is_official=False,
    )
    await session.flush()
    await svc.delete_gym_system(
        system_id=own.id, user_id=owner.id, is_admin=False
    )
    check(
        "본인 등록분 삭제 성공(회귀)",
        await repo.get_by_id(own.id) is None,
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
