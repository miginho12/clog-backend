"""add local auth fields to users (Day 17)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-22 10:00:00.000000

Day 17: 자체 회원가입(local OAuth) 지원.
- password_hash: bcrypt 해시 (local 가입자만, nullable)
- is_admin: 관리자 여부 (admin 가드용)
- auth_provider_id: nullable 화 (local 가입자는 OAuth ID 없음)

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"  # Day 14 의 is_public
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """자체 회원가입 지원 컬럼 추가.

    1. password_hash (nullable) — local 가입자만 값 존재
    2. is_admin (NOT NULL, default false) — admin 가드용
    3. auth_provider_id 를 nullable 로 변경 — local 가입자는 OAuth ID 없음
    """
    # 1. password_hash 추가 (nullable — OAuth 가입자는 NULL)
    op.add_column(
        "users",
        sa.Column(
            "password_hash",
            sa.String(length=255),
            nullable=True,
            comment="bcrypt 해시 (local 가입자만, OAuth 가입자는 NULL)",
        ),
    )

    # 2. is_admin 추가 (기본 false — 기존 사용자 모두 일반 사용자)
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="관리자 여부 (admin 가드용)",
        ),
    )

    # 3. auth_provider_id nullable 화
    #    기존 카카오 가입자는 값이 있으므로 영향 없음.
    #    앞으로 local 가입자는 NULL 로 들어감.
    op.alter_column(
        "users",
        "auth_provider_id",
        existing_type=sa.String(length=255),
        nullable=True,
        comment="프로바이더의 사용자 ID (local 가입자는 NULL)",
    )


def downgrade() -> None:
    """롤백.

    주의: local 가입자가 이미 존재하면 auth_provider_id NOT NULL 복원이
    실패할 수 있음 (NULL 값 존재). 그 경우 수동 정리 필요.
    """
    op.alter_column(
        "users",
        "auth_provider_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.drop_column("users", "is_admin")
    op.drop_column("users", "password_hash")
