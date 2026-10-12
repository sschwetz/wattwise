"""add export_adjustment_windows

Revision ID: c2d4e8a91f06
Revises: afb00184195f
Create Date: 2026-10-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2d4e8a91f06'
down_revision: Union[str, Sequence[str], None] = 'afb00184195f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'export_adjustment_windows',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_tariff_id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('months', sa.JSON(), nullable=False),
        sa.Column('days_of_week', sa.JSON(), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column('adjustment_c_per_kwh', sa.Numeric(10, 4), nullable=False),
        sa.ForeignKeyConstraint(['site_tariff_id'], ['site_tariffs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_export_adjustment_windows_site_tariff_id'),
        'export_adjustment_windows',
        ['site_tariff_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_export_adjustment_windows_site_tariff_id'), table_name='export_adjustment_windows')
    op.drop_table('export_adjustment_windows')
