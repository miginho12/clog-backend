"""kakao profile image urls to https

Revision ID: 6a4fc0e704e9
Revises: 8e9240acd321
Create Date: 2026-11-12 13:12:29.632902+00:00

데이터 마이그레이션(스키마 변경 없음). 2026-07-30 이전에 카카오로 가입한 사용자의
profile_image_url 이 http://*.kakaocdn.net 로 남아 있다 — 프로필 이미지는 가입 때만 저장하고,
카카오 요청에 secure_resource 를 주지 않아 http 로 받았다(이후 get_profile_image() 가 스킴 변환).
웹 CSP 는 브라우저가 https 로 올리기 전 주소로 검사해 강제 CSP(M3)에서 막히고, 앱 WebView 는
mixed content 로 막는다. 같은 자산이 https 로 그대로 서빙됨을 확인(dev 2건 http·https 둘 다 200 image/jpeg).

- 대상: 카카오 CDN 호스트(*.kakaocdn.net)의 http 주소만. 다른 주소·이미 https 인 행은 그대로.
- 여러 번 돌려도 결과가 같다(http 행이 없으면 0행 갱신).
- updated_at 은 건드리지 않는다(사용자 수정이 아님).
- downgrade 는 아무것도 하지 않는다 — http 로 되돌릴 이유가 없고, 원래 http 였던 행을 구분할 수 없다.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6a4fc0e704e9"
down_revision: str | None = "8e9240acd321"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 호스트 부분만 카카오 CDN 으로 한정(경로에 kakaocdn.net 이 들어간 다른 주소는 제외)
KAKAO_HTTP = r"^http://([A-Za-z0-9-]+\.)*kakaocdn\.net/"
# scripts/verify_kakao_profile_https.py 가 같은 SQL 을 savepoint 안에서 검증한다
SQL = (
    "UPDATE users SET profile_image_url = 'https://' || substr(profile_image_url, 8) "
    "WHERE profile_image_url ~ :pattern"
)


def upgrade() -> None:
    """http://*.kakaocdn.net 프로필 주소를 https 로."""
    result = op.get_bind().execute(sa.text(SQL), {"pattern": KAKAO_HTTP})
    print(f"kakao profile image urls → https: {result.rowcount} rows")


def downgrade() -> None:
    """되돌리지 않는다(위 설명)."""
