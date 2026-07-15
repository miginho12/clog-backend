"""add brand_name to gym_grade_systems

Revision ID: ef96f2ae7b85
Revises: 13c55e5c6909
Create Date: 2026-07-15 06:10:03.583733+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ef96f2ae7b85"
down_revision: str | None = "13c55e5c6909"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "gym_grade_systems",
        sa.Column(
            "brand_name",
            sa.String(length=100),
            nullable=True,
            comment="브랜드/체인 이름 — 같은 브랜드 지점을 묶어보는 용도. "
            "지점별 color_order 는 별개로 유지됨",
        ),
    )
    # 기존 등록분은 각자 자기 자신을 브랜드로 백필 (지점 분리 전 단일 항목들이라
    # 브랜드 필터로 조회해도 그대로 나오게). 이후 같은 브랜드의 새 지점을
    # 등록할 때 brand_name 을 맞춰주면 자동으로 묶인다.
    op.execute(
        "UPDATE gym_grade_systems SET brand_name = gym_name WHERE brand_name IS NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("gym_grade_systems", "brand_name")
