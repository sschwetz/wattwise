"""add pending password hash to email confirmation tokens

Revision ID: fdda8a2d8274
Revises: 747620ce7728
Create Date: 2026-10-12 12:36:31.877124

"""
import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fdda8a2d8274'
down_revision: Union[str, Sequence[str], None] = '747620ce7728'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('email_confirmation_tokens') as batch_op:
        batch_op.add_column(sa.Column('pending_password_hash', sa.String(length=255), nullable=True))

    conn = op.get_bind()
    # Any token issued before this column existed has no pending password to
    # apply at confirm -- invalidate it outright rather than leave it
    # confirmable with an empty credential; the affected user just registers
    # again to get a fresh one.
    conn.execute(
        sa.text("UPDATE email_confirmation_tokens SET used_at = :now WHERE used_at IS NULL").bindparams(
            now=datetime.datetime.now(datetime.timezone.utc)
        )
    )
    # Every row (freshly invalidated above, or already used) is now
    # unconfirmable regardless of this value -- backfill so the column can
    # be tightened to NOT NULL.
    conn.execute(
        sa.text("UPDATE email_confirmation_tokens SET pending_password_hash = '' WHERE pending_password_hash IS NULL")
    )

    with op.batch_alter_table('email_confirmation_tokens') as batch_op:
        batch_op.alter_column('pending_password_hash', existing_type=sa.String(length=255), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('email_confirmation_tokens') as batch_op:
        batch_op.drop_column('pending_password_hash')
