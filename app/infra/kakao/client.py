"""카카오 OAuth API 클라이언트.

카카오 외부 API 와 직접 HTTP 통신하는 인프라 레이어.
Service 는 이걸 사용 — Service 가 카카오의 HTTP 세부사항 안 알게.

[카카오 API 흐름]
1. authorize URL 생성 (사용자 리다이렉트)
2. POST /oauth/token (code → access_token)
3. GET /v2/user/me (access_token → 사용자 정보)
"""

from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.auth.exceptions import (
    KakaoAPIError,
    KakaoTokenExchangeFailed,
    KakaoUserInfoFailed,
)

logger = get_logger(__name__)


class KakaoOAuthClient:
    """카카오 OAuth API 클라이언트.

    인스턴스화는 dependencies 에서 (httpx.AsyncClient lifespan 관리).
    """

    def __init__(self, http_client: httpx.AsyncClient):
        self.http = http_client
        self.settings = get_settings()

    # ── 1. Authorize URL 생성 ──

    def build_authorize_url(self, state: str) -> str:
        """카카오 로그인 페이지 URL 생성.

        사용자 브라우저가 이 URL 로 리다이렉트되어 카카오 로그인.

        Args:
            state: CSRF 방어용 랜덤 값 (Redis 에 미리 저장)

        Returns:
            https://kauth.kakao.com/oauth/authorize?... 형태의 URL
        """
        params = {
            "client_id": self.settings.kakao_client_id,
            "redirect_uri": self.settings.kakao_redirect_uri,
            "response_type": "code",
            "state": state,
            # 추가 동의 항목 (카카오 콘솔에 설정한 것 중 강제 동의)
            # scope 생략 시 카카오 콘솔 설정 따름 → 우리는 콘솔에 설정함
        }
        return f"{self.settings.kakao_authorize_url}?{urlencode(params)}"

    # ── 2. Code → Access Token 교환 ──

    async def exchange_code_for_token(self, code: str) -> dict[str, Any]:
        """카카오 인증 코드를 access token 으로 교환.

        Args:
            code: 카카오가 callback URL 에 붙여준 코드

        Returns:
            {
                "access_token": "...",
                "token_type": "bearer",
                "refresh_token": "...",
                "expires_in": 21599,
                "scope": "...",
                "refresh_token_expires_in": 5183999
            }

        Raises:
            KakaoTokenExchangeFailed: 코드 교환 실패
        """
        data = {
            "grant_type": "authorization_code",
            "client_id": self.settings.kakao_client_id,
            "redirect_uri": self.settings.kakao_redirect_uri,
            "code": code,
        }
        # Client Secret 등록한 경우 추가
        if self.settings.kakao_client_secret:
            data["client_secret"] = self.settings.kakao_client_secret

        try:
            response = await self.http.post(
                self.settings.kakao_token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as e:
            logger.error("kakao_token_request_failed", error=str(e))
            raise KakaoAPIError(f"failed to call kakao token API: {e}") from e

        if response.status_code != 200:
            # 카카오 에러 응답 예: {"error": "invalid_grant", "error_description": "..."}
            error_data = self._safe_json(response)
            logger.warning(
                "kakao_token_exchange_failed",
                status=response.status_code,
                error=error_data.get("error"),
                description=error_data.get("error_description"),
            )
            raise KakaoTokenExchangeFailed(
                error=error_data.get("error", "unknown"),
                description=error_data.get("error_description", ""),
            )

        token_data: dict[str, Any] = response.json()
        logger.info(
            "kakao_token_received",
            has_refresh=("refresh_token" in token_data),
            expires_in=token_data.get("expires_in"),
        )
        return token_data

    # ── 3. 사용자 정보 조회 ──

    async def fetch_user_info(self, kakao_access_token: str) -> dict[str, Any]:
        """카카오 access token 으로 사용자 정보 조회.

        Args:
            kakao_access_token: exchange_code_for_token 의 결과

        Returns:
            카카오 사용자 정보 (구조는 동의 항목에 따라 다름):
            {
                "id": 12345678,
                "connected_at": "2024-...",
                "properties": {
                    "nickname": "홍길동",
                    "profile_image": "...",
                    "thumbnail_image": "..."
                },
                "kakao_account": {
                    "profile_nickname_needs_agreement": false,
                    "profile_image_needs_agreement": false,
                    "profile": {
                        "nickname": "홍길동",
                        "thumbnail_image_url": "...",
                        "profile_image_url": "...",
                        "is_default_image": false
                    },
                    "has_email": true,
                    "email_needs_agreement": false,
                    "is_email_valid": true,
                    "is_email_verified": true,
                    "email": "user@kakao.com"
                }
            }

        Raises:
            KakaoUserInfoFailed: 사용자 정보 조회 실패
        """
        try:
            response = await self.http.get(
                self.settings.kakao_user_info_url,
                headers={
                    "Authorization": f"Bearer {kakao_access_token}",
                    "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
                },
            )
        except httpx.HTTPError as e:
            logger.error("kakao_user_info_request_failed", error=str(e))
            raise KakaoAPIError(f"failed to call kakao user API: {e}") from e

        if response.status_code != 200:
            error_data = self._safe_json(response)
            logger.warning(
                "kakao_user_info_failed",
                status=response.status_code,
                error=error_data.get("msg"),
                code=error_data.get("code"),
            )
            raise KakaoUserInfoFailed(
                error=error_data.get("msg", "unknown"),
                code=error_data.get("code", 0),
            )

        user_data: dict[str, Any] = response.json()
        logger.info("kakao_user_info_received", kakao_id=user_data.get("id"))
        return user_data

    # ── 헬퍼 ──

    @staticmethod
    def _safe_json(response: httpx.Response) -> dict[str, Any]:
        """응답이 JSON 아니어도 빈 dict 반환 (예외 안전)."""
        try:
            return response.json()  # type: ignore[no-any-return]
        except Exception:
            return {}
