from __future__ import annotations

import datetime

import pytest

from local_pricing_collector.models import RepriceMarker, parse_reprice

UTC = datetime.timezone.utc


def test_no_marker_in_the_config_is_none():
    assert parse_reprice({"timezone": "UTC", "rate_windows": []}) is None


def test_a_marker_is_parsed_to_aware_utc_and_an_int_version():
    config = {"reprice": {"from": "2026-02-01T00:00:00+00:00", "version": 3}}
    assert parse_reprice(config) == RepriceMarker(datetime.datetime(2026, 2, 1, tzinfo=UTC), 3)


def test_an_offset_less_start_is_read_as_utc():
    config = {"reprice": {"from": "2026-02-01T00:00:00", "version": 1}}
    assert parse_reprice(config).start == datetime.datetime(2026, 2, 1, tzinfo=UTC)


@pytest.mark.parametrize("bad", [{"from": "x", "version": 1}, {"from": "2026-02-01T00:00:00Z"}, "text", {"version": 1}])
def test_a_malformed_marker_raises(bad):
    with pytest.raises((KeyError, TypeError, ValueError)):
        parse_reprice({"reprice": bad})
