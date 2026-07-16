"""add likes table

Revision ID: 80a20ce6a199
Revises: d4e5f6a7b8c9
Create Date: 2026-07-01 01:28:01.724324+00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '80a20ce6a199'
down_revision: str | None = 'd4e5f6a7b8c9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'likes',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False, comment='좋아요 누른 사용자'),
        sa.Column('climbing_log_id', sa.UUID(), nullable=False, comment='좋아요 대상 게시물'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['climbing_log_id'], ['climbing_logs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'climbing_log_id', name='uq_likes_user_log'),
    )
    op.create_index(op.f('ix_likes_climbing_log_id'), 'likes', ['climbing_log_id'], unique=False)
    op.create_index(op.f('ix_likes_user_id'), 'likes', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_likes_user_id'), table_name='likes')
    op.drop_index(op.f('ix_likes_climbing_log_id'), table_name='likes')
    op.drop_table('likes')
