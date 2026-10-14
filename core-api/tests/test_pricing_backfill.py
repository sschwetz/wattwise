from __future__ import annotations

import secrets
from datetime import datetime, timezone

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import Site, User
from core_api.models.sources import Source, SourceKind
from tests.conftest import PRICING_COLLECTOR_KEY


def make_pricing_source(db_url: str, config: dict | None = None) -> int:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email=f"pb-owner-{secrets.token_hex(4)}@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=SourceKind.PRICING,
        provider="amber",
        config=config or {"amber_site_id": "example-site-id"},
        effective_from=datetime.now(timezone.utc),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()
    return source_id


def test_create_gap_marker_is_idempotent(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)
    payload = {"channel": "buy", "gap_date": "2025-03-01"}
    headers = {"X-Collector-Key": PRICING_COLLECTOR_KEY}

    first = client.post(f"/api/v1/sources/{source_id}/prices/gap-markers", json=payload, headers=headers)
    assert first.status_code == 204, first.text
    second = client.post(f"/api/v1/sources/{source_id}/prices/gap-markers", json=payload, headers=headers)
    assert second.status_code == 204, second.text

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.pricing import PriceGapMarker

    markers = session.query(PriceGapMarker).all()
    session.close()
    assert len(markers) == 1


def test_create_gap_marker_wrong_key_rejected(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)
    response = client.post(
        f"/api/v1/sources/{source_id}/prices/gap-markers",
        json={"channel": "buy", "gap_date": "2025-03-01"},
        headers={"X-Collector-Key": "wrong"},
    )
    assert response.status_code == 401


def make_energy_source_for_site(db_url: str, site_id: int) -> int:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    source = Source(
        site_id=site_id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        effective_from=datetime.now(timezone.utc),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()
    return source_id


def test_site_data_floor_is_null_when_site_has_no_readings_anywhere(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)
    response = client.get(f"/api/v1/sources/{source_id}/site-data-floor", headers={"X-Collector-Key": PRICING_COLLECTOR_KEY})
    assert response.status_code == 200, response.text
    assert response.json() == {"floor_ts": None}


def test_site_data_floor_is_earliest_reading_across_any_source_kind(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    pricing_source = session.get(Source, source_id)
    energy_source_id = make_energy_source_for_site(db_url, pricing_source.site_id)

    from core_api.models.readings import EnergyMetric, EnergyReading
    from core_api.models.pricing import PriceChannel, PriceReading

    session.add(EnergyReading(
        source_id=energy_source_id,
        ts=datetime(2025, 1, 1, tzinfo=timezone.utc),
        metric=EnergyMetric.PRODUCTION_W,
        value=100.0,
    ))
    session.add(PriceReading(
        pricing_source_id=source_id,
        ts=datetime(2025, 6, 1, tzinfo=timezone.utc),
        channel=PriceChannel.BUY,
        value=25.0,
    ))
    session.commit()
    session.close()

    response = client.get(f"/api/v1/sources/{source_id}/site-data-floor", headers={"X-Collector-Key": PRICING_COLLECTOR_KEY})
    assert response.status_code == 200, response.text
    assert response.json()["floor_ts"].startswith("2025-01-01")


def test_prices_gaps_returns_empty_when_no_intervals_ever_ingested(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)
    response = client.get(
        f"/api/v1/sources/{source_id}/prices/gaps",
        params={"since": "2025-01-01T00:00:00Z"},
        headers={"X-Collector-Key": PRICING_COLLECTOR_KEY},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"gaps": []}


def test_prices_gaps_reports_uncovered_days_for_known_channel(client, app_and_db_url):
    # Uses dates relative to the real "today" (rather than freezing time) so
    # the test stays correct regardless of when the suite runs: day 0 is
    # covered by a real PriceInterval, day 1 is explicitly marked
    # unavailable, and days 2..N (up to today) are genuine gaps.
    from datetime import timedelta

    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)

    today = datetime.now(timezone.utc).date()
    since_date = today - timedelta(days=4)
    covered_day = since_date
    marked_day = since_date + timedelta(days=1)
    first_gap_day = since_date + timedelta(days=2)

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.pricing import PriceChannel, PriceGapMarker, PriceInterval

    covered_start = datetime(covered_day.year, covered_day.month, covered_day.day, tzinfo=timezone.utc)
    session.add(PriceInterval(
        pricing_source_id=source_id,
        channel=PriceChannel.BUY,
        interval_start=covered_start,
        interval_end=covered_start + timedelta(minutes=5),
        price=25,
    ))
    session.add(PriceGapMarker(pricing_source_id=source_id, channel=PriceChannel.BUY, gap_date=marked_day))
    session.commit()
    session.close()

    since_dt = datetime(since_date.year, since_date.month, since_date.day, tzinfo=timezone.utc)
    response = client.get(
        f"/api/v1/sources/{source_id}/prices/gaps",
        params={"since": since_dt.isoformat()},
        headers={"X-Collector-Key": PRICING_COLLECTOR_KEY},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "gaps": [{"channel": "buy", "start_date": first_gap_day.isoformat(), "end_date": today.isoformat()}]
    }


COLLECTOR_HEADERS = {"X-Collector-Key": PRICING_COLLECTOR_KEY}


def _interval(channel: str, start: str, end: str) -> dict:
    return {"channel": channel, "interval_start": start, "interval_end": end, "price": "30"}


def _push_intervals(client, source_id: int, intervals: list[dict]) -> None:
    response = client.post(
        f"/api/v1/sources/{source_id}/prices", json={"intervals": intervals}, headers=COLLECTOR_HEADERS
    )
    assert response.status_code == 202, response.text


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_price_coverage_is_empty_for_a_source_with_no_prices(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)

    response = client.get(f"/api/v1/sources/{source_id}/prices/coverage", headers=COLLECTOR_HEADERS)

    assert response.status_code == 200, response.text
    assert response.json() == {"channels": {}}


def test_price_coverage_gives_the_first_and_last_instant_per_channel(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)
    _push_intervals(
        client,
        source_id,
        [
            _interval("buy", "2026-06-01T10:00:00Z", "2026-06-01T10:05:00Z"),
            _interval("buy", "2026-06-01T10:05:00Z", "2026-06-01T10:10:00Z"),
            _interval("sell", "2026-06-01T09:00:00Z", "2026-06-01T09:05:00Z"),
        ],
    )

    channels = client.get(
        f"/api/v1/sources/{source_id}/prices/coverage", headers=COLLECTOR_HEADERS
    ).json()["channels"]

    assert set(channels) == {"buy", "sell"}
    utc = timezone.utc
    assert _utc(channels["buy"]["first"]) == datetime(2026, 6, 1, 10, 0, tzinfo=utc)
    assert _utc(channels["buy"]["last"]) == datetime(2026, 6, 1, 10, 10, tzinfo=utc)
    assert _utc(channels["sell"]["first"]) == datetime(2026, 6, 1, 9, 0, tzinfo=utc)
    assert _utc(channels["sell"]["last"]) == datetime(2026, 6, 1, 9, 5, tzinfo=utc)


def test_price_coverage_only_counts_the_requested_source(client, app_and_db_url):
    _, db_url = app_and_db_url
    first_source = make_pricing_source(db_url)
    other_source = make_pricing_source(db_url)
    _push_intervals(client, other_source, [_interval("buy", "2026-06-01T10:00:00Z", "2026-06-01T10:05:00Z")])

    response = client.get(f"/api/v1/sources/{first_source}/prices/coverage", headers=COLLECTOR_HEADERS)

    assert response.json() == {"channels": {}}


def test_price_coverage_rejects_a_wrong_key(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)

    response = client.get(
        f"/api/v1/sources/{source_id}/prices/coverage", headers={"X-Collector-Key": "wrong"}
    )

    assert response.status_code == 401


def test_price_coverage_rejects_a_provider_header_for_another_provider(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)

    response = client.get(
        f"/api/v1/sources/{source_id}/prices/coverage",
        headers={**COLLECTOR_HEADERS, "X-Collector-Provider": "local"},
    )

    assert response.status_code == 401
