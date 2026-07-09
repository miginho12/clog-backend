"""add is_banned to users

Revision ID: 7a1f9c2b4e8d
Revises: 98e3721d6e9a
Create Date: 2026-07-08 10:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a1f9c2b4e8d"
down_revision: str | None = "98e3721d6e9a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 기본 false → 기존 사용자 전부 미차단 (백필 불필요)
    op.add_column(
        "users",
        sa.Column(
            "is_banned",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="차단 여부 (True 면 로그인·활동 차단)",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "is_banned")
