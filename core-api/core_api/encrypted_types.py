from __future__ import annotations

import json
import os
from functools import lru_cache

from cryptography.fernet import Fernet
from sqlalchemy import String, Text
from sqlalchemy.types import TypeDecorator


@lru_cache(maxsize=4)
def _fernet_for(key: str) -> Fernet:
    return Fernet(key.encode("utf-8"))


def _fernet() -> Fernet:
    # Read the env var directly: get_settings() builds a full Settings object,
    # and this runs for every encrypted value read or written.
    key = os.environ.get("SECRETS_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("SECRETS_ENCRYPTION_KEY environment variable must be set.")
    return _fernet_for(key)


def encrypt_text(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_text(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")


class EncryptedString(TypeDecorator):
    """A string stored as a Fernet token. Fernet output is about 1.4x the
    plaintext plus 100 bytes, so the column is wider than the plaintext."""

    impl = String(1024)
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        return None if value is None else encrypt_text(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        return None if value is None else decrypt_text(value)


class EncryptedJSON(TypeDecorator):
    """A JSON-serialisable value stored as a Fernet token. Not queryable in SQL."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect) -> str | None:
        return None if value is None else encrypt_text(json.dumps(value))

    def process_result_value(self, value: str | None, dialect):
        return None if value is None else json.loads(decrypt_text(value))
