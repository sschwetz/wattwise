"""add daily_allowance_kwh to export_adjustment_windows

Revision ID: a7e2c9d4f1b8
Revises: b4d7e1a9c3f2
Create Date: 2026-10-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7e2c9d4f1b8'
down_revision: Union[str, Sequence[str], None] = 'b4d7e1a9c3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'export_adjustment_windows',
        sa.Column('daily_allowance_kwh', sa.Numeric(10, 4), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('export_adjustment_windows', 'daily_allowance_kwh')
