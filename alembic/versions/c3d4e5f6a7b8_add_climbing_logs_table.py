"""add climbing_logs table (Day 21)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-24 10:00:00.000000

클라이밍 기록 도메인.
- ADR-022: grade_raw + grade_system + gym_name (짐별 명시)
- 카테고리 다중 태그 (text[] 배열)
- 공개 범위 (public/private)
- soft delete

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"  # Day 17 local auth
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "climbing_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grade_raw", sa.String(length=50), nullable=False),
        sa.Column(
            "grade_system",
            sa.String(length=20),
            nullable=False,
            server_default="v_scale",
        ),
        sa.Column("gym_name", sa.String(length=100), nullable=True),
        sa.Column(
            "categories",
            postgresql.ARRAY(sa.String(length=30)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "is_success",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "climbed_at",
            sa.Date(),
            nullable=False,
            server_default=sa.func.current_date(),
        ),
        sa.Column("media_type", sa.String(length=10), nullable=True),
        sa.Column("media_url", sa.String(length=500), nullable=True),
        sa.Column(
            "visibility",
            sa.String(length=10),
            nullable=False,
            server_default=sa.text("'public'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # 인덱스 (피드 쿼리 최적화)
    op.create_index(
        "ix_climbing_logs_user_id", "climbing_logs", ["user_id"]
    )
    op.create_index(
        "ix_climbing_logs_gym_name", "climbing_logs", ["gym_name"]
    )
    op.create_index(
        "ix_climbing_logs_visibility", "climbing_logs", ["visibility"]
    )
    # GIN 인덱스: 카테고리 배열 필터(@>) 최적화
    op.create_index(
        "ix_climbing_logs_categories",
        "climbing_logs",
        ["categories"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_climbing_logs_categories", table_name="climbing_logs")
    op.drop_index("ix_climbing_logs_visibility", table_name="climbing_logs")
    op.drop_index("ix_climbing_logs_gym_name", table_name="climbing_logs")
    op.drop_index("ix_climbing_logs_user_id", table_name="climbing_logs")
    op.drop_table("climbing_logs")
