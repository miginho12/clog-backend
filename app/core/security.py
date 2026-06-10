"""JWT 인증 보안 모듈.

이 파일이 JWT 의 모든 것:
- 토큰 발급 (서명)
- 토큰 검증 (서명 + 만료)
- 페이로드 추출

RS256 (RSA 비대칭) 사용:
- private 키 → 서명 (이 서버만 가짐)
- public 키 → 검증 (다른 서비스도 가능)

설계 원칙:
- 토큰 종류 분리 (access vs refresh) → token_type 클레임으로 구분
- 짧은 access (1시간) + 긴 refresh (7일)
- 최소 페이로드 (sub, exp, iat, jti, type)
"""

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────
#  도메인 타입
# ─────────────────────────────────────────


class TokenType(StrEnum):
    """토큰 종류 — 페이로드의 'type' 클레임에 들어감."""

    ACCESS = "access"
    REFRESH = "refresh"


class TokenPair(BaseModel):
    """발급된 토큰 쌍."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"  # HTTP Authorization 헤더의 스킴
    expires_in: int  # access_token 의 수명 (초)


class TokenPayload(BaseModel):
    """디코드된 페이로드 (검증 통과 후)."""

    sub: str  # user_id (UUID 문자열)
    type: TokenType  # access 또는 refresh
    exp: datetime
    iat: datetime
    jti: str  # 토큰 고유 ID (refresh token 추적에 사용)


# ─────────────────────────────────────────
#  예외
# ─────────────────────────────────────────


class TokenError(Exception):
    """JWT 관련 베이스 예외."""

    pass


class InvalidToken(TokenError):
    """변조됨, 서명 불일치, 형식 오류 등."""

    pass


class TokenExpired(TokenError):
    """만료된 토큰."""

    pass


class WrongTokenType(TokenError):
    """access 가 와야 할 곳에 refresh 가 옴 (또는 반대)."""

    def __init__(self, expected: TokenType, actual: TokenType):
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected {expected}, got {actual}")


# ─────────────────────────────────────────
#  토큰 발급
# ─────────────────────────────────────────


def _create_token(
    *,
    subject: str,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """JWT 토큰 생성 (내부 함수).

    Returns:
        (token, jti): 발급된 토큰과 jti (refresh 추적에 사용)
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + expires_delta
    jti = str(uuid.uuid4())

    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type.value,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "iss": settings.jwt_issuer,
        "jti": jti,
    }
    if extra_claims:
        payload.update(extra_claims)

    token = jwt.encode(
        payload,
        settings.get_jwt_private_key(),
        algorithm=settings.jwt_algorithm,
    )
    return token, jti


def create_access_token(user_id: str | uuid.UUID) -> str:
    """Access token 발급 (1시간)."""
    settings = get_settings()
    token, _ = _create_token(
        subject=str(user_id),
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes),
    )
    return token


def create_refresh_token(user_id: str | uuid.UUID) -> tuple[str, str]:
    """Refresh token 발급 (7일).

    Returns:
        (token, jti): jti 는 Repository 에 저장해서 무효화 가능하게 함.
    """
    settings = get_settings()
    return _create_token(
        subject=str(user_id),
        token_type=TokenType.REFRESH,
        expires_delta=timedelta(days=settings.jwt_refresh_token_expire_days),
    )


def create_token_pair(user_id: str | uuid.UUID) -> tuple[TokenPair, str]:
    """Access + Refresh token 동시 발급.

    Returns:
        (TokenPair, refresh_jti):
          - TokenPair: 클라이언트에 응답할 객체
          - refresh_jti: Repository 에 저장할 ID
    """
    settings = get_settings()
    access = create_access_token(user_id)
    refresh, refresh_jti = create_refresh_token(user_id)

    pair = TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )
    return pair, refresh_jti


# ─────────────────────────────────────────
#  토큰 검증 / 디코드
# ─────────────────────────────────────────


def decode_token(token: str, expected_type: TokenType) -> TokenPayload:
    """토큰 디코드 + 검증.

    Args:
        token: JWT 문자열
        expected_type: 이 토큰이 access 인지 refresh 인지 명시

    Raises:
        TokenExpired: 만료됨
        InvalidToken: 서명 불일치, 형식 오류 등
        WrongTokenType: 토큰 종류 불일치

    Returns:
        검증 통과한 페이로드
    """
    settings = get_settings()

    try:
        # python-jose 가 서명 + 만료 자동 검증
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.get_jwt_public_key(),
            algorithms=[settings.jwt_algorithm],
            # 발급자 검증 (선택, 안전성 ↑)
            issuer=settings.jwt_issuer,
        )
    except jwt.ExpiredSignatureError as e:
        raise TokenExpired("token has expired") from e
    except JWTError as e:
        # 서명 불일치, 형식 오류, issuer 불일치 등
        logger.warning("invalid_token", error=str(e))
        raise InvalidToken(f"invalid token: {e}") from e

    # 토큰 종류 검증
    actual_type_raw = payload.get("type")
    if actual_type_raw is None:
        raise InvalidToken("token missing 'type' claim")

    try:
        actual_type = TokenType(actual_type_raw)
    except ValueError as e:
        raise InvalidToken(f"unknown token type: {actual_type_raw}") from e

    if actual_type != expected_type:
        raise WrongTokenType(expected=expected_type, actual=actual_type)

    # Pydantic 객체로 변환 (타입 안전)
    return TokenPayload(
        sub=payload["sub"],
        type=actual_type,
        exp=datetime.fromtimestamp(payload["exp"], tz=UTC),
        iat=datetime.fromtimestamp(payload["iat"], tz=UTC),
        jti=payload["jti"],
    )


def decode_access_token(token: str) -> TokenPayload:
    """Access token 전용 디코드."""
    return decode_token(token, expected_type=TokenType.ACCESS)


def decode_refresh_token(token: str) -> TokenPayload:
    """Refresh token 전용 디코드."""
    return decode_token(token, expected_type=TokenType.REFRESH)
