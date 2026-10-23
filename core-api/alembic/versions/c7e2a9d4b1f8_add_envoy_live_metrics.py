"""add envoy live-data metrics to energy metric constraints

Revision ID: c7e2a9d4b1f8
Revises: b1c5d8e2f4a6
Create Date: 2026-10-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c7e2a9d4b1f8'
down_revision: Union[str, Sequence[str], None] = 'b1c5d8e2f4a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_METRICS = (
    'production_w', 'consumption_w', 'grid_import_w', 'grid_export_w',
    'battery_charge_w', 'battery_discharge_w', 'battery_soc_pct',
)
NEW_METRICS = (
    'production_wh_lifetime', 'grid_import_wh_lifetime', 'grid_export_wh_lifetime',
    'grid_voltage_ph_a_v', 'grid_voltage_ph_b_v', 'grid_voltage_ph_c_v',
    'grid_relay_state',
)
TABLES = (
    ('energy_readings', 'ck_energy_readings_metric'),
    ('energy_readings_rollup_5m', 'ck_energy_readings_rollup_5m_metric'),
)


def _metric_check(metrics: Sequence[str]) -> str:
    return "metric IN (" + ", ".join(f"'{m}'" for m in metrics) + ")"


def _replace_constraint(metrics: Sequence[str]) -> None:
    for table, constraint in TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(constraint, type_='check')
            batch_op.create_check_constraint(constraint, _metric_check(metrics))


def upgrade() -> None:
    """Upgrade schema."""
    _replace_constraint(OLD_METRICS + NEW_METRICS)


def downgrade() -> None:
    """Downgrade schema."""
    # Rows with the new metrics would violate the restored constraint.
    new_list = ", ".join(f"'{m}'" for m in NEW_METRICS)
    for table, _ in TABLES:
        op.execute(f"DELETE FROM {table} WHERE metric IN ({new_list})")
    _replace_constraint(OLD_METRICS)
