"""/users API 테스트 (감사 L2, 2026-11-12) — 현재 라우트 기준으로 재작성.

예전 파일은 사라진 POST /users·GET /users 목록을 겨냥해 통째로 skip 이었다.
지금 회원가입은 /auth/register(이메일 인증)라, 사용자는 DB 에 직접 만들고
access token 을 발급해 HTTP 로 호출한다(라우트·인증·예외 핸들러·응답 스키마까지 통과).

- 실제 PostgreSQL·Redis 필요(CI 의 services, 로컬은 기존 pytest 설정과 동일).
- 요청이 커밋되므로 만든 사용자는 끝날 때 id 로 직접 삭제한다(다른 데이터는 건드리지 않음).

실행: uv run pytest tests/test_users_api.py -v
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete

import app.infra.db.models  # noqa: F401  — 모든 모델 매퍼 등록
import app.infra.db.session as db_session
from app.core.security import create_access_token, create_refresh_token
from app.domain.users.models import User
from app.infra.db.engine import close_engine, get_engine, init_engine
from app.infra.redis import close_redis, init_redis
from app.main import app

MakeUser = Callable[..., Awaitable[User]]

# 모듈 전체가 이벤트 루프 하나·엔진 하나를 쓴다. app.infra.db.session 의 sessionmaker 는
# 처음 만든 엔진에 묶여 캐시되므로(운영은 lifespan 1회라 무관), 테스트마다 엔진·루프를
# 새로 만들면 요청이 이전 루프의 연결을 써서 "attached to a different loop" 로 깨진다.
pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # lifespan 대신 users 라우트에 필요한 것만(DB·Redis)
    init_engine()
    init_redis()
    db_session._sessionmaker = None  # 다른 테스트 모듈이 남긴 캐시 대신 지금 엔진에 묶이게
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        await close_redis()
        await close_engine()
        db_session._sessionmaker = None  # 닫힌 엔진에 묶인 캐시를 다음 모듈에 남기지 않음


@pytest_asyncio.fixture(loop_scope="module")
async def make_user(client: httpx.AsyncClient) -> AsyncIterator[MakeUser]:
    from sqlalchemy.ext.asyncio import AsyncSession

    created: list[uuid.UUID] = []

    async def _make(
        nick: str = "user", *, is_public: bool = True, is_banned: bool = False, deleted: bool = False
    ) -> User:
        suffix = uuid.uuid4().hex[:8]
        user = User(
            email=f"l2_{suffix}@example.com",  # .test 는 email-validator 가 예약 도메인으로 거부(응답 검증 실패)
            nickname=f"{nick}_{suffix}",
            auth_provider="local",
            email_verified=True,
            is_public=is_public,
            is_banned=is_banned,
            deleted_at=datetime.now(UTC) if deleted else None,
        )
        async with AsyncSession(get_engine(), expire_on_commit=False) as s:
            s.add(user)
            await s.commit()
        created.append(user.id)
        return user

    yield _make

    async with AsyncSession(get_engine()) as s:
        await s.execute(delete(User).where(User.id.in_(created)))
        await s.commit()


def auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


# ── 인증 ──


async def test_me_requires_token(client: httpx.AsyncClient) -> None:
    r = await client.get("/users/me")
    assert r.status_code == 401


async def test_me_rejects_refresh_token_as_access(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    me = await make_user()
    refresh, _jti = create_refresh_token(me.id)
    r = await client.get("/users/me", headers={"Authorization": f"Bearer {refresh}"})
    assert r.status_code == 401


async def test_banned_user_is_blocked(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    banned = await make_user(is_banned=True)
    r = await client.get("/users/me", headers=auth(banned))
    assert r.status_code == 403


# ── /users/me ──


async def test_get_me_returns_private_fields(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    me = await make_user()
    r = await client.get("/users/me", headers=auth(me))
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == str(me.id)
    assert data["email"] == me.email
    assert data["auth_provider"] == "local"
    assert data["is_admin"] is False
    # 비밀번호·삭제 시각 같은 내부 필드는 응답에 없어야
    assert not {"password_hash", "hashed_password", "deleted_at", "is_banned"} & set(data)


async def test_update_me_persists(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    me = await make_user()
    new_nick = f"renamed_{uuid.uuid4().hex[:8]}"
    r = await client.patch(
        "/users/me", headers=auth(me), json={"nickname": new_nick, "bio": "안녕", "is_public": False}
    )
    assert r.status_code == 200
    again = (await client.get("/users/me", headers=auth(me))).json()
    assert (again["nickname"], again["bio"], again["is_public"]) == (new_nick, "안녕", False)


async def test_update_me_nickname_conflict(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    me = await make_user()
    other = await make_user()
    r = await client.patch("/users/me", headers=auth(me), json={"nickname": other.nickname})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "NICKNAME_ALREADY_EXISTS"


@pytest.mark.parametrize("payload", [{"nickname": "a"}, {"nickname": "x" * 51}, {"bio": "x" * 501}])
async def test_update_me_validation(client: httpx.AsyncClient, make_user: MakeUser, payload: dict) -> None:
    me = await make_user()
    r = await client.patch("/users/me", headers=auth(me), json=payload)
    assert r.status_code == 422


async def test_delete_me_then_token_stops_working(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    me = await make_user()
    r = await client.delete("/users/me", headers=auth(me))
    assert r.status_code == 204
    # 토큰 서명은 유효하지만 사용자가 탈퇴(soft delete) → 401
    assert (await client.get("/users/me", headers=auth(me))).status_code == 401


# ── /users/{user_id} ──


async def test_get_other_user_public_view(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    me = await make_user()
    other = await make_user()
    r = await client.get(f"/users/{other.id}", headers=auth(me))
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == str(other.id)
    assert data["follow_status"] == "none"
    # 다른 사람에게는 이메일·가입 경로를 보여주지 않는다
    assert "email" not in data
    assert "auth_provider" not in data


async def test_get_private_user_returns_profile_with_flag(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    # 비공개 정책(Day 24): 프로필 메타는 보이고 is_public=false 로 프론트가 게시글 자리에 안내
    me = await make_user()
    private = await make_user(is_public=False)
    r = await client.get(f"/users/{private.id}", headers=auth(me))
    assert r.status_code == 200
    assert r.json()["is_public"] is False


async def test_get_user_requires_token(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    other = await make_user()
    assert (await client.get(f"/users/{other.id}")).status_code == 401


@pytest.mark.parametrize("target", ["missing", "deleted"])
async def test_get_user_not_found(client: httpx.AsyncClient, make_user: MakeUser, target: str) -> None:
    me = await make_user()
    user_id = uuid.uuid4() if target == "missing" else (await make_user(deleted=True)).id
    r = await client.get(f"/users/{user_id}", headers=auth(me))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "USER_NOT_FOUND"


async def test_update_other_user_forbidden(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    me = await make_user()
    other = await make_user()
    r = await client.patch(f"/users/{other.id}", headers=auth(me), json={"bio": "해킹"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "USER_UPDATE_FORBIDDEN"
    # 실제로도 안 바뀌었는지
    assert (await client.get(f"/users/{other.id}", headers=auth(me))).json()["bio"] is None


async def test_update_self_by_id(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    me = await make_user()
    r = await client.patch(f"/users/{me.id}", headers=auth(me), json={"bio": "본인"})
    assert r.status_code == 200
    assert r.json()["bio"] == "본인"


# ── /users/search ──


async def test_search_excludes_banned_deleted_and_self(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    tag = f"l2s{uuid.uuid4().hex[:6]}"  # 이 테스트만의 접두어 → 다른 데이터와 섞이지 않음
    me = await make_user(tag)
    active = await make_user(tag)
    await make_user(tag, is_banned=True)
    await make_user(tag, deleted=True)
    r = await client.get("/users/search", params={"q": tag, "page_size": 50}, headers=auth(me))
    assert r.status_code == 200
    data = r.json()
    assert [i["id"] for i in data["items"]] == [str(active.id)]
    assert data["has_next"] is False
    assert "email" not in data["items"][0]


async def test_search_validation(client: httpx.AsyncClient) -> None:
    assert (await client.get("/users/search", params={"q": "x" * 51})).status_code == 422
    assert (await client.get("/users/search", params={"page_size": 51})).status_code == 422
