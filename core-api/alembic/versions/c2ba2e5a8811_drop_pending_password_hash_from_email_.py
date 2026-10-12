"""drop pending password hash from email confirmation tokens

Revision ID: c2ba2e5a8811
Revises: fdda8a2d8274
Create Date: 2026-10-12 00:00:00.000000

"""
import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2ba2e5a8811'
down_revision: Union[str, Sequence[str], None] = 'fdda8a2d8274'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The password is now chosen at /register/confirm time, by whoever holds
    # the token, instead of being baked into the token by whoever last called
    # /register -- closing an account-takeover where a third party could set
    # the password a victim's own confirmation click would apply. Nothing
    # left on this column is ever read going forward.
    with op.batch_alter_table('email_confirmation_tokens') as batch_op:
        batch_op.drop_column('pending_password_hash')

    # A freshly-registered, still-unconfirmed user now has no password at
    # all until /register/confirm sets one -- see User.password_hash --
    # rather than a placeholder hash of some fixed, known value, so the
    # column must accept NULL.
    with op.batch_alter_table('users') as batch_op:
        batch_op.alter_column('password_hash', existing_type=sa.String(length=255), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    # Any user with no password_hash yet is unconfirmed and already
    # unusable for login (login() rejects every unconfirmed account
    # regardless of password) -- backfill with the DB's existing timing-
    # dummy pattern isn't available at migration time, so an empty string
    # is enough: still unusable, just satisfies NOT NULL again.
    conn.execute(sa.text("UPDATE users SET password_hash = '' WHERE password_hash IS NULL"))

    with op.batch_alter_table('users') as batch_op:
        batch_op.alter_column('password_hash', existing_type=sa.String(length=255), nullable=False)

    with op.batch_alter_table('email_confirmation_tokens') as batch_op:
        batch_op.add_column(sa.Column('pending_password_hash', sa.String(length=255), nullable=True))

    # Any outstanding token has no pending password to restore -- invalidate
    # it outright rather than leave it confirmable with an empty credential.
    conn.execute(
        sa.text("UPDATE email_confirmation_tokens SET used_at = :now WHERE used_at IS NULL").bindparams(
            now=datetime.datetime.now(datetime.timezone.utc)
        )
    )
    conn.execute(
        sa.text("UPDATE email_confirmation_tokens SET pending_password_hash = '' WHERE pending_password_hash IS NULL")
    )

    with op.batch_alter_table('email_confirmation_tokens') as batch_op:
        batch_op.alter_column('pending_password_hash', existing_type=sa.String(length=255), nullable=False)
