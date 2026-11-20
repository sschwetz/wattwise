"""add the mail provider and email outbox tables

Revision ID: f1a3c5e7b9d2
Revises: e7a9c1d3f5b8
Create Date: 2026-11-21 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from core_api.encrypted_types import EncryptedJSON, EncryptedString


# revision identifiers, used by Alembic.
revision: str = 'f1a3c5e7b9d2'
down_revision: Union[str, Sequence[str], None] = 'e7a9c1d3f5b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'mail_providers',
        sa.Column('provider', sa.String(length=64), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('fields', sa.JSON(), nullable=False),
        sa.Column('settings', EncryptedJSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('declared_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('provider'),
    )
    op.create_index(
        'ix_mail_providers_one_active',
        'mail_providers',
        ['is_active'],
        unique=True,
        sqlite_where=sa.text('is_active'),
        postgresql_where=sa.text('is_active'),
    )
    op.create_table(
        'email_outbox',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('to_address', sa.String(length=320), nullable=False),
        sa.Column('subject', sa.String(length=998), nullable=False),
        sa.Column('body', EncryptedString(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed')", name='ck_email_outbox_status'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_email_outbox_status_next_attempt_at', 'email_outbox', ['status', 'next_attempt_at']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_email_outbox_status_next_attempt_at', table_name='email_outbox')
    op.drop_table('email_outbox')
    op.drop_index(
        'ix_mail_providers_one_active',
        table_name='mail_providers',
        sqlite_where=sa.text('is_active'),
        postgresql_where=sa.text('is_active'),
    )
    op.drop_table('mail_providers')
