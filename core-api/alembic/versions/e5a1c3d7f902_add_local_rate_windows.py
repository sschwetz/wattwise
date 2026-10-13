"""add local_rate_windows

Revision ID: e5a1c3d7f902
Revises: c7e4a1f9b2d3
Create Date: 2026-10-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5a1c3d7f902'
down_revision: Union[str, Sequence[str], None] = 'c7e4a1f9b2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'local_rate_windows',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=32), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('effective_from', sa.DateTime(timezone=True), nullable=False),
        sa.Column('effective_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('months', sa.JSON(), nullable=False),
        sa.Column('days_of_week', sa.JSON(), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column('rate_c_per_kwh', sa.Numeric(12, 4), nullable=False),
        sa.CheckConstraint(
            "channel IN ('buy', 'sell', 'controlled_load')", name='ck_local_rate_windows_channel'
        ),
        sa.ForeignKeyConstraint(['source_id'], ['sources.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_local_rate_windows_source_id'), 'local_rate_windows', ['source_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_local_rate_windows_source_id'), table_name='local_rate_windows')
    op.drop_table('local_rate_windows')
