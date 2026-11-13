"""add severity, cleared_at and sticky to alerts

Revision ID: b7d1f4a2c6e9
Revises: a3c5e7d9b1f2
Create Date: 2026-11-13 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b7d1f4a2c6e9'
down_revision: Union[str, Sequence[str], None] = 'a3c5e7d9b1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('alerts') as batch:
        batch.add_column(sa.Column('severity', sa.String(16), nullable=False, server_default='failure'))
        batch.add_column(sa.Column('cleared_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('sticky', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('alerts') as batch:
        batch.drop_column('sticky')
        batch.drop_column('cleared_at')
        batch.drop_column('severity')
