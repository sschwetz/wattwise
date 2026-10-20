"""add spike_status to price_intervals

Revision ID: b4d7e1a9c3f2
Revises: f1a8c3d5e7b2
Create Date: 2026-10-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4d7e1a9c3f2'
down_revision: Union[str, Sequence[str], None] = 'f1a8c3d5e7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('price_intervals') as batch_op:
        batch_op.add_column(sa.Column('spike_status', sa.String(length=16), server_default='none', nullable=False))
        batch_op.create_check_constraint(
            'ck_price_intervals_spike_status', "spike_status IN ('none', 'potential', 'spike')"
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('price_intervals') as batch_op:
        batch_op.drop_constraint('ck_price_intervals_spike_status', type_='check')
        batch_op.drop_column('spike_status')
