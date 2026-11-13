"""add sources.alert_message and sources.alert_since

Revision ID: f6b2d8a4c1e7
Revises: e5a7c9b1d3f2
Create Date: 2026-11-13 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f6b2d8a4c1e7'
down_revision: Union[str, Sequence[str], None] = 'e5a7c9b1d3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('sources', sa.Column('alert_message', sa.Text(), nullable=True))
    op.add_column('sources', sa.Column('alert_since', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('sources') as batch_op:
        batch_op.drop_column('alert_since')
        batch_op.drop_column('alert_message')
