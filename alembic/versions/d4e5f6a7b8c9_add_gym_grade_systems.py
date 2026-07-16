"""add gym_grade_systems table + seed (Day 22 구현 1)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-25 10:00:00.000000

짐별 색 난이도 순서 (ADR-041).
- 테이블 생성
- 주요 짐 6개 시드 (is_official=true)
  시드는 idempotent: 같은 gym_name 이 있으면 건너뜀

색 순서 데이터 출처: 사용자(클라이머) 직접 확인.
- 더클라임, 클라이밍파크(종로/강남 흰색이 최상급), 손상원, 알레, 피커스, 크래커
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"  # climbing_logs
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 시드 데이터: (짐 이름, [쉬운→어려운 색 순서])
SEED_GYMS = [
    ("더클라임", ["흰", "노", "주", "초", "파", "빨", "핑", "보", "회", "갈", "검"]),
    ("클라이밍파크", ["노", "핑", "파", "빨", "보", "갈", "회", "검", "흰"]),
    ("손상원클라이밍", ["흰", "노", "초", "파", "빨", "검", "회", "갈", "핑"]),
    ("알레클라이밍", ["흰", "노", "연두", "초", "파", "빨", "회", "갈", "핑"]),
    ("피커스", ["빨", "주", "노", "초", "파", "남", "보", "회", "검"]),
    ("크래커", ["빨", "주", "노", "초", "파", "남", "보", "회", "검"]),
]


def upgrade() -> None:
    op.create_table(
        "gym_grade_systems",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("gym_name", sa.String(length=100), nullable=False),
        sa.Column(
            "color_order",
            postgresql.ARRAY(sa.String(length=20)),
            nullable=False,
        ),
        sa.Column(
            "is_official",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gym_name", name="uq_gym_grade_systems_gym_name"),
    )
    op.create_index(
        "ix_gym_grade_systems_gym_name", "gym_grade_systems", ["gym_name"]
    )

    # 시드 삽입 (idempotent: ON CONFLICT DO NOTHING)
    gym_table = sa.table(
        "gym_grade_systems",
        sa.column("gym_name", sa.String),
        sa.column("color_order", postgresql.ARRAY(sa.String)),
        sa.column("is_official", sa.Boolean),
    )
    conn = op.get_bind()
    for gym_name, colors in SEED_GYMS:
        stmt = (
            postgresql.insert(gym_table)
            .values(gym_name=gym_name, color_order=colors, is_official=True)
            .on_conflict_do_nothing(index_elements=["gym_name"])
        )
        conn.execute(stmt)


def downgrade() -> None:
    op.drop_index("ix_gym_grade_systems_gym_name", table_name="gym_grade_systems")
    op.drop_table("gym_grade_systems")
