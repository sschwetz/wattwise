"""add actor_schedules.limits and actor_schedules.armed

Revision ID: c4e6a8b0d2f3
Revises: a9c3e5f7b1d4
Create Date: 2026-11-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4e6a8b0d2f3'
down_revision: Union[str, Sequence[str], None] = 'a9c3e5f7b1d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('actor_schedules', sa.Column('limits', sa.JSON(), nullable=False, server_default='{}'))
    op.add_column('actor_schedules', sa.Column('armed', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('actor_schedules') as batch_op:
        batch_op.drop_column('armed')
        batch_op.drop_column('limits')
