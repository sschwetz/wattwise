"""add actors

Revision ID: 895863e431c6
Revises: e5a7b9c2d4f6
Create Date: 2026-11-05 00:04:15.289168

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '895863e431c6'
down_revision: Union[str, Sequence[str], None] = 'e5a7b9c2d4f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('actor_instances',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('config', sa.Text(), nullable=False),
    sa.Column('auth_state', sa.JSON(), nullable=True),
    sa.Column('capabilities', sa.JSON(), nullable=True),
    sa.Column('capabilities_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('latest_readings', sa.JSON(), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_actor_instances_site_id'), 'actor_instances', ['site_id'], unique=False)
    op.create_table('actor_schedules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('instance_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('trigger', sa.JSON(), nullable=False),
    sa.Column('conditions', sa.JSON(), nullable=False),
    sa.Column('action', sa.JSON(), nullable=False),
    sa.Column('restore', sa.JSON(), nullable=False),
    sa.Column('retry', sa.JSON(), nullable=False),
    sa.Column('notify_email', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['instance_id'], ['actor_instances.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_actor_schedules_instance_id'), 'actor_schedules', ['instance_id'], unique=False)
    op.create_table('actor_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('schedule_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Enum('action', 'restore', 'test', name='runkind', native_enum=False, length=16), nullable=False),
    sa.Column('trigger_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('outcome', sa.Enum('pending', 'running', 'ok', 'skipped', 'failed', 'overridden', name='runoutcome', native_enum=False, length=16), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('parent_run_id', sa.Integer(), nullable=True),
    sa.Column('snapshot', sa.JSON(), nullable=True),
    sa.Column('watchdog_notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("kind IN ('action', 'restore', 'test')", name='ck_actor_runs_kind'),
    sa.CheckConstraint("outcome IN ('pending', 'running', 'ok', 'skipped', 'failed', 'overridden')", name='ck_actor_runs_outcome'),
    sa.ForeignKeyConstraint(['parent_run_id'], ['actor_runs.id'], ),
    sa.ForeignKeyConstraint(['schedule_id'], ['actor_schedules.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('schedule_id', 'kind', 'trigger_at', name='uq_actor_runs_schedule_kind_trigger')
    )
    op.create_index(op.f('ix_actor_runs_schedule_id'), 'actor_runs', ['schedule_id'], unique=False)
    op.create_index(op.f('ix_actor_runs_parent_run_id'), 'actor_runs', ['parent_run_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_actor_runs_parent_run_id'), table_name='actor_runs')
    op.drop_index(op.f('ix_actor_runs_schedule_id'), table_name='actor_runs')
    op.drop_table('actor_runs')
    op.drop_index(op.f('ix_actor_schedules_instance_id'), table_name='actor_schedules')
    op.drop_table('actor_schedules')
    op.drop_index(op.f('ix_actor_instances_site_id'), table_name='actor_instances')
    op.drop_table('actor_instances')
