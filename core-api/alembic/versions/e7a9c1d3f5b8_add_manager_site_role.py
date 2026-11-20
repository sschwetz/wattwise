"""add the manager site role

Revision ID: e7a9c1d3f5b8
Revises: d5f7b9c1e3a2
Create Date: 2026-11-21 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e7a9c1d3f5b8'
down_revision: Union[str, Sequence[str], None] = 'd5f7b9c1e3a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _set_role_check(roles: str) -> None:
    with op.batch_alter_table('site_access') as batch_op:
        batch_op.drop_constraint('ck_site_access_role', type_='check')
        batch_op.create_check_constraint('ck_site_access_role', f"role IN {roles}")


def upgrade() -> None:
    """Upgrade schema."""
    _set_role_check("('viewer', 'manager')")


def downgrade() -> None:
    """Downgrade schema."""
    # A manager keeps read access as a viewer rather than losing the site.
    op.execute(sa.text("UPDATE site_access SET role = 'viewer' WHERE role = 'manager'"))
    _set_role_check("('viewer')")
