"""JWT 보안 동작 검증 — python-jose → PyJWT 이전(ADR-075) 회귀 방지.

라이브러리 기본값을 믿지 않고 공격 토큰을 직접 만들어 넣는다:
만료 · 서명 변조 · 다른 키 · alg=none · HS256 알고리즘 혼동(공개키를 HMAC 비밀로) ·
issuer 불일치 · 필수 클레임 누락 · 토큰 종류 불일치/누락/미상.

필수 클레임 누락은 이전(python-jose, require 옵션 없음) 코드에서 decode 는 통과하고
payload["jti"] 에서 KeyError → 500 이 나던 경로다. 지금은 InvalidToken → 401 이어야 한다.

DB·Redis 를 쓰지 않는다 (잘못된 토큰은 get_current_user 의 User 조회 전에 거부됨).
실행:
    uv run python -m scripts.verify_jwt_security
"""

import asyncio
import base64
import hashlib
import hmac
import json
import sys
import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.api.dependencies import get_current_user
from app.core.config import get_settings
from app.core.security import (
    InvalidToken,
    TokenExpired,
    TokenType,
    WrongTokenType,
    create_token_pair,
    decode_access_token,
    decode_refresh_token,
)

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

_results: list[bool] = []


def check(name: str, ok: bool) -> None:
    _results.append(ok)
    print(f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}", name)


def expect(fn: Any, exc_type: type[Exception], name: str) -> None:
    try:
        fn()
        check(name, False)
    except exc_type:
        check(name, True)
    except Exception as e:  # 다른 예외(예: KeyError → 500)도 실패로 본다
        check(f"{name} (실제: {type(e).__name__}: {e})", False)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def base_claims(**over: Any) -> dict[str, Any]:
    s = get_settings()
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": str(uuid.uuid4()),
        "type": TokenType.ACCESS.value,
        "iat": now,
        "exp": now + 300,
        "iss": s.jwt_issuer,
        "jti": str(uuid.uuid4()),
    }
    claims.update(over)
    return {k: v for k, v in claims.items() if v is not None}


def sign_rs256(claims: dict[str, Any]) -> str:
    return jwt.encode(claims, get_settings().get_jwt_private_key(), algorithm="RS256")


def hand_made(header: dict[str, Any], claims: dict[str, Any], signature: bytes) -> str:
    h = b64url(json.dumps(header, separators=(",", ":")).encode())
    p = b64url(json.dumps(claims, separators=(",", ":")).encode())
    return f"{h}.{p}.{b64url(signature)}"


async def main() -> int:
    settings = get_settings()
    pub_pem = settings.get_jwt_public_key()

    # ── 정상 경로 ─────────────────────────────────────────
    user_id = str(uuid.uuid4())
    pair, refresh_jti = create_token_pair(user_id)
    p = decode_access_token(pair.access_token)
    check("정상 access 토큰 → sub·type·jti 복원", p.sub == user_id and p.type == TokenType.ACCESS)
    r = decode_refresh_token(pair.refresh_token)
    check("정상 refresh 토큰 → jti 가 저장용 jti 와 일치", r.jti == refresh_jti)
    check(
        "헤더 alg=RS256 (설정값)",
        jwt.get_unverified_header(pair.access_token)["alg"] == settings.jwt_algorithm == "RS256",
    )

    # ── 토큰 종류 ─────────────────────────────────────────
    expect(lambda: decode_access_token(pair.refresh_token), WrongTokenType, "refresh 를 access 자리에 → WrongTokenType")
    expect(lambda: decode_refresh_token(pair.access_token), WrongTokenType, "access 를 refresh 자리에 → WrongTokenType")
    expect(lambda: decode_access_token(sign_rs256(base_claims(type=None))), InvalidToken, "type 클레임 없음 → InvalidToken")
    expect(lambda: decode_access_token(sign_rs256(base_claims(type="admin"))), InvalidToken, "알 수 없는 type → InvalidToken")

    # ── 시간 ──────────────────────────────────────────────
    now = int(time.time())
    expect(lambda: decode_access_token(sign_rs256(base_claims(iat=now - 120, exp=now - 60))), TokenExpired, "만료 토큰 → TokenExpired")

    # ── 서명 ──────────────────────────────────────────────
    good = sign_rs256(base_claims())
    h, pl, sig = good.split(".")
    forged_payload = b64url(json.dumps(base_claims(sub=str(uuid.uuid4())), separators=(",", ":")).encode())
    expect(lambda: decode_access_token(f"{h}.{forged_payload}.{sig}"), InvalidToken, "페이로드 바꿔치기(서명 유지) → InvalidToken")
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    expect(lambda: decode_access_token(jwt.encode(base_claims(), other_key, algorithm="RS256")), InvalidToken, "다른 RSA 키로 서명 → InvalidToken")
    expect(lambda: decode_access_token(hand_made({"alg": "none", "typ": "JWT"}, base_claims(), b"")), InvalidToken, "alg=none (서명 없음) → InvalidToken")
    # 알고리즘 혼동: 서버 공개키(공개 정보)를 HMAC 비밀로 써서 HS256 서명 — CVE-2026-85394 유형
    confused_header = {"alg": "HS256", "typ": "JWT"}
    hp = ".".join(hand_made(confused_header, base_claims(), b"").split(".")[:2])
    mac = hmac.new(pub_pem.encode(), hp.encode(), hashlib.sha256).digest()
    expect(lambda: decode_access_token(f"{hp}.{b64url(mac)}"), InvalidToken, "HS256 알고리즘 혼동(공개키를 HMAC 비밀로) → InvalidToken")

    # ── 발급자·필수 클레임 ────────────────────────────────
    expect(lambda: decode_access_token(sign_rs256(base_claims(iss="evil-issuer"))), InvalidToken, "issuer 불일치 → InvalidToken")
    for claim in ("jti", "sub", "exp", "iat", "iss"):
        expect(
            lambda c=claim: decode_access_token(sign_rs256(base_claims(**{c: None}))),
            InvalidToken,
            f"{claim} 클레임 누락(서명 정상) → InvalidToken (이전: KeyError→500 경로)",
        )

    # ── HTTP 경계: get_current_user 가 401 로 바꾸는지 ────
    req = Request(scope={"type": "http", "headers": [], "method": "GET", "path": "/"})
    for name, token in (
        ("jti 누락 토큰", sign_rs256(base_claims(jti=None))),
        ("HS256 혼동 토큰", f"{hp}.{b64url(mac)}"),
        ("refresh 토큰", pair.refresh_token),
    ):
        try:
            await get_current_user(
                req,
                credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
                user_repo=None,  # type: ignore[arg-type]  # 토큰 단계에서 거부돼 조회까지 가지 않음
            )
            check(f"get_current_user({name}) → 401", False)
        except HTTPException as e:
            check(f"get_current_user({name}) → 401", e.status_code == 401)
        except Exception as e:
            check(f"get_current_user({name}) → 401 (실제: {type(e).__name__})", False)

    passed, total = sum(_results), len(_results)
    if passed == total:
        print(f"\n{GREEN}ALL PASS{RESET}  ({passed}/{total})")
        return 0
    print(f"\n{RED}FAILED{RESET}  ({passed}/{total})")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
