"""fix 손상원클라이밍 color order

Revision ID: 6f3e3f92d02f
Revises: 984d9793e746
Create Date: 2026-07-06 08:01:16.165265+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6f3e3f92d02f'
down_revision: Union[str, None] = '984d9793e746'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """손상원클라이밍 색 순서 수정 (V8 보라 추가, 공식 난이도표 기준)."""
    op.get_bind().execute(
        sa.text(
            "UPDATE gym_grade_systems SET color_order = :co "
            "WHERE gym_name = :g"
        ),
        {
            "co": ["흰", "노", "초", "파", "빨", "검", "회", "갈", "핑", "보"],
            "g": "손상원클라이밍",
        },
    )


def downgrade() -> None:
    """이전 색 순서(9색)로 복원."""
    op.get_bind().execute(
        sa.text(
            "UPDATE gym_grade_systems SET color_order = :co "
            "WHERE gym_name = :g"
        ),
        {
            "co": ["흰", "노", "초", "파", "빨", "검", "회", "갈", "핑"],
            "g": "손상원클라이밍",
        },
    )
