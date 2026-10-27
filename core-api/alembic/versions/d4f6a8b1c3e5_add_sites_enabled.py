"""add sites.enabled

Revision ID: d4f6a8b1c3e5
Revises: c7e2a9d4b1f8
Create Date: 2026-10-27 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd4f6a8b1c3e5'
down_revision: Union[str, Sequence[str], None] = 'c7e2a9d4b1f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The server default makes existing sites enabled.
    op.add_column(
        'sites',
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('sites') as batch_op:
        batch_op.drop_column('enabled')
