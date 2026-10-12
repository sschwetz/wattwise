from __future__ import annotations

from core_api.attempt_tracker import AttemptTracker

# Keyed by user_id (not jti): a per-token counter is trivially reset by
# re-login (mint a fresh mfa_token, get a fresh budget), since the attacker
# already holds the password at this stage. Keying by user_id with a
# rolling time window closes that gap without the failure mode a naive
# permanent per-user counter would have (indefinite self-lockout with no
# recovery path, since this project has no admin-unlock UI) — failures
# older than the window simply age out.
_tracker = AttemptTracker()

register_failure = _tracker.register_failure
is_locked = _tracker.is_locked
clear = _tracker.clear
