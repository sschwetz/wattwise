"""multi action schedules

Revision ID: e5a7c9b1d3f2
Revises: a1b2c3d4e5f6
Create Date: 2026-11-12 09:00:00.000000

"""
from typing import Callable, Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5a7c9b1d3f2'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_OUTCOMES = "('pending', 'running', 'ok', 'skipped', 'failed', 'overridden')"
NEW_OUTCOMES = "('pending', 'running', 'ok', 'skipped', 'failed', 'overridden', 'partial')"

schedules = sa.table(
    'actor_schedules',
    sa.column('id', sa.Integer),
    sa.column('action', sa.JSON),
    sa.column('actions', sa.JSON),
)


def _copy_column(source: str, target: str, convert: Callable) -> None:
    """Fill `target` from `source` for every schedule, one row at a time so it works on any engine."""
    bind = op.get_bind()
    rows = bind.execute(sa.select(schedules.c.id, schedules.c[source])).all()
    for row_id, value in rows:
        bind.execute(sa.update(schedules).where(schedules.c.id == row_id).values({target: convert(value)}))


def _set_outcome_check(outcomes: str) -> None:
    with op.batch_alter_table('actor_runs') as batch_op:
        batch_op.drop_constraint('ck_actor_runs_outcome', type_='check')
        batch_op.create_check_constraint('ck_actor_runs_outcome', f"outcome IN {outcomes}")


def _swap_schedule_column(old: str, new: str, convert: Callable) -> None:
    """Add `new`, fill it from `old`, make it required and drop `old`."""
    with op.batch_alter_table('actor_schedules') as batch_op:
        batch_op.add_column(sa.Column(new, sa.JSON(), nullable=True))
    _copy_column(old, new, convert)
    with op.batch_alter_table('actor_schedules') as batch_op:
        batch_op.alter_column(new, existing_type=sa.JSON(), nullable=False)
        batch_op.drop_column(old)


def upgrade() -> None:
    """Upgrade schema."""
    _swap_schedule_column('action', 'actions', lambda action: [action])
    with op.batch_alter_table('actor_runs') as batch_op:
        batch_op.add_column(sa.Column('steps', sa.JSON(), nullable=True))
    _set_outcome_check(NEW_OUTCOMES)


def downgrade() -> None:
    """Downgrade schema."""
    # The old schema has no partial outcome, so those runs read as failed.
    op.execute("UPDATE actor_runs SET outcome = 'failed' WHERE outcome = 'partial'")
    _set_outcome_check(OLD_OUTCOMES)
    with op.batch_alter_table('actor_runs') as batch_op:
        batch_op.drop_column('steps')
    _swap_schedule_column('actions', 'action', lambda actions: actions[0])
