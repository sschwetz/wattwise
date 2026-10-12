"""drop collector_api_key_hash from sources

Revision ID: 446663878755
Revises: a1c9f2e4b7d3
Create Date: 2026-10-07 15:07:53.894602

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '446663878755'
down_revision: Union[str, Sequence[str], None] = 'a1c9f2e4b7d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('sources') as batch_op:
        batch_op.drop_column('collector_api_key_hash')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('sources') as batch_op:
        batch_op.add_column(sa.Column('collector_api_key_hash', sa.String(64), nullable=False, server_default=''))
    op.execute("UPDATE sources SET collector_api_key_hash = lower(hex(randomblob(32)))")
    with op.batch_alter_table('sources') as batch_op:
        batch_op.create_unique_constraint('uq_sources_collector_api_key_hash', ['collector_api_key_hash'])
