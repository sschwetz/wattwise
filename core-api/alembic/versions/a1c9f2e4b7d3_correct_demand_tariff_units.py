"""correct demand tariff units: rename columns, drop demand_window_hours

Revision ID: a1c9f2e4b7d3
Revises: d7a583fd431c
Create Date: 2026-10-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c9f2e4b7d3'
down_revision: Union[str, Sequence[str], None] = 'd7a583fd431c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('demand_windows') as batch_op:
        batch_op.alter_column(
            'rate_c_per_kwh', new_column_name='rate_c_per_kw_per_day', existing_type=sa.Numeric(10, 4)
        )
    with op.batch_alter_table('demand_intervals') as batch_op:
        batch_op.alter_column(
            'avg_import_w', new_column_name='rolling_sum_w', existing_type=sa.Float()
        )
    with op.batch_alter_table('demand_charges') as batch_op:
        batch_op.drop_column('demand_window_hours')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('demand_charges') as batch_op:
        batch_op.add_column(sa.Column('demand_window_hours', sa.Numeric(10, 4), nullable=False, server_default='0'))
    with op.batch_alter_table('demand_intervals') as batch_op:
        batch_op.alter_column(
            'rolling_sum_w', new_column_name='avg_import_w', existing_type=sa.Float()
        )
    with op.batch_alter_table('demand_windows') as batch_op:
        batch_op.alter_column(
            'rate_c_per_kw_per_day', new_column_name='rate_c_per_kwh', existing_type=sa.Numeric(10, 4)
        )
