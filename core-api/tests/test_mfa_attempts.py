from __future__ import annotations

from core_api.mfa_attempts import clear, is_locked, register_failure

# The sliding-window counter logic itself (aging out, per-key isolation,
# configurable thresholds) is generic and tested once in test_attempt_tracker.py.
# These tests just confirm this module's public functions are wired to a real
# tracker end-to-end.


def test_is_locked_false_for_unknown_user():
    assert is_locked(999_001) is False


def test_locks_after_five_failures_then_clear_resets_it():
    user_id = 42
    for _ in range(5):
        register_failure(user_id)
    assert is_locked(user_id) is True
    clear(user_id)
    assert is_locked(user_id) is False


def test_lockout_is_per_user_not_shared():
    register_failure(46)
    for _ in range(5):
        register_failure(47)
    assert is_locked(47) is True
    assert is_locked(46) is False
