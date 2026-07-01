"""add comment_likes table

Revision ID: df4bd125be8c
Revises: ed79812163d4
Create Date: 2026-07-01 12:41:58.360539+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'df4bd125be8c'
down_revision: Union[str, None] = 'ed79812163d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'comment_likes',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False, comment='좋아요 누른 사용자'),
        sa.Column('comment_id', sa.UUID(), nullable=False, comment='좋아요 대상 댓글'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['comment_id'], ['comments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'comment_id', name='uq_comment_likes_user_comment'),
    )
    op.create_index(op.f('ix_comment_likes_comment_id'), 'comment_likes', ['comment_id'], unique=False)
    op.create_index(op.f('ix_comment_likes_user_id'), 'comment_likes', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_comment_likes_user_id'), table_name='comment_likes')
    op.drop_index(op.f('ix_comment_likes_comment_id'), table_name='comment_likes')
    op.drop_table('comment_likes')
