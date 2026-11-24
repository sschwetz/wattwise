"""add job_schedules

Revision ID: b2d4f6a8c0e1
Revises: a7c9e1b3d5f2
Create Date: 2026-11-24 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b2d4f6a8c0e1'
down_revision: Union[str, Sequence[str], None] = 'a7c9e1b3d5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'job_schedules',
        sa.Column('job_id', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('interval_minutes', sa.Integer(), nullable=True),
        sa.Column('hour', sa.Integer(), nullable=True),
        sa.Column('minute', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('job_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('job_schedules')
