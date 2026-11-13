"""add alerts, replacing sources.alert_message and sources.alert_since

Revision ID: a3c5e7d9b1f2
Revises: f6b2d8a4c1e7
Create Date: 2026-11-13 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a3c5e7d9b1f2'
down_revision: Union[str, Sequence[str], None] = 'f6b2d8a4c1e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'alerts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('sites.id'), nullable=False),
        sa.Column('subject_kind', sa.String(16), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(64), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('since', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('subject_kind', 'subject_id', 'code', name='uq_alerts_subject_code'),
    )
    op.create_index('ix_alerts_site_id', 'alerts', ['site_id'])
    # An alert set before this table existed was always about the Envoy certificate.
    op.execute(
        "INSERT INTO alerts (site_id, subject_kind, subject_id, code, message, since) "
        "SELECT site_id, 'source', id, 'cert', alert_message, alert_since FROM sources "
        "WHERE alert_message IS NOT NULL AND alert_since IS NOT NULL"
    )
    with op.batch_alter_table('sources') as batch_op:
        batch_op.drop_column('alert_since')
        batch_op.drop_column('alert_message')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('sources', sa.Column('alert_message', sa.Text(), nullable=True))
    op.add_column('sources', sa.Column('alert_since', sa.DateTime(timezone=True), nullable=True))
    op.drop_index('ix_alerts_site_id', table_name='alerts')
    op.drop_table('alerts')
