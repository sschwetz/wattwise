from __future__ import annotations

import re

COMMIT_PATTERN: re.Pattern[str] = re.compile(r"^([0-9a-f]{7,40}|unknown|dev)$")
SHORT_COMMIT_PATTERN: re.Pattern[str] = re.compile(r"^([0-9a-f]{7}|unknown|dev)$")


def normalise_commit(raw: str | None) -> str:
    """Lowercase and shorten to 7 chars; anything that is not a short hash, unknown or dev becomes unknown."""
    value = (raw or "").strip().lower()[:7]
    return value if SHORT_COMMIT_PATTERN.match(value) else "unknown"
