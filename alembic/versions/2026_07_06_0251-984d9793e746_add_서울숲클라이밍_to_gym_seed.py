"""add 서울숲클라이밍 to gym seed

Revision ID: 984d9793e746
Revises: 57264df634d9
Create Date: 2026-07-06 02:51:32.057747+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '984d9793e746'
down_revision: Union[str, None] = '57264df634d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """서울숲클라이밍 추가 (EASY 빨강 → HARD 핑크, 10색)."""
    gym_table = sa.table(
        "gym_grade_systems",
        sa.column("gym_name", sa.String),
        sa.column("color_order", postgresql.ARRAY(sa.String)),
        sa.column("is_official", sa.Boolean),
    )
    stmt = (
        postgresql.insert(gym_table)
        .values(
            gym_name="서울숲클라이밍",
            color_order=["빨", "주", "노", "초", "파", "남", "보", "갈", "검", "핑"],
            is_official=True,
        )
        .on_conflict_do_nothing(index_elements=["gym_name"])
    )
    op.get_bind().execute(stmt)


def downgrade() -> None:
    """서울숲클라이밍 제거."""
    op.get_bind().execute(
        sa.text("DELETE FROM gym_grade_systems WHERE gym_name = :g"),
        {"g": "서울숲클라이밍"},
    )
