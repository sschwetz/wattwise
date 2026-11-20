"""add users.is_admin

Revision ID: d5f7b9c1e3a2
Revises: c4e6a8b0d2f3
Create Date: 2026-11-20 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd5f7b9c1e3a2'
down_revision: Union[str, Sequence[str], None] = 'c4e6a8b0d2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.false()))
    # The first account is the one the bootstrap created, so it becomes the admin.
    op.execute(sa.text('UPDATE users SET is_admin = :t WHERE id = (SELECT MIN(id) FROM users)').bindparams(
        sa.bindparam('t', True, type_=sa.Boolean())
    ))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('is_admin')
