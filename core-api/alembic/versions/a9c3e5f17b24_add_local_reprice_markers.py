"""add local_reprice_markers

Revision ID: a9c3e5f17b24
Revises: e5a1c3d7f902
Create Date: 2026-10-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9c3e5f17b24'
down_revision: Union[str, Sequence[str], None] = 'e5a1c3d7f902'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'local_reprice_markers',
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('reprice_from', sa.DateTime(timezone=True), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['source_id'], ['sources.id']),
        sa.PrimaryKeyConstraint('source_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('local_reprice_markers')
