"""암장 이름(gym_name) 개명 검증 (rollback — 실제 변경 없음).

관리자페이지에서 gym_name 을 못 고치던 걸 admin 전용으로 열면서, 개명 시
climbing_logs.gym_name(문자열 매칭 키, FK 아님)도 같은 트랜잭션에서
일괄 갱신되는지 확인한다. 안 하면 옛 이름으로 이미 기록된 과거 완등
기록이 조용히(에러 없이) 매칭 실패해 그레이드 계산에서 빠진다.

실행:
    uv run python -m scripts.verify_gym_rename

savepoint 패턴 — 마지막에 바깥 트랜잭션 rollback, DB 에 아무것도 남지 않는다.
"""

import asyncio
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401 — 전체 모델 로드(FK 참조 등록)
from app.domain.climbing.models import ClimbingLog
from app.domain.grade.exceptions import GymGradeSystemAlreadyExists
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
        email=f"gymrn_{uid}@clog.test",
        nickname=f"{nickname}_{uid}",
        auth_provider="local",
        email_verified=True,
        is_admin=is_admin,
    )


def gym_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def run(session: AsyncSession) -> None:
    repo = GradeRepository(session)
    svc = GradeService(session=session, repo=repo)

    admin = make_user("admin", is_admin=True)
    owner = make_user("owner")
    session.add_all([admin, owner])
    await session.flush()

    base = ["흰", "노", "검"]
    old_name = gym_name("old")
    other_name = gym_name("other")

    system = await repo.create(
        gym_name=old_name, color_order=base, created_by=None, is_official=True
    )
    await repo.create(
        gym_name=other_name, color_order=base, created_by=None, is_official=True
    )
    log = ClimbingLog(
        user_id=owner.id, grade_raw="보라", gym_name=old_name
    )
    session.add(log)
    await session.flush()

    # ── 일반 유저: gym_name 무시(기존 이름 유지, 다른 admin 전용 필드와 동일 패턴) ──
    print("\n[일반 유저 개명 시도 → 무시]")
    own_gym = await repo.create(
        gym_name=gym_name("own"), color_order=base, created_by=owner.id
    )
    await session.flush()
    attempted_new_name = gym_name("attempted")
    u_ignored = await svc.update_gym_system(
        system_id=own_gym.id,
        color_order=base,
        user_id=owner.id,
        is_admin=False,
        gym_name=attempted_new_name,
    )
    check(
        "일반 유저가 gym_name 보내도 기존 이름 유지(무시)",
        u_ignored.gym_name == own_gym.gym_name
        and u_ignored.gym_name != attempted_new_name,
    )

    # ── admin: 중복 이름으로 개명 시도 → 409 ──
    print("\n[admin 개명 - 중복]")
    try:
        await svc.update_gym_system(
            system_id=system.id,
            color_order=base,
            user_id=admin.id,
            is_admin=True,
            gym_name=other_name,  # 이미 other_system 이 쓰는 이름
        )
        check("중복 이름 개명 → GymGradeSystemAlreadyExists(409)", False)
    except GymGradeSystemAlreadyExists:
        check("중복 이름 개명 → GymGradeSystemAlreadyExists(409)", True)

    # ── admin: 정상 개명 → climbing_logs.gym_name 도 같이 갱신 ──
    print("\n[admin 개명 - 정상, cascade 확인]")
    new_name = gym_name("new")
    updated = await svc.update_gym_system(
        system_id=system.id,
        color_order=base,
        user_id=admin.id,
        is_admin=True,
        gym_name=new_name,
    )
    check("GymGradeSystem.gym_name 갱신됨", updated.gym_name == new_name)

    refreshed_log = (
        await session.execute(select(ClimbingLog).where(ClimbingLog.id == log.id))
    ).scalar_one()
    check(
        "climbing_logs.gym_name 도 새 이름으로 일괄 갱신(cascade)",
        refreshed_log.gym_name == new_name,
    )
    old_name_orphans = (
        await session.execute(
            select(ClimbingLog).where(ClimbingLog.gym_name == old_name)
        )
    ).scalars().all()
    check("옛 이름으로 남은 고아 기록 없음", len(old_name_orphans) == 0)

    # ── admin: 같은 이름으로 "변경" 요청(no-op) → 정상 처리 ──
    print("\n[admin 개명 - 동일 이름(no-op)]")
    noop = await svc.update_gym_system(
        system_id=system.id,
        color_order=base,
        user_id=admin.id,
        is_admin=True,
        gym_name=new_name,  # 이미 이 이름
    )
    check("동일 이름 재전송은 에러 없이 처리(no-op)", noop.gym_name == new_name)


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
            await outer.rollback()  # DB 에 아무것도 남지 않음
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
