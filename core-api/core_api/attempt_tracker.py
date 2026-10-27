from __future__ import annotations

import datetime


class AttemptTracker:
    """In-process sliding-window failed-attempt counter, shared by every
    lockout in this service (login by email, MFA by user_id).

    Deliberately NOT keyed by anything that resets just by trying again (a
    fresh token's jti, say) -- see each call site's own comment for why that
    specific key choice closes a particular bypass. Module-level instances
    are intentionally process-local and in-memory: not built for a
    multi-worker deployment (would need a shared store, e.g. Redis), which
    matches the Core API spec's "not built for scale" stance. Failures older
    than the window age out instead of requiring any admin-unlock path."""

    def __init__(
        self, *, max_attempts: int = 5, window: datetime.timedelta = datetime.timedelta(minutes=15)
    ) -> None:
        self._max_attempts = max_attempts
        self._window = window
        self._failures: dict[object, list[datetime.datetime]] = {}

    def register_failure(self, key: object) -> None:
        now = datetime.datetime.now(datetime.UTC)
        self._failures.setdefault(key, []).append(now)

    def is_locked(self, key: object) -> bool:
        now = datetime.datetime.now(datetime.UTC)
        cutoff = now - self._window
        recent = [ts for ts in self._failures.get(key, []) if ts > cutoff]
        self._failures[key] = recent
        return len(recent) >= self._max_attempts

    def clear(self, key: object) -> None:
        self._failures.pop(key, None)
