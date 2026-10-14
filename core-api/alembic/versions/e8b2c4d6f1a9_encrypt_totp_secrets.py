"""encrypt TOTP secrets left in plaintext

Revision ID: e8b2c4d6f1a9
Revises: d3a7b9c1e4f8
Create Date: 2026-10-14 20:30:00.000000

c5d8f1a3b6e2 was edited to cover the TOTP columns after some databases had
already applied it, so those databases still hold plaintext TOTP secrets.
This repeats that step for the two TOTP columns. Values that are already
Fernet tokens are skipped, so it is a no-op where c5d8f1a3b6e2 did the work.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from core_api.encrypted_types import decrypt_text, encrypt_text


# revision identifiers, used by Alembic.
revision: str = 'e8b2c4d6f1a9'
down_revision: Union[str, Sequence[str], None] = 'd3a7b9c1e4f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = ["totp_secret", "pending_totp_secret"]


def _rewrite(encrypt: bool) -> None:
    bind = op.get_bind()
    for column in _COLUMNS:
        rows = bind.execute(sa.text(f"SELECT id, {column} FROM users WHERE {column} IS NOT NULL")).fetchall()
        for row_id, value in rows:
            if value.startswith("gAAAA") == encrypt:
                continue
            new_value = encrypt_text(value) if encrypt else decrypt_text(value)
            bind.execute(sa.text(f"UPDATE users SET {column} = :v WHERE id = :id"), {"v": new_value, "id": row_id})


def upgrade() -> None:
    """Encrypt any plaintext TOTP secrets."""
    _rewrite(encrypt=True)


def downgrade() -> None:
    """Restore plaintext TOTP secrets (needs the same SECRETS_ENCRYPTION_KEY)."""
    _rewrite(encrypt=False)
