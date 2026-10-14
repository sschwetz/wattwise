"""encrypt source configs, Amber API keys and TOTP secrets

Revision ID: c5d8f1a3b6e2
Revises: f3b7d2a9c814
Create Date: 2026-10-14 19:10:00.000000

Data-only: SQLite stores both columns as text, so the declared column types
stay as they are. Rows already holding a Fernet token are skipped, so a
rerun after a partial failure is safe.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from core_api.encrypted_types import decrypt_text, encrypt_text


# revision identifiers, used by Alembic.
revision: str = 'c5d8f1a3b6e2'
down_revision: Union[str, Sequence[str], None] = 'f3b7d2a9c814'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Columns encrypted in place.
_COLUMNS = [
    ("sources", "config"),
    ("users", "amber_api_key"),
    ("users", "totp_secret"),
    ("users", "pending_totp_secret"),
]


def _is_token(value: str) -> bool:
    return value.startswith("gAAAA")


def _rewrite(encrypt: bool) -> None:
    bind = op.get_bind()
    for table, column in _COLUMNS:
        rows = bind.execute(sa.text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")).fetchall()
        for row_id, value in rows:
            if _is_token(value) == encrypt:
                continue
            new_value = encrypt_text(value) if encrypt else decrypt_text(value)
            bind.execute(sa.text(f"UPDATE {table} SET {column} = :v WHERE id = :id"), {"v": new_value, "id": row_id})


def upgrade() -> None:
    """Encrypt existing plaintext values."""
    _rewrite(encrypt=True)


def downgrade() -> None:
    """Restore plaintext values (needs the same SECRETS_ENCRYPTION_KEY)."""
    _rewrite(encrypt=False)
