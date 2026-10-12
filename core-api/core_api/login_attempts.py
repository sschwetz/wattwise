from __future__ import annotations

from core_api.attempt_tracker import AttemptTracker

# Keyed by email, not user_id: unlike mfa_attempts (which only runs once a
# password is already known to be correct, so a user_id exists), a login
# attempt must be throttled even for an email that has no account at all —
# otherwise the mere presence/absence of a lockout after 5 tries would itself
# reveal whether the address is registered.
_tracker = AttemptTracker()

register_failure = _tracker.register_failure
is_locked = _tracker.is_locked
clear = _tracker.clear
