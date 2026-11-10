"""앱 카카오 로그인(일회용 코드 + PKCE) 검증 (감사 M14, rollback — DB 변경 없음).

앱은 시스템 브라우저로 카카오 로그인 → 백엔드 콜백이 커스텀 스킴으로 일회용 코드만
돌려줌 → 앱이 code + code_verifier 로 JWT 교환. 카카오 API 는 가짜 클라이언트로 대체.

검증 항목:
[서비스]
- 웹 흐름은 그대로: 콜백이 JWT 발급, 일회용 코드 없음, 옛 state 값("1")도 web 으로 처리
- 앱 흐름: 콜백이 JWT 대신 코드 발급(refresh 저장 안 함), 교환하면 JWT + refresh 저장
- 코드 재사용 불가 / verifier 불일치는 거부되고 코드도 폐기 / 형식 틀린 verifier 거부
- 만료(TTL) 코드 거부 / 코드 발급 뒤 차단된 계정은 AccountBanned
- challenge 없는 앱 state 는 OAuthStateInvalid
[HTTP]
- /auth/kakao/login: client=app 인데 challenge 없음·형식 오류 → 422, 정상 → 302 카카오
- /auth/kakao/callback: 앱이면 com.climblog.app://auth/callback?code=… (토큰 없음), 웹은 #fragment
- /auth/kakao/app/exchange: 성공 200 응답 형태, 실패 401 OAUTH_APP_CODE_INVALID

실행:
    uv run python -m scripts.verify_kakao_app_login
"""

import asyncio
import secrets
import sys
import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.infra.db.models  # noqa: F401
from app.core.security import TokenPair, decode_token
from app.domain.auth.app_code_repository import AppLoginCodeRepository, _code_key
from app.domain.auth.exceptions import AccountBanned, OAuthAppCodeInvalid, OAuthStateInvalid
from app.domain.auth.kakao_service import KakaoCallbackResult, KakaoOAuthService, pkce_s256
from app.domain.auth.repository import RedisRefreshTokenRepository
from app.domain.auth.state_repository import OAuthState, OAuthStateRepository, _state_key
from app.domain.users.models import User
from app.domain.users.repository import UserRepository
from app.infra.db.engine import close_engine, init_engine
from app.infra.redis import close_redis, init_redis

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
_results: list[bool] = []


def check(name, ok, extra=""):
    _results.append(bool(ok))
    print(f"  [{(GREEN + 'PASS' if ok else RED + 'FAIL') + RESET}] {name}" + (f"  ({extra})" if extra else ""))


async def expect_raises(coro, exc_type, name):
    try:
        await coro
        check(name, False, "예외 없음")
    except exc_type:
        check(name, True)
    except Exception as e:  # noqa: BLE001
        check(name, False, f"예상과 다른 예외: {type(e).__name__}")


class FakeKakao:
    """카카오 API 대체 — 콜백 code 로 kakao_id 를 고른다."""

    def __init__(self, kakao_id: int, email: str):
        self.kakao_id, self.email = kakao_id, email

    def build_authorize_url(self, state: str) -> str:
        return f"https://kauth.kakao.com/oauth/authorize?state={state}"

    async def exchange_code_for_token(self, code: str):
        return {"access_token": "kakao-at", "token_type": "bearer", "expires_in": 3600}

    async def fetch_user_info(self, kakao_access_token: str):
        return {
            "id": self.kakao_id,
            "kakao_account": {
                "has_email": True, "is_email_valid": True, "is_email_verified": True,
                "email": self.email, "profile": {"nickname": "앱로그인"},
            },
        }


def new_verifier() -> str:
    return secrets.token_urlsafe(48)  # 64자, RFC 7636 허용 문자


async def run(session: AsyncSession, redis) -> None:
    uid = uuid.uuid4().hex[:8]
    kakao_id = int(uuid.uuid4().int % 10**12)
    user = User(
        email=f"app_{uid}@clog.test", nickname=f"app_{uid}", auth_provider="kakao",
        auth_provider_id=str(kakao_id), email_verified=True,
    )
    session.add(user)
    await session.flush()

    state_repo = OAuthStateRepository(redis)
    code_repo = AppLoginCodeRepository(redis)
    refresh_repo = RedisRefreshTokenRepository(redis)
    svc = KakaoOAuthService(
        session=session, kakao_client=FakeKakao(kakao_id, user.email), state_repo=state_repo,
        refresh_repo=refresh_repo, user_repo=UserRepository(session), app_code_repo=code_repo,
    )

    async def refresh_saved(pair: TokenPair) -> bool:
        jti = decode_token(pair.refresh_token, expected_type="refresh").jti
        return await refresh_repo.get(jti) is not None

    print("\n[웹 흐름 — 변경 없음]")
    _, state = await svc.initiate_login()
    r = await svc.handle_callback(code="c", state=state)
    check("웹 콜백: JWT 발급 + 일회용 코드 없음", r.pair is not None and r.app_code is None)
    check("웹 콜백: refresh 저장됨", await refresh_saved(r.pair))
    check("웹 콜백: 기존 사용자로 로그인", r.user.id == user.id and not r.is_new_user)
    await redis.set(_state_key("legacy-" + uid), "1", ex=60)
    r = await svc.handle_callback(code="c", state="legacy-" + uid)
    check("옛 state 값 \"1\" → 웹으로 처리 (배포 중 호환)", r.pair is not None)
    await expect_raises(svc.handle_callback(code="c", state=state), OAuthStateInvalid, "state 재사용 → OAuthStateInvalid")

    print("\n[앱 흐름 — 코드 발급·교환]")
    verifier = new_verifier()
    _, state = await svc.initiate_login(OAuthState(client="app", code_challenge=pkce_s256(verifier)))
    r = await svc.handle_callback(code="c", state=state)
    check("앱 콜백: JWT 대신 일회용 코드", r.pair is None and bool(r.app_code))
    ttl = await redis.ttl(_code_key(r.app_code))
    check("코드 TTL ≤ 60초", 0 < ttl <= 60, f"ttl={ttl}")
    pair, u, is_new = await svc.exchange_app_code(r.app_code, verifier)
    check("교환: JWT 발급 + 같은 사용자", bool(pair.access_token) and u.id == user.id and not is_new)
    check("교환: refresh 저장됨 (회전·로그아웃 대상)", await refresh_saved(pair))
    await expect_raises(svc.exchange_app_code(r.app_code, verifier), OAuthAppCodeInvalid, "같은 코드 재사용 → 거부")

    print("\n[앱 흐름 — 가로채기·오용 방어]")
    verifier = new_verifier()
    _, state = await svc.initiate_login(OAuthState(client="app", code_challenge=pkce_s256(verifier)))
    r = await svc.handle_callback(code="c", state=state)
    await expect_raises(svc.exchange_app_code(r.app_code, new_verifier()), OAuthAppCodeInvalid, "다른 verifier(가로챈 쪽) → 거부")
    await expect_raises(svc.exchange_app_code(r.app_code, verifier), OAuthAppCodeInvalid, "틀린 시도 뒤엔 원래 verifier 로도 거부 (코드 폐기)")

    verifier = new_verifier()
    code = await code_repo.create(str(user.id), False, pkce_s256(verifier))
    await expect_raises(svc.exchange_app_code(code, "short"), OAuthAppCodeInvalid, "형식 틀린 verifier → 거부")

    code = await code_repo.create(str(user.id), False, pkce_s256(verifier))
    await redis.pexpire(_code_key(code), 1)
    await asyncio.sleep(0.05)
    await expect_raises(svc.exchange_app_code(code, verifier), OAuthAppCodeInvalid, "만료된 코드 → 거부")
    await expect_raises(svc.exchange_app_code("no-such-code", verifier), OAuthAppCodeInvalid, "없는 코드 → 거부")

    code = await code_repo.create(str(user.id), False, pkce_s256(verifier))
    user.is_banned = True
    await session.flush()
    await expect_raises(svc.exchange_app_code(code, verifier), AccountBanned, "코드 발급 뒤 차단된 계정 → AccountBanned")
    user.is_banned = False
    await session.flush()

    await redis.set(_state_key("nochal-" + uid), '{"client": "app", "code_challenge": null}', ex=60)
    await expect_raises(svc.handle_callback(code="c", state="nochal-" + uid), OAuthStateInvalid, "challenge 없는 앱 state → OAuthStateInvalid")

    print("\n[RFC 7636 부록 B 테스트 벡터]")
    check("pkce_s256 = RFC 예시값", pkce_s256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")


def run_http() -> None:
    from app.core.rate_limit import limiter
    from app.domain.auth.dependencies import get_kakao_oauth_service
    from app.main import app

    # 응답 형태 검증이 목적 — 반복 실행 시 로컬 Redis 의 rate limit(로그인 10/분)에 걸리지 않게
    limiter.enabled = False

    print("\n[HTTP — 로그인 시작]")
    with TestClient(app) as c:
        ch = pkce_s256(new_verifier())
        r = c.get("/auth/kakao/login", params={"client": "app"}, follow_redirects=False)
        check("client=app, challenge 없음 → 422", r.status_code == 422, str(r.status_code))
        r = c.get("/auth/kakao/login", params={"client": "app", "code_challenge": "short"}, follow_redirects=False)
        check("challenge 형식 오류 → 422", r.status_code == 422, str(r.status_code))
        r = c.get("/auth/kakao/login", params={"client": "app", "code_challenge": ch, "code_challenge_method": "plain"}, follow_redirects=False)
        check("method=plain → 422 (S256 만)", r.status_code == 422, str(r.status_code))
        r = c.get("/auth/kakao/login", params={"client": "app", "code_challenge": ch}, follow_redirects=False)
        loc = r.headers.get("location", "")
        check("앱 정상 → 302 카카오", r.status_code == 302 and loc.startswith("https://kauth.kakao.com/"), str(r.status_code))
        r = c.get("/auth/kakao/login", follow_redirects=False)
        check("웹(파라미터 없음) → 302 카카오 (기존 그대로)", r.status_code == 302, str(r.status_code))

        print("\n[HTTP — 콜백·교환 (서비스 대역)]")
        now = datetime.now(UTC)
        fake_user = User(
            id=uuid.uuid4(), email="h@example.com", nickname="h", auth_provider="kakao",
            email_verified=True, is_public=True, is_admin=False, created_at=now, updated_at=now,
        )

        class Stub:
            def __init__(self, result=None, exc=None):
                self.result, self.exc = result, exc

            async def handle_callback(self, code, state):
                return self.result

            async def exchange_app_code(self, code, code_verifier):
                if self.exc:
                    raise self.exc
                return TokenPair(access_token="at", refresh_token="rt", expires_in=900), fake_user, True

        def use(stub):
            app.dependency_overrides[get_kakao_oauth_service] = lambda: stub

        try:
            use(Stub(KakaoCallbackResult(user=fake_user, is_new_user=False, app_code="abc_DEF-123")))
            r = c.get("/auth/kakao/callback", params={"code": "k", "state": "s"}, follow_redirects=False)
            loc = r.headers.get("location", "")
            u = urlparse(loc)
            check("앱 콜백 → com.climblog.app://auth/callback?code=…", r.status_code == 302 and f"{u.scheme}://{u.netloc}{u.path}" == "com.climblog.app://auth/callback" and parse_qs(u.query) == {"code": ["abc_DEF-123"]}, loc[:60])
            check("앱 콜백 URL 에 토큰 없음", "access_token" not in loc and "refresh_token" not in loc)

            use(Stub(KakaoCallbackResult(user=fake_user, is_new_user=False, pair=TokenPair(access_token="at", refresh_token="rt", expires_in=900))))
            r = c.get("/auth/kakao/callback", params={"code": "k", "state": "s"}, follow_redirects=False)
            loc = r.headers.get("location", "")
            check("웹 콜백 → {frontend}/auth/callback#access_token=… (기존 그대로)", r.status_code == 302 and "/auth/callback#access_token=at&refresh_token=rt" in loc)

            use(Stub())
            r = c.post("/auth/kakao/app/exchange", json={"code": "abc", "code_verifier": new_verifier()})
            body = r.json()
            check("교환 성공 → 200 + 토큰·user·is_new_user", r.status_code == 200 and body.get("access_token") == "at" and body.get("is_new_user") is True and "user" in body, str(r.status_code))

            use(Stub(exc=OAuthAppCodeInvalid("x")))
            r = c.post("/auth/kakao/app/exchange", json={"code": "abc", "code_verifier": new_verifier()})
            check("교환 실패 → 401 OAUTH_APP_CODE_INVALID", r.status_code == 401 and "OAUTH_APP_CODE_INVALID" in r.text, str(r.status_code))
            r = c.post("/auth/kakao/app/exchange", json={"code": "abc", "code_verifier": "short"})
            check("verifier 길이 부족 → 422", r.status_code == 422, str(r.status_code))
        finally:
            app.dependency_overrides.pop(get_kakao_oauth_service, None)


async def main():
    engine = init_engine()
    redis = init_redis()
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint",
                               expire_on_commit=False, autoflush=False)
        try:
            await run(session, redis)
        finally:
            await session.close()
            await outer.rollback()
    await close_engine()
    await close_redis()


if __name__ == "__main__":
    asyncio.run(main())
    run_http()
    total, passed = len(_results), sum(_results)
    print(f"\n{'─' * 40}")
    if passed == total:
        print(f"{GREEN}ALL PASS{RESET}  ({passed}/{total})  — DB 변경 없음(rollback)")
        sys.exit(0)
    print(f"{RED}FAILED{RESET}  ({passed}/{total})")
    sys.exit(1)
