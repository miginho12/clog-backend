"""Rate limiting 인프라.

slowapi (https://github.com/laurentS/slowapi) 를 사용:
- FastAPI 친화 (Limiter 데코레이터)
- Redis 백엔드 (멀티 Pod 환경 일관)
- IP 기반 + 인증 기반 키 가능

[정책 (엔드포인트별 차등)]
- /health/*           : 1000/minute  (사실상 무제한)
- /auth/kakao/login   : 10/minute    (CSRF + 부하 보호)
- /auth/kakao/callback: 30/minute    (정상 흐름 여유)
- /auth/refresh       : 30/minute
- /auth/logout        : 60/minute
- /users/me           : 100/minute   (인증된 사용자)
- /users/{user_id}    : 60/minute    (조회)
- 기본                : 100/minute   (catch-all)

[키 함수]
- 인증 안된 요청: IP 기반
- 인증된 요청: user_id 기반 (IP 우회 방지)
"""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _key_func(request: Request) -> str:
    """Rate limit 키 결정.

    인증된 사용자: user_id 기반 (request.state.user_id 가 있으면)
    그 외: IP 기반

    IP 기반의 경우 X-Forwarded-For 헤더 신뢰 X (스푸핑 가능).
    Tailscale Funnel 환경에선 실제 IP 가 라즈베리파이의 tailscaled.
    """
    # 인증된 사용자면 user_id 사용
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"

    # 그 외 IP
    return f"ip:{get_remote_address(request)}"


def _get_storage_uri() -> str:
    """Redis URI (slowapi 가 쓸 백엔드).

    slowapi 의 형식: redis://[:password]@host:port/db
    """
    settings = get_settings()
    return settings.redis_url


# Limiter 인스턴스
# slowapi 가 자동으로 RateLimitExceeded 예외 발생
# 우리는 main.py 에서 예외 핸들러 등록
limiter = Limiter(
    key_func=_key_func,
    storage_uri=_get_storage_uri(),
    # 기본 정책 (각 엔드포인트 데코레이터 없을 때)
    default_limits=["100/minute"],
    # 헤더에 limit 정보 포함 (X-RateLimit-*)
    headers_enabled=False,
)


# 정책 상수 (가독성)
class RateLimits:
    """엔드포인트별 rate limit 정책."""

    # 헬스체크 (사실상 무제한)
    HEALTH = "1000/minute"

    # OAuth (CSRF + 부하 보호)
    KAKAO_LOGIN = "10/minute"
    KAKAO_CALLBACK = "30/minute"

    # 비밀번호 찾기 (메일 스팸/코드 무차별 대입 방지)
    PASSWORD_RESET_REQUEST = "5/minute"
    PASSWORD_RESET_VERIFY = "10/minute"

    # JWT 관련
    REFRESH = "30/minute"
    LOGOUT = "60/minute"
    LOGOUT_ALL = "10/minute"  # 전체 디바이스 로그아웃은 드물어야

    # User
    USERS_ME = "100/minute"
    USERS_DETAIL = "60/minute"
    USERS_SEARCH = "60/minute"
    USERS_UPDATE = "30/minute"

    # 기본 (catch-all)
    DEFAULT = "100/minute"
