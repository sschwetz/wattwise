"""add sites.latitude and sites.longitude

Revision ID: a9c3e5f7b1d4
Revises: b7d1f4a2c6e9
Create Date: 2026-11-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a9c3e5f7b1d4'
down_revision: Union[str, Sequence[str], None] = 'b7d1f4a2c6e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('sites', sa.Column('latitude', sa.Float(), nullable=True))
    op.add_column('sites', sa.Column('longitude', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('sites') as batch_op:
        batch_op.drop_column('longitude')
        batch_op.drop_column('latitude')
