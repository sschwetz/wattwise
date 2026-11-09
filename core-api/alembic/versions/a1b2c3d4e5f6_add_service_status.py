"""add service_status

Revision ID: a1b2c3d4e5f6
Revises: 895863e431c6
Create Date: 2026-11-09 19:28:32.789791

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '895863e431c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "service_status",
        sa.Column("service", sa.String(length=64), nullable=False),
        sa.Column("commit", sa.String(length=40), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("service"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("service_status")
