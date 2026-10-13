"""짐 색체계 변경 이력 검증 (ADR-069, rollback — 실제 변경 없음).

보정치(difficulty_offset)·색 순서가 개인 종합점수를 직접 움직이게 되면서(ADR-068)
변경을 이력으로 남긴다. 값이 실제로 바뀐 경우만, 같은 트랜잭션으로.

실행:
    uv run python -m scripts.verify_gym_system_audit
"""

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.domain.grade.repository import GradeRepository
from app.domain.grade.service import GradeService
from app.domain.users.models import User
from app.infra.db.engine import close_engine, init_engine
from app.main import app as fastapi_app

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f"  ({detail})" if detail else ""))


def make_user(nick: str, *, admin: bool) -> User:
    uid = uuid.uuid4().hex[:8]
    return User(
        email=f"audit_{nick}_{uid}@clog.test",
        nickname=f"{nick}_{uid}",
        auth_provider="local",
        email_verified=True,
        is_public=True,
        is_admin=admin,
    )


async def run(session: AsyncSession) -> None:
    service = GradeService(GradeRepository(session), session)
    admin = make_user("admin", admin=True)
    member = make_user("member", admin=False)
    session.add_all([admin, member])
    await session.flush()
    colors = ["빨", "주", "노", "초", "파", "남", "보", "갈", "검"]
    name = f"감사짐_{uuid.uuid4().hex[:6]}"

    print("\n[등록]")
    gym = await service.create_gym_system(
        gym_name=name, color_order=colors, user_id=admin.id, is_admin=True,
        is_official=True, difficulty_offset=0.1, reason="공식 암장 등록",
    )
    h = await service.list_gym_system_changes(gym.id)
    check("create 이력 1건 (before 없음, after 스냅샷, 사유)",
          len(h) == 1 and h[0].action == "create" and h[0].before is None
          and h[0].after is not None and h[0].after["difficulty_offset"] == 0.1
          and h[0].reason == "공식 암장 등록" and h[0].changed_by == admin.id)

    print("\n[수정]")
    await service.update_gym_system(
        system_id=gym.id, color_order=colors, user_id=admin.id, is_admin=True,
        difficulty_offset=0.25, reason="회원 체감 반영: 같은 색이 더 어려움",
    )
    h = await service.list_gym_system_changes(gym.id)
    upd = [c for c in h if c.action == "update"]
    check("보정치 변경 → update 이력 (0.1 → 0.25, 사유)",
          len(upd) == 1 and upd[0].before is not None and upd[0].after is not None
          and upd[0].before["difficulty_offset"] == 0.1
          and upd[0].after["difficulty_offset"] == 0.25
          and upd[0].reason == "회원 체감 반영: 같은 색이 더 어려움")

    await service.update_gym_system(
        system_id=gym.id, color_order=colors, user_id=admin.id, is_admin=True,
        difficulty_offset=0.25,
    )
    h = await service.list_gym_system_changes(gym.id)
    check("값이 같은 수정은 이력 안 남김", len(h) == 2, f"{len(h)}건")

    new_colors = colors[:-1] + ["회", "검"]
    await service.update_gym_system(
        system_id=gym.id, color_order=new_colors, user_id=admin.id, is_admin=True,
        difficulty_offset=0.25, gym_name=name + "_개명",
    )
    h = await service.list_gym_system_changes(gym.id)
    last = h[0]
    check("색 순서·개명 변경도 before/after 로 남음",
          len(h) == 3 and last.before is not None and last.after is not None
          and last.before["color_order"] == colors
          and last.after["color_order"] == new_colors
          and last.after["gym_name"] == name + "_개명" and last.gym_name == name + "_개명")

    print("\n[일반 사용자]")
    own = await service.create_gym_system(
        gym_name=name + "_개인", color_order=colors, user_id=member.id, is_admin=False,
        difficulty_offset=0.5,
    )
    await service.update_gym_system(
        system_id=own.id, color_order=colors, user_id=member.id, is_admin=False,
        difficulty_offset=0.5,
    )
    h_own = await service.list_gym_system_changes(own.id)
    check("일반 사용자가 보낸 보정치는 무시 → 등록 시 0, 같은 값 수정은 이력 없음",
          len(h_own) == 1 and h_own[0].after is not None
          and h_own[0].after["difficulty_offset"] == 0.0, f"{len(h_own)}건")

    print("\n[삭제]")
    await service.delete_gym_system(
        system_id=gym.id, user_id=admin.id, is_admin=True, reason="폐점"
    )
    h = await service.list_gym_system_changes(gym.id)
    check("삭제 후에도 id 로 이력 조회 + delete 이력(after 없음, 사유)",
          len(h) == 4 and h[0].action == "delete" and h[0].after is None
          and h[0].before is not None and h[0].reason == "폐점")
    check("최신순 정렬 (한 트랜잭션 안에서도 시각이 달라 순서 확정)",
          [c.action for c in h] == ["delete", "update", "update", "create"]
          and len({c.created_at for c in h}) == 4, f"{[c.action for c in h]}")

    print("\n[라우트]")
    routes = {
        (getattr(r, "path", ""), m)
        for r in fastapi_app.routes
        for m in getattr(r, "methods", set()) or set()
    }
    check("GET /gym-grade-systems/{system_id}/history 등록",
          ("/gym-grade-systems/{system_id}/history", "GET") in routes)
    route = next(r for r in fastapi_app.routes
                 if getattr(r, "path", "") == "/gym-grade-systems/{system_id}/history")
    deps = [d.call.__name__ for d in route.dependant.dependencies]  # type: ignore[attr-defined]
    check("관리자 전용 (require_admin 의존성)", "require_admin" in deps, f"{deps}")


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
