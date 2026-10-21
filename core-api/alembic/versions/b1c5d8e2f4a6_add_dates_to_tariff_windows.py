"""add effective dates to demand, export adjustment and conditional credit windows

Revision ID: b1c5d8e2f4a6
Revises: a7e2c9d4f1b8
Create Date: 2026-10-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1c5d8e2f4a6'
down_revision: Union[str, Sequence[str], None] = 'a7e2c9d4f1b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

WINDOW_TABLES = ('demand_windows', 'export_adjustment_windows', 'conditional_credit_windows')


def upgrade() -> None:
    """Upgrade schema. Each existing window takes its tariff's dates, so it
    prices the same minutes as before."""
    for table in WINDOW_TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column('effective_from', sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(sa.Column('effective_to', sa.DateTime(timezone=True), nullable=True))
        op.execute(
            f"UPDATE {table} SET "
            f"effective_from = (SELECT effective_from FROM site_tariffs WHERE site_tariffs.id = {table}.site_tariff_id), "
            f"effective_to = (SELECT effective_to FROM site_tariffs WHERE site_tariffs.id = {table}.site_tariff_id)"
        )
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column('effective_from', existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    for table in WINDOW_TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column('effective_to')
            batch_op.drop_column('effective_from')
