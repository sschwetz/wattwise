from __future__ import annotations

import datetime
import zoneinfo

from local_pricing_collector.triggers import SourceState, Triggers, after_success, due

UTC = datetime.timezone.utc
SYDNEY = zoneinfo.ZoneInfo("Australia/Sydney")
ADELAIDE = zoneinfo.ZoneInfo("Australia/Adelaide")


def at(*args) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=UTC)


def test_a_source_with_no_state_needs_a_catch_up():
    assert due(None, at(2026, 6, 15, 0, 0), SYDNEY) == Triggers(catch_up=True, scheduled=False)


def test_a_caught_up_source_is_not_due_during_the_day():
    state = SourceState(caught_up_date=datetime.date(2026, 6, 15))
    assert due(state, at(2026, 6, 15, 0, 0), SYDNEY) == Triggers(catch_up=False, scheduled=False)


def test_the_scheduled_push_is_due_at_23_59_local():
    state = SourceState(caught_up_date=datetime.date(2026, 6, 15))
    # 13:59Z is 23:59 in Sydney (UTC+10 in June).
    assert due(state, at(2026, 6, 15, 13, 59), SYDNEY) == Triggers(catch_up=False, scheduled=True)


def test_it_is_not_due_at_23_58_local():
    state = SourceState(caught_up_date=datetime.date(2026, 6, 15))
    assert not due(state, at(2026, 6, 15, 13, 58), SYDNEY).any


def test_the_scheduled_push_is_due_in_a_half_hour_zone():
    state = SourceState(caught_up_date=datetime.date(2026, 6, 15))
    # 14:29Z is 23:59 in Adelaide (UTC+9:30 in June).
    assert due(state, at(2026, 6, 15, 14, 29), ADELAIDE).scheduled
    assert not due(state, at(2026, 6, 15, 14, 28), ADELAIDE).scheduled


def test_the_scheduled_push_stays_due_after_23_59_until_it_succeeds():
    state = SourceState(caught_up_date=datetime.date(2026, 6, 15))
    assert due(state, at(2026, 6, 15, 13, 59, 30), SYDNEY).scheduled
    assert due(state, at(2026, 6, 15, 14, 0), SYDNEY).catch_up  # past local midnight, a new date


def test_the_scheduled_push_runs_once_per_local_date():
    state = SourceState(caught_up_date=datetime.date(2026, 6, 15), scheduled_date=datetime.date(2026, 6, 15))
    assert not due(state, at(2026, 6, 15, 13, 59), SYDNEY).any


def test_a_new_local_date_triggers_a_catch_up():
    state = SourceState(caught_up_date=datetime.date(2026, 6, 15), scheduled_date=datetime.date(2026, 6, 15))
    # 14:01Z is 00:01 on 16 June in Sydney.
    assert due(state, at(2026, 6, 15, 14, 1), SYDNEY) == Triggers(catch_up=True, scheduled=False)


def test_after_a_catch_up_the_local_date_is_recorded():
    state = after_success(None, Triggers(catch_up=True, scheduled=False), at(2026, 6, 15, 0, 0), SYDNEY)
    assert state == SourceState(caught_up_date=datetime.date(2026, 6, 15), scheduled_date=None)


def test_after_a_scheduled_push_only_the_scheduled_date_changes():
    before = SourceState(caught_up_date=datetime.date(2026, 6, 15))
    state = after_success(before, Triggers(catch_up=False, scheduled=True), at(2026, 6, 15, 13, 59), SYDNEY)
    assert state == SourceState(caught_up_date=datetime.date(2026, 6, 15), scheduled_date=datetime.date(2026, 6, 15))


def test_the_recorded_date_is_the_local_date_not_the_utc_date():
    # 20:00Z on 15 June is 06:00 on 16 June in Sydney.
    state = after_success(None, Triggers(catch_up=True, scheduled=False), at(2026, 6, 15, 20, 0), SYDNEY)
    assert state.caught_up_date == datetime.date(2026, 6, 16)
