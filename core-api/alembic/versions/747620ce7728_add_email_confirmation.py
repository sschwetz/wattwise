"""add email confirmation

Revision ID: 747620ce7728
Revises: 785d1dffd4c0
Create Date: 2026-10-12 11:44:42.575237

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '747620ce7728'
down_revision: Union[str, Sequence[str], None] = '785d1dffd4c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('email_confirmed', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        'email_confirmation_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index(
        op.f('ix_email_confirmation_tokens_user_id'), 'email_confirmation_tokens', ['user_id'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_email_confirmation_tokens_user_id'), table_name='email_confirmation_tokens')
    op.drop_table('email_confirmation_tokens')
    op.drop_column('users', 'email_confirmed')
