from __future__ import annotations

import datetime

from core_api.attempt_tracker import AttemptTracker


def test_is_locked_false_for_unknown_key():
    tracker = AttemptTracker()
    assert tracker.is_locked("unknown") is False


def test_locks_after_five_failures():
    tracker = AttemptTracker()
    for _ in range(4):
        tracker.register_failure("a")
        assert tracker.is_locked("a") is False
    tracker.register_failure("a")
    assert tracker.is_locked("a") is True


def test_clear_resets_the_counter():
    tracker = AttemptTracker()
    for _ in range(5):
        tracker.register_failure("a")
    assert tracker.is_locked("a") is True
    tracker.clear("a")
    assert tracker.is_locked("a") is False


def test_failures_older_than_the_window_age_out():
    """The property that makes keying by a value an attacker controls (email,
    user_id) safe: an old burst of failures must not lock a key out forever.
    Reaches into _failures directly to time-travel, since the tracker has no
    injectable clock seam."""
    tracker = AttemptTracker()
    stale = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=30)
    tracker._failures["a"] = [stale] * 7

    # 7 recorded failures, all outside the 15-minute window -> not locked.
    assert tracker.is_locked("a") is False
    # ...and is_locked pruned them, rather than leaving them to accumulate.
    assert tracker._failures["a"] == []


def test_recent_failures_still_count_when_mixed_with_stale_ones():
    tracker = AttemptTracker()
    now = datetime.datetime.now(datetime.UTC)
    stale = now - datetime.timedelta(minutes=30)
    recent = now - datetime.timedelta(minutes=1)
    # 4 stale + 5 recent: only the recent ones are inside the window, and
    # 5 is exactly the cap.
    tracker._failures["a"] = [stale] * 4 + [recent] * 5

    assert tracker.is_locked("a") is True
    assert tracker._failures["a"] == [recent] * 5


def test_lockout_is_per_key_not_shared():
    tracker = AttemptTracker()
    tracker.register_failure("a")
    for _ in range(5):
        tracker.register_failure("b")
    assert tracker.is_locked("b") is True
    assert tracker.is_locked("a") is False


def test_max_attempts_and_window_are_configurable():
    tracker = AttemptTracker(max_attempts=2, window=datetime.timedelta(minutes=1))
    tracker.register_failure("a")
    assert tracker.is_locked("a") is False
    tracker.register_failure("a")
    assert tracker.is_locked("a") is True
