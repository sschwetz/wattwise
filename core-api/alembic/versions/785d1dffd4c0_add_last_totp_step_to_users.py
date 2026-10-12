"""add last_totp_step to users

Revision ID: 785d1dffd4c0
Revises: 2d0367d67fe9
Create Date: 2026-10-11 18:33:34.365938

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '785d1dffd4c0'
down_revision: Union[str, Sequence[str], None] = '2d0367d67fe9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('last_totp_step', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'last_totp_step')
