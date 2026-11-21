"""store email_outbox.body as text

Revision ID: a7c9e1b3d5f2
Revises: f1a3c5e7b9d2
Create Date: 2026-11-21 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a7c9e1b3d5f2'
down_revision: Union[str, Sequence[str], None] = 'f1a3c5e7b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The body is a Fernet token, about 1.4 times the plaintext, so a
    # String(1024) column on Postgres refused bodies over about 650 characters.
    with op.batch_alter_table('email_outbox') as batch_op:
        batch_op.alter_column('body', existing_type=sa.String(length=1024), type_=sa.Text(), existing_nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('email_outbox') as batch_op:
        batch_op.alter_column('body', existing_type=sa.Text(), type_=sa.String(length=1024), existing_nullable=False)
