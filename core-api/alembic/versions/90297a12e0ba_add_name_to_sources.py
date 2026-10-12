"""add name to sources

Revision ID: 90297a12e0ba
Revises: 446663878755
Create Date: 2026-10-07 19:19:46.335175

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '90297a12e0ba'
down_revision: Union[str, Sequence[str], None] = '446663878755'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('sources') as batch_op:
        batch_op.add_column(sa.Column('name', sa.String(200), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('sources') as batch_op:
        batch_op.drop_column('name')
