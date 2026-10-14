"""add conditional_credit_windows

Revision ID: f3b7d2a9c814
Revises: a9c3e5f17b24
Create Date: 2026-10-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3b7d2a9c814'
down_revision: Union[str, Sequence[str], None] = 'a9c3e5f17b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'conditional_credit_windows',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_tariff_id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('months', sa.JSON(), nullable=False),
        sa.Column('days_of_week', sa.JSON(), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column('max_avg_import_w', sa.Numeric(10, 4), nullable=False),
        sa.Column('credit_c', sa.Numeric(10, 4), nullable=False),
        sa.ForeignKeyConstraint(['site_tariff_id'], ['site_tariffs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_conditional_credit_windows_site_tariff_id'),
        'conditional_credit_windows',
        ['site_tariff_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_conditional_credit_windows_site_tariff_id'), table_name='conditional_credit_windows')
    op.drop_table('conditional_credit_windows')
