"""add site_invites

Revision ID: e5a7b9c2d4f6
Revises: d4f6a8b1c3e5
Create Date: 2026-10-27 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e5a7b9c2d4f6'
down_revision: Union[str, Sequence[str], None] = 'd4f6a8b1c3e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'site_invites',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=False),
        sa.Column('inviter_user_id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('declined_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by_user_id', sa.Integer(), nullable=True),
        sa.Column('invitee_user_id', sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('invited', 'accepted', 'declined', 'revoked')",
            name='ck_site_invites_status',
        ),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id']),
        sa.ForeignKeyConstraint(['inviter_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['revoked_by_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['invitee_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_site_invites_site_id', 'site_invites', ['site_id'])
    op.create_index('ix_site_invites_email', 'site_invites', ['email'])
    # Each existing grant becomes an accepted invite from the site owner.
    op.execute(
        """
        INSERT INTO site_invites
            (site_id, inviter_user_id, email, status, created_at, accepted_at, invitee_user_id)
        SELECT sa.site_id, s.owner_user_id, LOWER(u.email), 'accepted',
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, sa.user_id
        FROM site_access sa
        JOIN sites s ON s.id = sa.site_id
        JOIN users u ON u.id = sa.user_id
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_site_invites_email', table_name='site_invites')
    op.drop_index('ix_site_invites_site_id', table_name='site_invites')
    op.drop_table('site_invites')
