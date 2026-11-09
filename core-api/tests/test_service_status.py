from __future__ import annotations

import datetime

import pytest

from core_api.models.service_status import ServiceStatus
from core_api.service_status import (
    COMMIT_PATTERN,
    REPORT_INTERVAL_SECONDS,
    SERVICES,
    allowed_services_for_key,
    list_statuses,
    record_report,
)
from tests.conftest import ACTOR_KEY, ENVOY_COLLECTOR_KEY, PRICING_COLLECTOR_KEY, WEATHER_COLLECTOR_KEY

NOW = datetime.datetime(2026, 11, 9, 12, 0, tzinfo=datetime.UTC)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (ENVOY_COLLECTOR_KEY, {"envoy-collector"}),
        (WEATHER_COLLECTOR_KEY, {"weatherlink-local-collector"}),
        (PRICING_COLLECTOR_KEY, {"amber-pricing-collector", "local-pricing-collector"}),
        (ACTOR_KEY, {"actron-neo-actor"}),
        ("wrong-key", set()),
        ("kéy-ünicode", set()),
    ],
)
def test_allowed_services_for_key(key, expected):
    assert allowed_services_for_key(key) == frozenset(expected)


def test_constants():
    assert len(SERVICES) == 7
    assert REPORT_INTERVAL_SECONDS == 60
    assert COMMIT_PATTERN.match("abc1234")
    assert COMMIT_PATTERN.match("unknown")
    assert not COMMIT_PATTERN.match("zzz")


def test_record_report_upserts(db):
    record_report(db, "envoy-collector", "aaaaaaa", NOW)
    later = NOW + datetime.timedelta(minutes=1)
    record_report(db, "envoy-collector", "bbbbbbb", later)
    rows = db.query(ServiceStatus).all()
    assert len(rows) == 1
    assert rows[0].commit == "bbbbbbb"
    assert rows[0].reported_at.replace(tzinfo=datetime.UTC) == later


def test_list_statuses_freshness(db):
    record_report(db, SERVICES[0], "aaaaaaa", NOW - datetime.timedelta(seconds=179))
    record_report(db, SERVICES[1], "bbbbbbb", NOW - datetime.timedelta(seconds=181))
    rows = list_statuses(db, NOW)
    assert [r.service for r in rows] == list(SERVICES)
    assert rows[0].reporting is True
    assert rows[1].reporting is False
    assert rows[1].last_reported_at is not None
    assert rows[1].commit == "bbbbbbb"
    never = rows[2]
    assert (never.commit, never.last_reported_at, never.reporting) == (None, None, False)
