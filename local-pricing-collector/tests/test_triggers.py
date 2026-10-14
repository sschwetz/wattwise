from __future__ import annotations

import datetime
import zoneinfo

from local_pricing_collector.triggers import SourceState, Triggers, after_success, due

UTC = datetime.timezone.utc
SYDNEY = zoneinfo.ZoneInfo("Australia/Sydney")
ADELAIDE = zoneinfo.ZoneInfo("Australia/Adelaide")
KEY = "windows-a"


def at(*args) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=UTC)


def test_a_source_with_no_state_needs_a_catch_up():
    assert due(None, at(2026, 6, 15, 0, 0), SYDNEY, KEY) == Triggers(catch_up=True)


def test_a_caught_up_source_is_not_due_during_the_day():
    state = SourceState(windows_key=KEY, caught_up_date=datetime.date(2026, 6, 15))
    assert due(state, at(2026, 6, 15, 0, 0), SYDNEY, KEY) == Triggers(catch_up=False)


def test_the_scheduled_push_is_due_at_23_59_local():
    state = SourceState(windows_key=KEY, caught_up_date=datetime.date(2026, 6, 15))
    # 13:59Z is 23:59 in Sydney (UTC+10 in June).
    assert due(state, at(2026, 6, 15, 13, 59), SYDNEY, KEY) == Triggers(
        catch_up=False, scheduled_for=datetime.date(2026, 6, 16)
    )


def test_it_is_not_due_at_23_58_local():
    state = SourceState(windows_key=KEY, caught_up_date=datetime.date(2026, 6, 15))
    assert not due(state, at(2026, 6, 15, 13, 58), SYDNEY, KEY).any


def test_the_scheduled_push_is_due_in_a_half_hour_zone():
    state = SourceState(windows_key=KEY, caught_up_date=datetime.date(2026, 6, 15))
    # 14:29Z is 23:59 in Adelaide (UTC+9:30 in June).
    assert due(state, at(2026, 6, 15, 14, 29), ADELAIDE, KEY).scheduled
    assert not due(state, at(2026, 6, 15, 14, 28), ADELAIDE, KEY).scheduled


def test_the_scheduled_push_stays_due_after_23_59_until_it_succeeds():
    state = SourceState(windows_key=KEY, caught_up_date=datetime.date(2026, 6, 15))
    assert due(state, at(2026, 6, 15, 13, 59, 30), SYDNEY, KEY).scheduled
    assert due(state, at(2026, 6, 15, 14, 0), SYDNEY, KEY).catch_up  # past local midnight, a new date


def test_the_scheduled_push_runs_once_per_target_date():
    state = SourceState(windows_key=KEY, caught_up_date=datetime.date(2026, 6, 15), scheduled_date=datetime.date(2026, 6, 16))
    assert not due(state, at(2026, 6, 15, 13, 59), SYDNEY, KEY).any


def test_a_new_local_date_triggers_a_catch_up():
    state = SourceState(windows_key=KEY, caught_up_date=datetime.date(2026, 6, 15), scheduled_date=datetime.date(2026, 6, 16))
    # 14:01Z is 00:01 on 16 June in Sydney.
    assert due(state, at(2026, 6, 15, 14, 1), SYDNEY, KEY) == Triggers(catch_up=True)


def test_after_a_catch_up_the_local_date_is_recorded():
    state = after_success(None, Triggers(catch_up=True), at(2026, 6, 15, 0, 0), SYDNEY, KEY)
    assert state == SourceState(windows_key=KEY, caught_up_date=datetime.date(2026, 6, 15), scheduled_date=None)


def test_after_a_scheduled_push_only_the_scheduled_date_changes():
    before = SourceState(windows_key=KEY, caught_up_date=datetime.date(2026, 6, 15))
    state = after_success(before, Triggers(catch_up=False, scheduled_for=datetime.date(2026, 6, 16)), at(2026, 6, 15, 13, 59), SYDNEY, KEY)
    assert state == SourceState(windows_key=KEY, caught_up_date=datetime.date(2026, 6, 15), scheduled_date=datetime.date(2026, 6, 16))


def test_the_recorded_date_is_the_local_date_not_the_utc_date():
    # 20:00Z on 15 June is 06:00 on 16 June in Sydney.
    state = after_success(None, Triggers(catch_up=True), at(2026, 6, 15, 20, 0), SYDNEY, KEY)
    assert state.caught_up_date == datetime.date(2026, 6, 16)


def test_a_failed_scheduled_push_is_still_owed_after_midnight():
    state = SourceState(windows_key=KEY, caught_up_date=datetime.date(2026, 6, 15), scheduled_date=datetime.date(2026, 6, 15))
    # 14:30Z is 00:30 on 16 June in Sydney.
    triggers = due(state, at(2026, 6, 15, 14, 30), SYDNEY, KEY)
    assert triggers.catch_up
    assert triggers.scheduled_for == datetime.date(2026, 6, 16)


def test_a_scheduled_push_that_succeeded_is_not_owed_after_midnight():
    state = SourceState(windows_key=KEY, caught_up_date=datetime.date(2026, 6, 16), scheduled_date=datetime.date(2026, 6, 16))
    assert due(state, at(2026, 6, 15, 14, 30), SYDNEY, KEY).scheduled_for is None


def test_the_scheduled_push_is_owed_until_02_00_local():
    state = SourceState(windows_key=KEY, caught_up_date=datetime.date(2026, 6, 16))
    assert due(state, at(2026, 6, 15, 15, 59), SYDNEY, KEY).scheduled_for == datetime.date(2026, 6, 16)  # 01:59
    assert due(state, at(2026, 6, 15, 16, 0), SYDNEY, KEY).scheduled_for is None  # 02:00


def test_a_changed_windows_key_triggers_a_catch_up_on_the_same_day():
    state = SourceState(caught_up_date=datetime.date(2026, 6, 15), windows_key="old")
    assert due(state, at(2026, 6, 15, 0, 0), SYDNEY, KEY) == Triggers(catch_up=True)


def test_an_unchanged_windows_key_triggers_nothing():
    state = SourceState(caught_up_date=datetime.date(2026, 6, 15), windows_key=KEY)
    assert not due(state, at(2026, 6, 15, 0, 0), SYDNEY, KEY).any


def test_a_state_with_no_key_triggers_a_catch_up():
    state = SourceState(caught_up_date=datetime.date(2026, 6, 15), windows_key=None)
    assert due(state, at(2026, 6, 15, 0, 0), SYDNEY, KEY).catch_up


def test_a_catch_up_records_the_windows_key():
    state = after_success(None, Triggers(catch_up=True), at(2026, 6, 15, 0, 0), SYDNEY, "new")
    assert state.windows_key == "new"


def test_a_scheduled_push_keeps_the_old_windows_key():
    before = SourceState(caught_up_date=datetime.date(2026, 6, 15), windows_key="old")
    triggers = Triggers(catch_up=False, scheduled_for=datetime.date(2026, 6, 16))
    state = after_success(before, triggers, at(2026, 6, 15, 13, 59), SYDNEY, "new")
    assert state.windows_key == "old"
