"""delete recovery codes hashed with unkeyed SHA-256

Revision ID: d3a7b9c1e4f8
Revises: c5d8f1a3b6e2
Create Date: 2026-10-14 20:00:00.000000

Recovery codes are now stored as a keyed HMAC (security.hash_recovery_code).
The old hashes cannot be converted, so every existing code is deleted. Users
get new codes by replacing their authenticator under Account settings.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd3a7b9c1e4f8'
down_revision: Union[str, Sequence[str], None] = 'c5d8f1a3b6e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Delete the old recovery codes."""
    op.execute("DELETE FROM recovery_codes")


def downgrade() -> None:
    """Deleted codes cannot be restored; nothing to do."""
