"""add monthly fee to tariffs and monthly_fee_discount_windows

Revision ID: 2d0367d67fe9
Revises: c2d4e8a91f06
Create Date: 2026-10-10 16:37:13.024466

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2d0367d67fe9'
down_revision: Union[str, Sequence[str], None] = 'c2d4e8a91f06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('site_tariffs', sa.Column('monthly_fee_c', sa.Numeric(10, 4), nullable=True))
    op.create_table(
        'monthly_fee_discount_windows',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_tariff_id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('discounted_fee_c', sa.Numeric(10, 4), nullable=False),
        sa.ForeignKeyConstraint(['site_tariff_id'], ['site_tariffs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_monthly_fee_discount_windows_site_tariff_id'),
        'monthly_fee_discount_windows',
        ['site_tariff_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_monthly_fee_discount_windows_site_tariff_id'), table_name='monthly_fee_discount_windows')
    op.drop_table('monthly_fee_discount_windows')
    op.drop_column('site_tariffs', 'monthly_fee_c')
