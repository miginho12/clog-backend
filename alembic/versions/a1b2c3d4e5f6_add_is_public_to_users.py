"""add is_public to users

Revision ID: a1b2c3d4e5f6
Revises: c3f45b4553f4
Create Date: 2026-06-15 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "c3f45b4553f4"  # Day 9 의 첫 마이그레이션
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add is_public column to users table.

    Day 14 추가: 사용자 프로필 공개/비공개 설정.
    기본값 True (기존 사용자도 공개로).
    """
    op.add_column(
        "users",
        sa.Column(
            "is_public",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    """Remove is_public column."""
    op.drop_column("users", "is_public")
