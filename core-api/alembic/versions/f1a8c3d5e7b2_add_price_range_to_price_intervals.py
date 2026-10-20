"""add price_low and price_high to price_intervals

Revision ID: f1a8c3d5e7b2
Revises: e8b2c4d6f1a9
Create Date: 2026-10-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a8c3d5e7b2'
down_revision: Union[str, Sequence[str], None] = 'e8b2c4d6f1a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('price_intervals', sa.Column('price_low', sa.Numeric(precision=12, scale=4), nullable=True))
    op.add_column('price_intervals', sa.Column('price_high', sa.Numeric(precision=12, scale=4), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('price_intervals', 'price_high')
    op.drop_column('price_intervals', 'price_low')
