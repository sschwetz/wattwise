# Local pricing daily push and backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the local collector's 5-minute, 48-hour push with one push a day at 23:59 site time for the next local day, plus a coverage-driven fill that prices a new source, a restart or an outage, back to the site's first data.

**Architecture:** Core-api gets one read-only endpoint that reports the first and last priced instant per channel. The collector wakes every minute, uses the rate windows and timezone that discovery already returns, and decides per source whether a push is due. A pure planner turns the windows, the coverage, the site data floor and the clock into price ranges. Ranges are cut into 7-day chunks and pushed by a child process that does not block the loop.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy, httpx, pytest, respx.

**Spec:** `docs/superpowers/specs/2026-10-14-local-pricing-daily-push-and-backfill-design.md` (amends `2026-10-13-local-pricing-collector-design.md`)

## Global Constraints

- Run all commands from the `wattwise/` directory (the git root is its parent). Core-api tests: `cd core-api && uv run pytest`. Collector tests: `cd local-pricing-collector && uv run pytest`. Docs tests: `cd webapp && npm test`.
- The scheduled push is at 23:59 on the site's local clock, for the next local calendar day (local midnight to the following local midnight). A literal 24 hours is wrong on the 25-hour clocks-go-back day.
- The fill reaches back to the later of the site data floor and the channel's earliest window `effective_from`, and never past the latest `effective_to` when every window on the channel has one.
- Ranges longer than 7 days are cut into 7-day chunks with a 120-second push timeout.
- A price interval is at most 60 minutes and closes at each top of the UTC hour and wherever the rate changes (unchanged from the first spec). Sell is pushed negative and zero is pushed as `0`.
- `sync_one_source` never raises. A failure is logged and reported by its return value, and the parent retries on the next tick.
- Core-api response timestamps use `UtcDatetime`. `SiteDataFloorOut.floor_ts` is a plain datetime that serialises without an offset, so the collector treats an offset-less floor as UTC.
- `LOCAL_HORIZON_HOURS` is removed. The collector's settings are `CORE_API_URL` and `COLLECTOR_KEY`.
- No `any` in TypeScript or Python annotations. Functions stay under 30 lines. Prose follows `.claude/rules/avoid-ai-writing.md`.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- This environment refuses compound shell commands that mention git. Run git commands as separate plain commands.

## Rulings that tighten the spec (they match the spec's Decisions section)

- The scheduled range is the start of the next local day to the end of the next local day, not "now" to the end. It reprices no minute of the current day.
- The parent process keeps a small in-memory record per source so it does not rebuild a long range every minute. A catch-up runs when the source has not had a successful catch-up on the current local date (this covers a new source, a restart and a day rollover). The scheduled push runs at 23:59 local or later if it has not run on the current local date. A run that fails leaves the record unchanged, so the next tick retries. A restart starts with no record, so it always does a fresh catch-up and nothing is lost.
- The collector uses the config in the discovery response and no longer calls `GET /sources/{id}/config`.
- A child process runs one source's work. The parent does not wait for it, so a long history fill never delays the next minute's checks for other sources. A child is terminated after 2 hours.

## Review Focus

1. A source created a few minutes before 23:59: the catch-up and the scheduled range overlap and merge into one non-repeating range. (Task 4)
2. A channel whose windows cover only some months or days: ranges that build no intervals push nothing and report success, and the throttle stops them rebuilding every minute. (Tasks 4, 5)
3. A half-hour timezone (`Australia/Adelaide`): 23:59 local and local midnights do not fall on a UTC hour. (Tasks 3, 4)
4. A collector that is down across local midnight: the first tick of the new day fills the day it missed. (Tasks 3, 5)
5. A site data floor that arrives with no timezone offset is read as UTC. (Task 2)

## File Structure

Core-api (`core-api/`):
- Modify `core_api/schemas.py`, `core_api/routers/pricing_backfill.py`.
- Tests: add to `tests/test_pricing_backfill.py`.

Collector (`local-pricing-collector/local_pricing_collector/`):
- Create `models.py` (`LocalSource`, `Coverage`), `triggers.py` (`Triggers`, `SourceState`, `due`, `after_success`), `plan.py` (`PlannedRange`, `plan_ranges`, `chunk_range`).
- Modify `config.py`, `core_api_client.py`, `rates.py`, `poll.py`, `scheduling.py` (unchanged code, new use).
- Tests: `tests/test_config.py`, `tests/test_core_api_client.py`, `tests/test_rates.py`, `tests/test_triggers.py` (create), `tests/test_plan.py` (create), `tests/test_poll.py` (rewrite), `tests/test_scheduling.py` (create).

Docs: `webapp/src/docs/04-sources.md`, `README.md`, and the 2026-10-14 spec.

---

### Task 1: Core-api price coverage endpoint

**Files:**
- Modify: `core-api/core_api/schemas.py` (after `SiteDataFloorOut`), `core-api/core_api/routers/pricing_backfill.py`
- Test: `core-api/tests/test_pricing_backfill.py`

**Interfaces:**
- Produces: `GET /api/v1/sources/{source_id}/prices/coverage` returning `{"channels": {"<channel>": {"first": <UTC datetime>, "last": <UTC datetime>}}}`. `first` is the earliest `interval_start` and `last` the latest `interval_end` stored for that source and channel. A channel with no prices is absent. Requires the collector key for the pricing kind and honours `X-Collector-Provider`. Task 2 calls it.

- [ ] **Step 1: Write the failing tests**

Append to `core-api/tests/test_pricing_backfill.py` (the file already imports `datetime`, `timezone`, `make_engine`, `make_session_factory`, `Site`, `User`, `Source`, `SourceKind`, `PRICING_COLLECTOR_KEY` and defines `make_pricing_source`):

```python
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
```

- [ ] **Step 2: Run them to see the failures**

Run: `cd core-api && uv run pytest tests/test_pricing_backfill.py -k price_coverage -v`
Expected: the empty, per-channel, scoped and provider tests FAIL with 404. The wrong-key test passes or fails with 404 depending on routing; it passes after Step 4.

- [ ] **Step 3: Add the schemas**

In `core-api/core_api/schemas.py`, after `SiteDataFloorOut`:

```python
class ChannelCoverageOut(BaseModel):
    first: UtcDatetime
    last: UtcDatetime


class PriceCoverageOut(BaseModel):
    channels: dict[str, ChannelCoverageOut]
```

- [ ] **Step 4: Add the endpoint**

In `core-api/core_api/routers/pricing_backfill.py`, extend the schema import to include `ChannelCoverageOut, PriceCoverageOut`, and add after `get_price_gaps` (before `site_router`):

```python
@router.get("/coverage", response_model=PriceCoverageOut)
def get_price_coverage(
    source: Source = Depends(require_source_kind(SourceKind.PRICING)),
    db: Session = Depends(get_db_session),
) -> PriceCoverageOut:
    """The earliest interval start and the latest interval end stored for each
    channel of this source. A channel with no stored prices is left out. The
    local pricing collector compares this with what its windows imply to find
    what is missing, so it needs instants and not the whole UTC days that
    /gaps reports."""
    rows = db.execute(
        select(PriceInterval.channel, func.min(PriceInterval.interval_start), func.max(PriceInterval.interval_end))
        .where(PriceInterval.pricing_source_id == source.id)
        .group_by(PriceInterval.channel)
    ).all()
    return PriceCoverageOut(
        channels={channel.value: ChannelCoverageOut(first=first, last=last) for channel, first, last in rows}
    )
```

- [ ] **Step 5: Run the tests**

Run: `cd core-api && uv run pytest tests/test_pricing_backfill.py -q`
Expected: all pass. Then run the whole suite once: `cd core-api && uv run pytest -q`. Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A .
git commit -m "feat: add a price coverage endpoint for pricing sources

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Collector client, settings and interval builder

**Files (under `local-pricing-collector/`):**
- Create: `local_pricing_collector/models.py`
- Modify: `local_pricing_collector/config.py`, `local_pricing_collector/core_api_client.py`, `local_pricing_collector/rates.py`
- Test: `tests/test_config.py` (replace), `tests/test_core_api_client.py` (edit), `tests/test_rates.py` (edit)

**Interfaces:**
- Produces:
  - `models.LocalSource(source_id: int, config: dict)` and `models.Coverage(first: datetime, last: datetime)` (frozen dataclasses, UTC-aware datetimes).
  - `config.Settings(core_api_url: str, collector_key: str)`.
  - `core_api_client.fetch_collector_sources(base_url, collector_key) -> list[LocalSource]`, `fetch_site_data_floor(base_url, source_id, collector_key) -> datetime | None`, `fetch_price_coverage(base_url, source_id, collector_key) -> dict[str, Coverage]`. All raise `CoreApiError` on failure or an unexpected body. `fetch_source_config` is removed.
  - `rates.channel_intervals(windows, channel, tz, start, end) -> list[dict]` (public, replaces `_channel_intervals`). `rates.build_intervals` is removed.

- [ ] **Step 1: Create the shared types**

Create `local-pricing-collector/local_pricing_collector/models.py`:

```python
from __future__ import annotations

import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalSource:
    """A local pricing source as discovery returns it: the id and the config
    (timezone and rate windows) core-api resolved for it."""

    source_id: int
    config: dict


@dataclass(frozen=True)
class Coverage:
    """The earliest interval start and latest interval end stored for one
    channel of a source. Both are aware UTC datetimes."""

    first: datetime.datetime
    last: datetime.datetime
```

- [ ] **Step 2: Replace the settings test and code**

Replace `local-pricing-collector/tests/test_config.py`:

```python
from __future__ import annotations

import pytest

from local_pricing_collector.config import get_settings


def test_reads_url_and_key_and_strips_a_trailing_slash(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://core-api:8000/")
    monkeypatch.setenv("COLLECTOR_KEY", "k")

    settings = get_settings()

    assert settings.core_api_url == "http://core-api:8000"
    assert settings.collector_key == "k"


def test_missing_url_raises(monkeypatch):
    monkeypatch.delenv("CORE_API_URL", raising=False)
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    with pytest.raises(RuntimeError):
        get_settings()


def test_missing_key_raises(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://x")
    monkeypatch.delenv("COLLECTOR_KEY", raising=False)
    with pytest.raises(RuntimeError):
        get_settings()
```

Replace `local-pricing-collector/local_pricing_collector/config.py`:

```python
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    collector_key: str


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    collector_key = os.environ.get("COLLECTOR_KEY")
    if not core_api_url or not collector_key:
        raise RuntimeError("CORE_API_URL and COLLECTOR_KEY must both be set")
    return Settings(core_api_url=core_api_url.rstrip("/"), collector_key=collector_key)
```

Run: `cd local-pricing-collector && uv run pytest tests/test_config.py -q`
Expected: pass. (Other test files still import the old names and are fixed in the steps below, so run only this file now.)

- [ ] **Step 3: Write the failing client tests**

In `local-pricing-collector/tests/test_core_api_client.py`:

1. Change the imports to:

```python
import datetime
import json

import httpx
import pytest
import respx

from local_pricing_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_price_coverage,
    fetch_site_data_floor,
    push_price_intervals,
)
from local_pricing_collector.models import Coverage, LocalSource
```

2. Delete every test that calls `fetch_source_config` (its success test, the non-JSON-body test and the missing-`config` test).
3. Replace `test_fetch_collector_sources_asks_for_local_pricing_sources` with the version below, and keep the other `fetch_collector_sources` tests (non-200, connection error, unexpected shape) but make the unexpected-shape test post a list entry that has no `config` key:

```python
@respx.mock
def test_fetch_collector_sources_asks_for_local_pricing_sources_and_returns_their_config():
    config = {"timezone": "UTC", "rate_windows": []}
    route = respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(
        return_value=httpx.Response(200, json=[{"source_id": 4, "site_id": 1, "config": config}])
    )

    assert fetch_collector_sources(BASE_URL, "k") == [LocalSource(4, config)]
    request = route.calls[0].request
    assert request.url.params["kind"] == "pricing"
    assert request.url.params["provider"] == "local"
    assert request.headers["X-Collector-Key"] == "k"
    assert request.headers["X-Collector-Provider"] == "local"


@respx.mock
def test_fetch_collector_sources_raises_when_an_entry_has_no_config():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(
        return_value=httpx.Response(200, json=[{"source_id": 4, "site_id": 1}])
    )
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "k")
```

4. Add:

```python
UTC = datetime.timezone.utc


@respx.mock
def test_fetch_site_data_floor_reads_an_offset_less_timestamp_as_utc():
    """core-api serialises the floor without an offset."""
    respx.get(f"{BASE_URL}/api/v1/sources/4/site-data-floor").mock(
        return_value=httpx.Response(200, json={"floor_ts": "2026-06-01T10:00:00"})
    )
    assert fetch_site_data_floor(BASE_URL, 4, "k") == datetime.datetime(2026, 6, 1, 10, 0, tzinfo=UTC)


@respx.mock
def test_fetch_site_data_floor_keeps_an_explicit_offset():
    respx.get(f"{BASE_URL}/api/v1/sources/4/site-data-floor").mock(
        return_value=httpx.Response(200, json={"floor_ts": "2026-06-01T20:00:00+10:00"})
    )
    assert fetch_site_data_floor(BASE_URL, 4, "k") == datetime.datetime(2026, 6, 1, 10, 0, tzinfo=UTC)


@respx.mock
def test_fetch_site_data_floor_is_none_for_a_site_with_no_data():
    route = respx.get(f"{BASE_URL}/api/v1/sources/4/site-data-floor").mock(
        return_value=httpx.Response(200, json={"floor_ts": None})
    )
    assert fetch_site_data_floor(BASE_URL, 4, "k") is None
    assert route.calls[0].request.headers["X-Collector-Provider"] == "local"


@respx.mock
def test_fetch_site_data_floor_raises_on_a_bad_response():
    respx.get(f"{BASE_URL}/api/v1/sources/4/site-data-floor").mock(return_value=httpx.Response(500, text="no"))
    with pytest.raises(CoreApiError):
        fetch_site_data_floor(BASE_URL, 4, "k")


@respx.mock
def test_fetch_price_coverage_parses_each_channel():
    route = respx.get(f"{BASE_URL}/api/v1/sources/4/prices/coverage").mock(
        return_value=httpx.Response(
            200,
            json={"channels": {"buy": {"first": "2026-06-01T10:00:00Z", "last": "2026-06-16T14:00:00Z"}}},
        )
    )

    assert fetch_price_coverage(BASE_URL, 4, "k") == {
        "buy": Coverage(
            first=datetime.datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            last=datetime.datetime(2026, 6, 16, 14, 0, tzinfo=UTC),
        )
    }
    assert route.calls[0].request.headers["X-Collector-Provider"] == "local"


@respx.mock
def test_fetch_price_coverage_is_empty_for_a_source_with_no_prices():
    respx.get(f"{BASE_URL}/api/v1/sources/4/prices/coverage").mock(
        return_value=httpx.Response(200, json={"channels": {}})
    )
    assert fetch_price_coverage(BASE_URL, 4, "k") == {}


@respx.mock
@pytest.mark.parametrize("body", [{"nope": 1}, {"channels": {"buy": {"first": "x"}}}, {"channels": []}])
def test_fetch_price_coverage_raises_on_an_unexpected_body(body):
    respx.get(f"{BASE_URL}/api/v1/sources/4/prices/coverage").mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(CoreApiError):
        fetch_price_coverage(BASE_URL, 4, "k")
```

Run: `cd local-pricing-collector && uv run pytest tests/test_core_api_client.py -q`
Expected: FAIL (ImportError on `fetch_site_data_floor`).

- [ ] **Step 4: Update the client**

Replace `local-pricing-collector/local_pricing_collector/core_api_client.py`:

```python
from __future__ import annotations

import datetime

import httpx

from local_pricing_collector.models import Coverage, LocalSource

PROVIDER = "local"


class CoreApiError(Exception):
    """Raised on any failure talking to Core API: a non-success response, a
    transport failure or a body of an unexpected shape. Callers only catch
    this one type."""


def _headers(collector_key: str) -> dict[str, str]:
    return {"X-Collector-Key": collector_key, "X-Collector-Provider": PROVIDER}


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    try:
        return httpx.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise CoreApiError(f"Failed to reach Core API: {exc}") from exc


def _parse_utc(value: str) -> datetime.datetime:
    """An instant as aware UTC. core-api sends some timestamps without an
    offset (the site data floor); those are UTC."""
    parsed = datetime.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _get_json(url: str, collector_key: str, what: str):
    response = _request("GET", url, headers=_headers(collector_key), timeout=10)
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch {what}: {response.status_code} {response.text}")
    try:
        return response.json()
    except ValueError as exc:
        raise CoreApiError(f"Unexpected {what} body: {response.text}") from exc


def fetch_collector_sources(base_url: str, collector_key: str) -> list[LocalSource]:
    response = _request(
        "GET",
        f"{base_url}/api/v1/collector/sources",
        params={"kind": "pricing", "provider": PROVIDER},
        headers=_headers(collector_key),
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch collector sources: {response.status_code} {response.text}")
    try:
        return [LocalSource(entry["source_id"], entry["config"]) for entry in response.json()]
    except (ValueError, KeyError, TypeError) as exc:
        raise CoreApiError(f"Unexpected collector sources body: {response.text}") from exc


def fetch_site_data_floor(base_url: str, source_id: int, collector_key: str) -> datetime.datetime | None:
    body = _get_json(f"{base_url}/api/v1/sources/{source_id}/site-data-floor", collector_key, "site data floor")
    try:
        floor_ts = body["floor_ts"]
        return _parse_utc(floor_ts) if floor_ts else None
    except (KeyError, TypeError, ValueError) as exc:
        raise CoreApiError(f"Unexpected site data floor body: {body!r}") from exc


def fetch_price_coverage(base_url: str, source_id: int, collector_key: str) -> dict[str, Coverage]:
    body = _get_json(f"{base_url}/api/v1/sources/{source_id}/prices/coverage", collector_key, "price coverage")
    try:
        return {
            channel: Coverage(first=_parse_utc(entry["first"]), last=_parse_utc(entry["last"]))
            for channel, entry in body["channels"].items()
        }
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CoreApiError(f"Unexpected price coverage body: {body!r}") from exc


def push_price_intervals(
    base_url: str, source_id: int, collector_key: str, intervals: list[dict], *, timeout: float = 120
) -> None:
    """The server writes one row per covered minute, so a 7-day chunk is
    about 10,000 rows per channel. The timeout is longer than the 10 seconds
    used for the small reads."""
    response = _request(
        "POST",
        f"{base_url}/api/v1/sources/{source_id}/prices",
        headers=_headers(collector_key),
        json={"intervals": intervals},
        timeout=timeout,
    )
    if response.status_code != 202:
        raise CoreApiError(f"Failed to push price intervals: {response.status_code} {response.text}")
```

Run: `cd local-pricing-collector && uv run pytest tests/test_core_api_client.py -q`
Expected: pass (the existing push tests still pass; they import `push_price_intervals`, `CoreApiError` and `json`).

- [ ] **Step 5: Make the interval builder take a range**

In `local-pricing-collector/local_pricing_collector/rates.py`, rename `_channel_intervals` to `channel_intervals` and delete `build_intervals` (the function at the end of the file). The docstring of `channel_intervals` becomes:

```python
    """Price intervals for one channel from start to end. Walks minute by
    minute and closes an interval at each top of the UTC hour and wherever the
    rate changes. A minute with no rate closes the interval and opens nothing.
    start and end are whole minutes."""
```

In `local-pricing-collector/tests/test_rates.py`, change the import and add a helper that keeps the old call shape, so the existing tests keep their assertions:

```python
from local_pricing_collector.rates import CHANNELS, channel_intervals, parse_windows, price_at
```

```python
def build_intervals(windows, tz, now, horizon_hours):
    """The old 'from the top of the hour for N hours' shape, over every
    channel that has a window, built on the range function."""
    start = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(hours=horizon_hours)
    return [
        interval
        for channel in CHANNELS
        if any(w.channel == channel for w in windows)
        for interval in channel_intervals(windows, channel, tz, start, end)
    ]
```

Add one test that the range form handles a start that is not on the hour (a half-hour zone's local midnight):

```python
def test_a_range_that_starts_and_ends_mid_hour_is_cut_at_the_hour():
    adelaide = zoneinfo.ZoneInfo("Australia/Adelaide")
    windows = parse_windows([window()])

    intervals = channel_intervals(windows, "buy", adelaide, at(2026, 6, 14, 14, 30), at(2026, 6, 14, 16, 30))

    assert [(i["interval_start"], i["interval_end"]) for i in intervals] == [
        ("2026-06-14T14:30:00+00:00", "2026-06-14T15:00:00+00:00"),
        ("2026-06-14T15:00:00+00:00", "2026-06-14T16:00:00+00:00"),
        ("2026-06-14T16:00:00+00:00", "2026-06-14T16:30:00+00:00"),
    ]
```

Run: `cd local-pricing-collector && uv run pytest tests/test_rates.py tests/test_core_api_client.py tests/test_config.py -q`
Expected: pass. (`tests/test_poll.py` still fails to import until Task 5 rewrites it; do not run the whole suite yet.)

- [ ] **Step 6: Commit**

```bash
git add -A .
git commit -m "refactor: let the local collector fetch coverage and build any range

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Triggers

**Files (under `local-pricing-collector/`):**
- Create: `local_pricing_collector/triggers.py`, `tests/test_triggers.py`, `tests/test_scheduling.py`

**Interfaces:**
- Produces:
  - `Triggers(catch_up: bool, scheduled: bool)` with property `any`.
  - `SourceState(caught_up_date: date | None = None, scheduled_date: date | None = None)`.
  - `due(state: SourceState | None, now: datetime, tz: ZoneInfo) -> Triggers`.
  - `after_success(state: SourceState | None, triggers: Triggers, now: datetime, tz: ZoneInfo) -> SourceState`.
  - `SCHEDULED_TIME = datetime.time(23, 59)`.

- [ ] **Step 1: Write the failing tests**

Create `local-pricing-collector/tests/test_triggers.py`:

```python
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
```

Create `local-pricing-collector/tests/test_scheduling.py`:

```python
from __future__ import annotations

import datetime

from local_pricing_collector.scheduling import next_aligned_tick

UTC = datetime.timezone.utc


def test_the_next_five_minute_boundary_plus_buffer():
    now = datetime.datetime(2026, 6, 15, 10, 31, 0, tzinfo=UTC)
    assert next_aligned_tick(now) == datetime.datetime(2026, 6, 15, 10, 35, 5, tzinfo=UTC)


def test_a_one_minute_interval_gives_the_next_minute_plus_buffer():
    now = datetime.datetime(2026, 6, 15, 10, 31, 20, tzinfo=UTC)
    assert next_aligned_tick(now, interval_minutes=1) == datetime.datetime(2026, 6, 15, 10, 32, 5, tzinfo=UTC)


def test_a_one_minute_interval_before_the_buffer_gives_this_minute():
    now = datetime.datetime(2026, 6, 15, 10, 31, 2, tzinfo=UTC)
    assert next_aligned_tick(now, interval_minutes=1) == datetime.datetime(2026, 6, 15, 10, 31, 5, tzinfo=UTC)


def test_the_tick_is_always_after_now():
    now = datetime.datetime(2026, 6, 15, 10, 35, 5, tzinfo=UTC)
    assert next_aligned_tick(now) > now
```

Run: `cd local-pricing-collector && uv run pytest tests/test_triggers.py tests/test_scheduling.py -q`
Expected: `test_triggers.py` FAILS (ModuleNotFoundError). `test_scheduling.py` passes (it covers the existing function).

- [ ] **Step 2: Implement**

Create `local-pricing-collector/local_pricing_collector/triggers.py`:

```python
from __future__ import annotations

import datetime
import zoneinfo
from dataclasses import dataclass

SCHEDULED_TIME = datetime.time(23, 59)


@dataclass(frozen=True)
class Triggers:
    """Why a source needs work this tick. `catch_up` fills what is missing
    (a new source, a restart, a new local day). `scheduled` is the daily push
    for the next local day."""

    catch_up: bool
    scheduled: bool

    @property
    def any(self) -> bool:
        return self.catch_up or self.scheduled


@dataclass(frozen=True)
class SourceState:
    """What the parent remembers about a source: the local dates on which its
    catch-up and its scheduled push last succeeded. It lives in memory only,
    so a restart starts with none and does a fresh catch-up."""

    caught_up_date: datetime.date | None = None
    scheduled_date: datetime.date | None = None


def due(state: SourceState | None, now: datetime.datetime, tz: zoneinfo.ZoneInfo) -> Triggers:
    local = now.astimezone(tz)
    state = state or SourceState()
    clock = local.time().replace(second=0, microsecond=0)
    return Triggers(
        catch_up=state.caught_up_date != local.date(),
        scheduled=clock >= SCHEDULED_TIME and state.scheduled_date != local.date(),
    )


def after_success(
    state: SourceState | None, triggers: Triggers, now: datetime.datetime, tz: zoneinfo.ZoneInfo
) -> SourceState:
    day = now.astimezone(tz).date()
    state = state or SourceState()
    return SourceState(
        caught_up_date=day if triggers.catch_up else state.caught_up_date,
        scheduled_date=day if triggers.scheduled else state.scheduled_date,
    )
```

- [ ] **Step 3: Run the tests**

Run: `cd local-pricing-collector && uv run pytest tests/test_triggers.py tests/test_scheduling.py -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add -A .
git commit -m "feat: add the daily and catch-up triggers for the local collector

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Planner

**Files (under `local-pricing-collector/`):**
- Create: `local_pricing_collector/plan.py`, `tests/test_plan.py`

**Interfaces:**
- Consumes: `rates.RateWindow`, `rates.CHANNELS`, `models.Coverage`, `triggers.Triggers`.
- Produces:
  - `PlannedRange(channel: str, start: datetime, end: datetime)` (frozen).
  - `plan_ranges(windows: list[RateWindow], coverage: dict[str, Coverage], floor: datetime | None, tz: ZoneInfo, now: datetime, triggers: Triggers) -> list[PlannedRange]`
  - `chunk_range(planned: PlannedRange, days: int = 7) -> list[PlannedRange]`
  - `local_midnight(day: date, tz: ZoneInfo) -> datetime` (UTC-aware).

- [ ] **Step 1: Write the failing tests**

Create `local-pricing-collector/tests/test_plan.py`:

```python
from __future__ import annotations

import datetime
import zoneinfo

from local_pricing_collector.models import Coverage
from local_pricing_collector.plan import PlannedRange, chunk_range, local_midnight, plan_ranges
from local_pricing_collector.rates import parse_windows
from local_pricing_collector.triggers import Triggers

UTC = datetime.timezone.utc
SYDNEY = zoneinfo.ZoneInfo("Australia/Sydney")
ADELAIDE = zoneinfo.ZoneInfo("Australia/Adelaide")
CATCH_UP = Triggers(catch_up=True, scheduled=False)
SCHEDULED = Triggers(catch_up=False, scheduled=True)
BOTH = Triggers(catch_up=True, scheduled=True)


def at(*args) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=UTC)


def window(**overrides) -> dict:
    base = {
        "channel": "buy",
        "effective_from": "2026-01-01T00:00:00+00:00",
        "effective_to": None,
        "months": list(range(1, 13)),
        "days_of_week": list(range(7)),
        "start_time": "00:00:00",
        "end_time": "00:00:00",
        "rate_c_per_kwh": "30.0000",
    }
    return {**base, **overrides}


def plan(windows, coverage=None, floor=None, tz=SYDNEY, now=at(2026, 6, 15, 0, 0), triggers=CATCH_UP):
    return plan_ranges(parse_windows(windows), coverage or {}, floor, tz, now, triggers)


# now = 2026-06-15T00:00Z = 10:00 in Sydney. Local midnights: today ends 2026-06-15T14:00Z,
# tomorrow ends 2026-06-16T14:00Z.
TODAY_END = at(2026, 6, 15, 14, 0)
TOMORROW_END = at(2026, 6, 16, 14, 0)


def test_local_midnight_is_an_aware_utc_instant():
    assert local_midnight(datetime.date(2026, 6, 16), SYDNEY) == at(2026, 6, 15, 14, 0)
    assert local_midnight(datetime.date(2026, 6, 16), ADELAIDE) == at(2026, 6, 15, 14, 30)


def test_a_new_source_is_filled_from_the_site_data_floor_to_the_end_of_tomorrow():
    ranges = plan([window()], floor=at(2026, 6, 1, 0, 0))
    assert ranges == [PlannedRange("buy", at(2026, 6, 1, 0, 0), TOMORROW_END)]


def test_the_fill_starts_at_the_window_start_when_that_is_after_the_floor():
    ranges = plan([window(effective_from="2026-06-10T00:00:00+00:00")], floor=at(2026, 6, 1, 0, 0))
    assert ranges == [PlannedRange("buy", at(2026, 6, 10, 0, 0), TOMORROW_END)]


def test_the_fill_starts_at_the_floor_when_the_window_started_earlier():
    ranges = plan([window(effective_from="2025-01-01T00:00:00+00:00")], floor=at(2026, 6, 1, 0, 0))
    assert ranges[0].start == at(2026, 6, 1, 0, 0)


def test_a_new_source_on_a_site_with_no_data_starts_now():
    ranges = plan([window()], floor=None, now=at(2026, 6, 15, 0, 0, 40))
    assert ranges == [PlannedRange("buy", at(2026, 6, 15, 0, 0), TOMORROW_END)]


def test_a_source_covered_to_the_end_of_today_needs_nothing_on_a_catch_up():
    coverage = {"buy": Coverage(first=at(2026, 6, 1, 0, 0), last=TODAY_END)}
    assert plan([window()], coverage, floor=at(2026, 6, 1, 0, 0)) == []


def test_an_outage_is_filled_from_the_last_priced_instant():
    coverage = {"buy": Coverage(first=at(2026, 6, 1, 0, 0), last=at(2026, 6, 12, 14, 0))}
    ranges = plan([window()], coverage, floor=at(2026, 6, 1, 0, 0))
    assert ranges == [PlannedRange("buy", at(2026, 6, 12, 14, 0), TOMORROW_END)]


def test_history_before_the_first_priced_instant_is_filled():
    coverage = {"buy": Coverage(first=at(2026, 6, 10, 0, 0), last=TOMORROW_END)}
    ranges = plan([window()], coverage, floor=at(2026, 6, 1, 0, 0))
    assert ranges == [PlannedRange("buy", at(2026, 6, 1, 0, 0), at(2026, 6, 10, 0, 0))]


def test_history_and_a_forward_gap_are_both_planned():
    coverage = {"buy": Coverage(first=at(2026, 6, 10, 0, 0), last=at(2026, 6, 14, 0, 0))}
    ranges = plan([window()], coverage, floor=at(2026, 6, 1, 0, 0))
    assert ranges == [
        PlannedRange("buy", at(2026, 6, 1, 0, 0), at(2026, 6, 10, 0, 0)),
        PlannedRange("buy", at(2026, 6, 14, 0, 0), TOMORROW_END),
    ]


def test_a_direction_added_later_is_filled_without_touching_the_others():
    windows = [window(), window(channel="sell")]
    coverage = {"buy": Coverage(first=at(2026, 6, 1, 0, 0), last=TOMORROW_END)}
    ranges = plan(windows, coverage, floor=at(2026, 6, 1, 0, 0))
    assert ranges == [PlannedRange("sell", at(2026, 6, 1, 0, 0), TOMORROW_END)]


def test_a_direction_without_windows_is_never_planned():
    assert {r.channel for r in plan([window()], floor=at(2026, 6, 1, 0, 0))} == {"buy"}


def test_ended_windows_are_not_planned_past_their_end():
    ended = window(effective_to="2026-03-01T00:00:00+00:00")
    coverage = {"buy": Coverage(first=at(2026, 1, 1, 0, 0), last=at(2026, 3, 1, 0, 0))}
    assert plan([ended], coverage, floor=at(2026, 1, 1, 0, 0)) == []


def test_a_range_is_cut_at_the_latest_end_when_every_window_has_one():
    ended = window(effective_to="2026-06-14T00:00:00+00:00")
    ranges = plan([ended], floor=at(2026, 6, 1, 0, 0))
    assert ranges == [PlannedRange("buy", at(2026, 6, 1, 0, 0), at(2026, 6, 14, 0, 0))]


def test_one_open_ended_window_removes_the_end_limit():
    windows = [window(effective_to="2026-06-14T00:00:00+00:00"), window(effective_from="2026-06-14T00:00:00+00:00")]
    ranges = plan(windows, floor=at(2026, 6, 1, 0, 0))
    assert ranges[-1].end == TOMORROW_END


def test_a_window_that_has_not_started_is_not_planned_before_it_starts():
    future = window(effective_from="2026-06-20T00:00:00+00:00")
    assert plan([future], floor=at(2026, 6, 1, 0, 0)) == []


def test_the_scheduled_push_is_the_whole_next_local_day():
    ranges = plan([window()], now=at(2026, 6, 15, 13, 59), triggers=SCHEDULED)
    assert ranges == [PlannedRange("buy", at(2026, 6, 15, 14, 0), at(2026, 6, 16, 14, 0))]


def test_the_scheduled_push_before_the_clocks_go_back_is_25_hours_long():
    """Sydney leaves daylight saving at 03:00 on 4 April 2027, so that local day has 25 hours."""
    now = at(2027, 4, 3, 12, 59)  # 23:59 on 3 April in Sydney (UTC+11)
    ranges = plan([window()], now=now, triggers=SCHEDULED)
    assert len(ranges) == 1
    assert ranges[0].end - ranges[0].start == datetime.timedelta(hours=25)
    assert ranges[0].start == at(2027, 4, 3, 13, 0)
    assert ranges[0].end == at(2027, 4, 4, 14, 0)


def test_the_scheduled_push_in_a_half_hour_zone_starts_at_local_midnight():
    now = at(2026, 6, 15, 14, 29)  # 23:59 in Adelaide
    ranges = plan([window()], now=now, tz=ADELAIDE, triggers=SCHEDULED)
    assert ranges == [PlannedRange("buy", at(2026, 6, 15, 14, 30), at(2026, 6, 16, 14, 30))]


def test_a_catch_up_and_a_scheduled_push_together_become_one_range():
    now = at(2026, 6, 15, 13, 59)
    ranges = plan([window()], floor=at(2026, 6, 15, 0, 0), now=now, triggers=BOTH)
    assert ranges == [PlannedRange("buy", at(2026, 6, 15, 0, 0), at(2026, 6, 16, 14, 0))]


def test_a_scheduled_push_overwrites_tomorrow_even_when_it_is_already_covered():
    coverage = {"buy": Coverage(first=at(2026, 6, 1, 0, 0), last=TOMORROW_END)}
    ranges = plan([window()], coverage, now=at(2026, 6, 15, 13, 59), triggers=SCHEDULED)
    assert ranges == [PlannedRange("buy", TODAY_END, TOMORROW_END)]


def test_the_chunks_of_a_long_range_are_seven_days_or_less_and_join_up():
    planned = PlannedRange("buy", at(2026, 5, 1, 0, 0), at(2026, 6, 16, 14, 0))

    chunks = chunk_range(planned)

    assert len(chunks) == 7
    assert chunks[0].start == planned.start
    assert chunks[-1].end == planned.end
    assert all(c.end - c.start <= datetime.timedelta(days=7) for c in chunks)
    assert all(a.end == b.start for a, b in zip(chunks, chunks[1:]))


def test_a_short_range_is_one_chunk():
    planned = PlannedRange("buy", at(2026, 6, 15, 0, 0), at(2026, 6, 16, 0, 0))
    assert chunk_range(planned) == [planned]
```

Run: `cd local-pricing-collector && uv run pytest tests/test_plan.py -q`
Expected: FAIL (ModuleNotFoundError: `local_pricing_collector.plan`).

- [ ] **Step 2: Implement**

Create `local-pricing-collector/local_pricing_collector/plan.py`:

```python
from __future__ import annotations

import datetime
import zoneinfo
from dataclasses import dataclass

from local_pricing_collector.models import Coverage
from local_pricing_collector.rates import CHANNELS, RateWindow
from local_pricing_collector.triggers import Triggers

UTC = datetime.timezone.utc
ONE_DAY = datetime.timedelta(days=1)
CHUNK_DAYS = 7


@dataclass(frozen=True)
class PlannedRange:
    channel: str
    start: datetime.datetime
    end: datetime.datetime


def local_midnight(day: datetime.date, tz: zoneinfo.ZoneInfo) -> datetime.datetime:
    """The instant a local calendar day begins, as aware UTC."""
    return datetime.datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(UTC)


def _whole_minute(value: datetime.datetime) -> datetime.datetime:
    return value.replace(second=0, microsecond=0)


def _day_edges(now: datetime.datetime, tz: zoneinfo.ZoneInfo) -> tuple[datetime.datetime, datetime.datetime]:
    """The end of the current local day and the end of the next one."""
    today = now.astimezone(tz).date()
    return local_midnight(today + ONE_DAY, tz), local_midnight(today + 2 * ONE_DAY, tz)


def _history_start(
    windows: list[RateWindow], floor: datetime.datetime | None, now: datetime.datetime
) -> datetime.datetime:
    """Where this channel's history begins: the site's first data, or now when
    the site has none, and never before the channel's first window."""
    earliest = min(w.effective_from for w in windows)
    return max(_whole_minute(floor) if floor is not None else _whole_minute(now), earliest)


def _end_limit(windows: list[RateWindow]) -> datetime.datetime | None:
    """The latest end among the windows, or None when any window is open ended."""
    if any(w.effective_to is None for w in windows):
        return None
    return max(w.effective_to for w in windows)


def _wanted_ranges(
    coverage: Coverage | None,
    history_start: datetime.datetime,
    today_end: datetime.datetime,
    tomorrow_end: datetime.datetime,
    triggers: Triggers,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    ranges = []
    if triggers.catch_up:
        if coverage is None:
            ranges.append((history_start, tomorrow_end))
        else:
            if coverage.first > history_start:
                ranges.append((history_start, coverage.first))
            if coverage.last < today_end:
                ranges.append((coverage.last, tomorrow_end))
    if triggers.scheduled:
        ranges.append((today_end, tomorrow_end))
    return ranges


def _clamp_and_merge(
    ranges: list[tuple[datetime.datetime, datetime.datetime]],
    low: datetime.datetime,
    high: datetime.datetime | None,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    clamped = sorted((max(start, low), end if high is None else min(end, high)) for start, end in ranges)
    merged: list[tuple[datetime.datetime, datetime.datetime]] = []
    for start, end in clamped:
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def plan_ranges(
    windows: list[RateWindow],
    coverage: dict[str, Coverage],
    floor: datetime.datetime | None,
    tz: zoneinfo.ZoneInfo,
    now: datetime.datetime,
    triggers: Triggers,
) -> list[PlannedRange]:
    """What to price this tick, per channel. Ranges are limited to when the
    channel's windows were in effect, so a source whose windows have all ended
    does not rebuild empty history."""
    today_end, tomorrow_end = _day_edges(now, tz)
    planned = []
    for channel in CHANNELS:
        channel_windows = [w for w in windows if w.channel == channel]
        if not channel_windows:
            continue
        wanted = _wanted_ranges(
            coverage.get(channel), _history_start(channel_windows, floor, now), today_end, tomorrow_end, triggers
        )
        earliest = min(w.effective_from for w in channel_windows)
        for start, end in _clamp_and_merge(wanted, earliest, _end_limit(channel_windows)):
            planned.append(PlannedRange(channel, start, end))
    return planned


def chunk_range(planned: PlannedRange, days: int = CHUNK_DAYS) -> list[PlannedRange]:
    """Cuts a range into pieces of at most `days` days, one request each."""
    step = datetime.timedelta(days=days)
    chunks = []
    start = planned.start
    while start < planned.end:
        end = min(start + step, planned.end)
        chunks.append(PlannedRange(planned.channel, start, end))
        start = end
    return chunks
```

- [ ] **Step 3: Run the tests**

Run: `cd local-pricing-collector && uv run pytest tests/test_plan.py -q`
Expected: all pass. If `test_the_scheduled_push_before_the_clocks_go_back_is_25_hours_long` fails, print the two instants before touching code: Sydney leaves daylight saving at 03:00 AEDT on the first Sunday of April, which in 2027 is 4 April, so 23:59 on 3 April is UTC+11 and the next local day runs from 13:00Z on 3 April to 14:00Z on 4 April.

- [ ] **Step 4: Commit**

```bash
git add -A .
git commit -m "feat: plan which price ranges the local collector fills

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Loop and sync

**Files (under `local-pricing-collector/`):**
- Modify: `local_pricing_collector/poll.py` (replace)
- Test: `tests/test_poll.py` (replace)

**Interfaces:**
- Consumes: everything from Tasks 2-4.
- Produces:
  - `poll.site_timezone(config: dict) -> ZoneInfo` (raises `KeyError`, `TypeError`, `ZoneInfoNotFoundError` or `ValueError` on a bad config).
  - `poll.sync_one_source(settings, source: LocalSource, triggers: Triggers, now: datetime) -> bool` (never raises; `True` when everything planned was pushed or nothing was needed).
  - `poll.sync_child(settings, source, triggers, now) -> None` (exits 0 on success, 1 on failure).
  - `poll.TickState`, `poll.run_tick(settings, tick, now, process_factory=multiprocessing.Process) -> None`, `poll.run_forever(settings) -> None`.
  - Constants `PUSH_TIMEOUT_SECONDS = 120`, `MAX_RUN = timedelta(hours=2)`.

- [ ] **Step 1: Write the failing tests**

Replace `local-pricing-collector/tests/test_poll.py`:

```python
from __future__ import annotations

import datetime

import pytest

from local_pricing_collector import poll
from local_pricing_collector.config import Settings
from local_pricing_collector.core_api_client import CoreApiError
from local_pricing_collector.models import Coverage, LocalSource
from local_pricing_collector.triggers import SourceState, Triggers

UTC = datetime.timezone.utc
SETTINGS = Settings(core_api_url="http://core-api:8000", collector_key="k")
# 00:00Z on 15 June is 10:00 in Sydney. Local midnights: 2026-06-15T14:00Z and 2026-06-16T14:00Z.
NOW = datetime.datetime(2026, 6, 15, 0, 0, tzinfo=UTC)
AT_2359 = datetime.datetime(2026, 6, 15, 13, 59, tzinfo=UTC)
CATCH_UP = Triggers(catch_up=True, scheduled=False)
SCHEDULED = Triggers(catch_up=False, scheduled=True)
FLAT_BUY = {
    "channel": "buy",
    "effective_from": "2026-01-01T00:00:00+00:00",
    "effective_to": None,
    "months": list(range(1, 13)),
    "days_of_week": list(range(7)),
    "start_time": "00:00:00",
    "end_time": "00:00:00",
    "rate_c_per_kwh": "30.0000",
}


def source(windows=None, timezone="Australia/Sydney", source_id=7) -> LocalSource:
    return LocalSource(source_id, {"timezone": timezone, "rate_windows": [FLAT_BUY] if windows is None else windows})


class FakeApi:
    """Stands in for the three core-api calls the sync makes."""

    def __init__(self, coverage=None, floor=None, fail_push_after=None):
        self.coverage = coverage or {}
        self.floor = floor
        self.fail_push_after = fail_push_after
        self.coverage_calls = 0
        self.pushes = []

    def fetch_price_coverage(self, url, source_id, key):
        self.coverage_calls += 1
        return self.coverage

    def fetch_site_data_floor(self, url, source_id, key):
        return self.floor

    def push_price_intervals(self, url, source_id, key, intervals, **kwargs):
        if self.fail_push_after is not None and len(self.pushes) >= self.fail_push_after:
            raise CoreApiError("down")
        self.pushes.append((source_id, intervals))

    def install(self, monkeypatch):
        for name in ("fetch_price_coverage", "fetch_site_data_floor", "push_price_intervals"):
            monkeypatch.setattr(poll, name, getattr(self, name))
        return self


def test_a_new_source_is_filled_from_the_floor_to_the_end_of_tomorrow(monkeypatch):
    api = FakeApi(floor=datetime.datetime(2026, 6, 14, 0, 0, tzinfo=UTC)).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), CATCH_UP, NOW) is True

    assert len(api.pushes) == 1
    source_id, intervals = api.pushes[0]
    assert source_id == 7
    assert {i["channel"] for i in intervals} == {"buy"}
    assert intervals[0]["interval_start"] == "2026-06-14T00:00:00+00:00"
    assert intervals[-1]["interval_end"] == "2026-06-16T14:00:00+00:00"
    assert len(intervals) == 62


def test_the_scheduled_push_prices_only_the_next_local_day_and_asks_for_no_coverage(monkeypatch):
    api = FakeApi().install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), SCHEDULED, AT_2359) is True

    assert api.coverage_calls == 0
    intervals = api.pushes[0][1]
    assert intervals[0]["interval_start"] == "2026-06-15T14:00:00+00:00"
    assert intervals[-1]["interval_end"] == "2026-06-16T14:00:00+00:00"
    assert len(intervals) == 24


def test_a_source_that_is_fully_covered_pushes_nothing(monkeypatch):
    covered = {
        "buy": Coverage(
            first=datetime.datetime(2026, 6, 1, tzinfo=UTC), last=datetime.datetime(2026, 6, 15, 14, 0, tzinfo=UTC)
        )
    }
    api = FakeApi(coverage=covered, floor=datetime.datetime(2026, 6, 1, tzinfo=UTC)).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), CATCH_UP, NOW) is True
    assert api.pushes == []


def test_a_source_with_no_windows_makes_no_requests(monkeypatch):
    api = FakeApi().install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(windows=[]), CATCH_UP, NOW) is True
    assert api.coverage_calls == 0
    assert api.pushes == []


def test_a_long_range_is_pushed_in_seven_day_chunks(monkeypatch):
    api = FakeApi(floor=datetime.datetime(2026, 5, 1, tzinfo=UTC)).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), CATCH_UP, NOW) is True

    assert len(api.pushes) == 7


def test_a_failed_chunk_stops_the_rest_and_reports_failure(monkeypatch, caplog):
    api = FakeApi(floor=datetime.datetime(2026, 5, 1, tzinfo=UTC), fail_push_after=1).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), CATCH_UP, NOW) is False

    assert len(api.pushes) == 1
    assert "will retry" in caplog.text


def test_a_coverage_failure_is_logged_and_reported(monkeypatch, caplog):
    def boom(url, source_id, key):
        raise CoreApiError("down")

    FakeApi().install(monkeypatch)
    monkeypatch.setattr(poll, "fetch_price_coverage", boom)

    assert poll.sync_one_source(SETTINGS, source(), CATCH_UP, NOW) is False
    assert "will retry" in caplog.text


def test_an_unknown_time_zone_is_logged_and_reported(monkeypatch, caplog):
    FakeApi().install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(timezone="Mars/Base"), CATCH_UP, NOW) is False
    assert "Failed to read config" in caplog.text


def test_an_unexpected_error_is_logged_and_does_not_escape(monkeypatch, caplog):
    FakeApi().install(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(poll, "plan_ranges", boom)

    assert poll.sync_one_source(SETTINGS, source(), CATCH_UP, NOW) is False
    assert "Unexpected error" in caplog.text


def test_a_range_with_no_matching_window_pushes_nothing_and_succeeds(monkeypatch):
    december_only = dict(FLAT_BUY, months=[12])
    api = FakeApi(floor=datetime.datetime(2026, 6, 14, tzinfo=UTC)).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(windows=[december_only]), CATCH_UP, NOW) is True
    assert api.pushes == []


class FakeProcess:
    instances: list["FakeProcess"] = []

    def __init__(self, target, args):
        self.target = target
        self.args = args
        self.started = False
        self.alive = True
        self.exitcode = None
        self.terminated = False
        FakeProcess.instances.append(self)

    def start(self):
        self.started = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        pass

    def terminate(self):
        self.terminated = True
        self.alive = False
        self.exitcode = -15

    def finish(self, exitcode=0):
        self.alive = False
        self.exitcode = exitcode


@pytest.fixture(autouse=True)
def _reset_fake_processes():
    FakeProcess.instances = []


def fake_discovery(monkeypatch, sources):
    monkeypatch.setattr(poll, "fetch_collector_sources", lambda url, key: sources)


def tick(state, now=NOW):
    poll.run_tick(SETTINGS, state, now, process_factory=FakeProcess)


def test_a_new_source_gets_a_catch_up_child(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState()

    tick(state)

    assert len(FakeProcess.instances) == 1
    child = FakeProcess.instances[0]
    assert child.started
    assert child.args[2] == CATCH_UP
    assert child.args[1].source_id == 7


def test_a_source_that_is_not_due_gets_no_child(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState(states={7: SourceState(caught_up_date=datetime.date(2026, 6, 15))})

    tick(state)

    assert FakeProcess.instances == []


def test_the_scheduled_push_starts_a_child_at_23_59_local(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState(states={7: SourceState(caught_up_date=datetime.date(2026, 6, 15))})

    tick(state, now=AT_2359)

    assert FakeProcess.instances[0].args[2] == SCHEDULED


def test_a_running_child_is_not_started_twice(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState()

    tick(state)
    tick(state)

    assert len(FakeProcess.instances) == 1


def test_a_successful_child_is_recorded_so_the_next_tick_starts_nothing(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState()
    tick(state)
    FakeProcess.instances[0].finish(0)

    tick(state)

    assert len(FakeProcess.instances) == 1
    assert state.states[7].caught_up_date == datetime.date(2026, 6, 15)
    assert state.running == {}


def test_a_failed_child_is_retried_on_the_next_tick(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState()
    tick(state)
    FakeProcess.instances[0].finish(1)

    tick(state)

    assert len(FakeProcess.instances) == 2
    assert 7 not in state.states


def test_a_child_that_runs_too_long_is_terminated_and_retried(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState()
    tick(state)

    tick(state, now=NOW + poll.MAX_RUN + datetime.timedelta(minutes=1))

    assert FakeProcess.instances[0].terminated
    assert 7 not in state.states
    assert len(FakeProcess.instances) == 2


def test_state_is_forgotten_for_a_source_that_disappears(monkeypatch):
    fake_discovery(monkeypatch, [])
    state = poll.TickState(states={7: SourceState(caught_up_date=datetime.date(2026, 6, 15))})

    tick(state)

    assert state.states == {}


def test_a_discovery_failure_is_logged_and_starts_nothing(monkeypatch, caplog):
    def boom(url, key):
        raise CoreApiError("down")

    monkeypatch.setattr(poll, "fetch_collector_sources", boom)

    tick(poll.TickState())

    assert "Failed to refresh" in caplog.text
    assert FakeProcess.instances == []


def test_a_source_with_a_bad_time_zone_is_skipped_and_the_others_still_run(monkeypatch, caplog):
    fake_discovery(monkeypatch, [source(timezone="Mars/Base", source_id=1), source(source_id=2)])

    tick(poll.TickState())

    assert [p.args[1].source_id for p in FakeProcess.instances] == [2]
    assert "time zone" in caplog.text
```

Run: `cd local-pricing-collector && uv run pytest tests/test_poll.py -q`
Expected: FAIL (AttributeError/ImportError: `poll.TickState` and the new names do not exist).

- [ ] **Step 2: Implement**

Replace `local-pricing-collector/local_pricing_collector/poll.py`:

```python
from __future__ import annotations

import datetime
import logging
import multiprocessing
import multiprocessing.process
import sys
import time
import zoneinfo
from collections.abc import Callable
from dataclasses import dataclass, field

from local_pricing_collector.config import Settings
from local_pricing_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_price_coverage,
    fetch_site_data_floor,
    push_price_intervals,
)
from local_pricing_collector.models import Coverage, LocalSource
from local_pricing_collector.plan import PlannedRange, chunk_range, plan_ranges
from local_pricing_collector.rates import RateWindow, channel_intervals, parse_windows
from local_pricing_collector.scheduling import next_aligned_tick
from local_pricing_collector.triggers import SourceState, Triggers, after_success, due

logger = logging.getLogger("local_pricing_collector")

PUSH_TIMEOUT_SECONDS = 120
MAX_RUN = datetime.timedelta(hours=2)
CONFIG_ERRORS = (KeyError, TypeError, zoneinfo.ZoneInfoNotFoundError, ValueError)


def site_timezone(config: dict) -> zoneinfo.ZoneInfo:
    return zoneinfo.ZoneInfo(config["timezone"])


def _coverage_and_floor(
    settings: Settings, source_id: int, triggers: Triggers
) -> tuple[dict[str, Coverage], datetime.datetime | None]:
    """Only a catch-up needs to know what is already priced. The scheduled
    push is the next local day whatever is stored."""
    if not triggers.catch_up:
        return {}, None
    url, key = settings.core_api_url, settings.collector_key
    return fetch_price_coverage(url, source_id, key), fetch_site_data_floor(url, source_id, key)


def _push_range(
    settings: Settings, source_id: int, windows: list[RateWindow], tz: zoneinfo.ZoneInfo, planned: PlannedRange
) -> None:
    for chunk in chunk_range(planned):
        intervals = channel_intervals(windows, chunk.channel, tz, chunk.start, chunk.end)
        if intervals:
            push_price_intervals(
                settings.core_api_url, source_id, settings.collector_key, intervals, timeout=PUSH_TIMEOUT_SECONDS
            )


def _sync(settings: Settings, source: LocalSource, triggers: Triggers, now: datetime.datetime) -> bool:
    try:
        tz = site_timezone(source.config)
        windows = parse_windows(source.config["rate_windows"])
    except CONFIG_ERRORS:
        logger.exception("Failed to read config for pricing source %s this cycle", source.source_id)
        return False
    if not windows:
        return True
    try:
        coverage, floor = _coverage_and_floor(settings, source.source_id, triggers)
        for planned in plan_ranges(windows, coverage, floor, tz, now, triggers):
            _push_range(settings, source.source_id, windows, tz, planned)
    except CoreApiError:
        logger.exception("Failed to price source %s this cycle; will retry", source.source_id)
        return False
    return True


def sync_one_source(settings: Settings, source: LocalSource, triggers: Triggers, now: datetime.datetime) -> bool:
    """Prices what the triggers call for. Never raises, so one source's
    failure cannot stop the others. Returns True when everything planned was
    pushed (or nothing was needed) and False when the parent should retry."""
    try:
        return _sync(settings, source, triggers, now)
    except Exception:
        logger.exception("Unexpected error syncing pricing source %s this cycle", source.source_id)
        return False


def sync_child(settings: Settings, source: LocalSource, triggers: Triggers, now: datetime.datetime) -> None:
    """Process entry point. The exit code tells the parent whether to record
    the work as done."""
    sys.exit(0 if sync_one_source(settings, source, triggers, now) else 1)


@dataclass
class RunningJob:
    process: multiprocessing.process.BaseProcess
    triggers: Triggers
    tz: zoneinfo.ZoneInfo
    started: datetime.datetime


@dataclass
class TickState:
    """What the parent loop remembers between ticks. In memory only: after a
    restart every source starts with no record and does a catch-up."""

    states: dict[int, SourceState] = field(default_factory=dict)
    running: dict[int, RunningJob] = field(default_factory=dict)


def _finish(tick: TickState, source_id: int, job: RunningJob) -> None:
    job.process.join()
    if job.process.exitcode == 0:
        tick.states[source_id] = after_success(tick.states.get(source_id), job.triggers, job.started, job.tz)
    else:
        logger.warning("Pricing source %s did not finish its work (exit %s); retrying", source_id, job.process.exitcode)


def _reap(tick: TickState, now: datetime.datetime) -> None:
    for source_id, job in list(tick.running.items()):
        if job.process.is_alive():
            if now - job.started <= MAX_RUN:
                continue
            logger.error("Process for local pricing source %s ran too long; terminating", source_id)
            job.process.terminate()
            job.process.join()
        else:
            _finish(tick, source_id, job)
        del tick.running[source_id]


def _start_if_due(
    settings: Settings,
    tick: TickState,
    source: LocalSource,
    now: datetime.datetime,
    process_factory: Callable[..., multiprocessing.process.BaseProcess],
) -> None:
    if source.source_id in tick.running:
        return
    try:
        tz = site_timezone(source.config)
    except CONFIG_ERRORS:
        logger.exception("Unusable time zone for pricing source %s; skipping it this tick", source.source_id)
        return
    triggers = due(tick.states.get(source.source_id), now, tz)
    if not triggers.any:
        return
    process = process_factory(target=sync_child, args=(settings, source, triggers, now))
    process.start()
    tick.running[source.source_id] = RunningJob(process, triggers, tz, now)


def run_tick(
    settings: Settings,
    tick: TickState,
    now: datetime.datetime,
    process_factory: Callable[..., multiprocessing.process.BaseProcess] = multiprocessing.Process,
) -> None:
    """One pass of the loop. Collects finished children, then starts a child
    for each source whose work is due. It never waits for a child, so a long
    history fill does not delay the next minute's checks."""
    _reap(tick, now)
    try:
        sources = fetch_collector_sources(settings.core_api_url, settings.collector_key)
    except CoreApiError:
        logger.exception("Failed to refresh the local pricing source list this tick; skipping")
        return
    live = {source.source_id for source in sources}
    for source_id in [sid for sid in tick.states if sid not in live]:
        del tick.states[source_id]
    for source in sources:
        _start_if_due(settings, tick, source, now, process_factory)


def run_forever(settings: Settings) -> None:
    """Wakes every minute. This process stays single-threaded so the forks
    are safe."""
    tick = TickState()
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            time.sleep(max((next_aligned_tick(now, interval_minutes=1) - now).total_seconds(), 0))
            run_tick(settings, tick, datetime.datetime.now(datetime.timezone.utc))
        except Exception:
            logger.exception("Unexpected error in the local pricing loop; continuing")
```

- [ ] **Step 3: Run the tests**

Run: `cd local-pricing-collector && uv run pytest tests/test_poll.py -q`
Expected: all pass. Fix the code, not the tests, if one fails, unless the failure is arithmetic in the test's expected values; in that case print the real instants and check them against the comments at the top of the file.

- [ ] **Step 4: Run the whole collector suite**

Run: `cd local-pricing-collector && uv run pytest -q`
Expected: all pass. `main.py` is unchanged: it calls `run_forever(get_settings())`.

- [ ] **Step 5: Check the compose file needs no change**

Run: `grep -n "LOCAL_HORIZON_HOURS" -r . --include='*.yml' --include='*.py' --include='*.md' --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=superpowers`
Expected: only `README.md` (fixed in Task 6). If the compose file mentions it, remove that line.

- [ ] **Step 6: Commit**

```bash
git add -A .
git commit -m "feat: push local prices once a day at 23:59 and fill what is missing

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Docs, README and the spec notes

**Files:**
- Modify: `webapp/src/docs/04-sources.md`, `README.md`, `docs/superpowers/specs/2026-10-14-local-pricing-daily-push-and-backfill-design.md`

**Interfaces:** none. `docs.test.ts` and `links.test.ts` check structure and links.

- [ ] **Step 1: Update the help chapter**

In `webapp/src/docs/04-sources.md`:

1. In the add-window field table, replace the **From** row with:

```markdown
| **From** | The date the window starts. Today's date starts the window at the moment you add it. A later date starts it at midnight in your browser's time zone. An earlier date prices past days, back to the first data on your site. |
```

2. In "Changing a rate", after the sentence "Cost for earlier times keeps the old rate. **Delete** removes a window, and it stops new prices from it." add a new paragraph:

```markdown
The new rate shows in the prices written from the next 23:59 push, which is tomorrow's prices. Prices already written for today keep the old rate.
```

3. Replace the whole "When changes take effect" section with:

```markdown
### When changes take effect

The collector writes the next day's prices once a day, at 23:59 in your site's time zone. A rate you add or change today therefore shows in tomorrow's prices, and today's prices keep the rate they already have.

It also fills in what is missing. Within a minute of seeing a new source, after it restarts, and on the first check of each new day, it compares the prices stored for each direction with what your windows imply and writes the difference. That covers the rest of today for a source you have just created, and a day it missed. It reaches back to the first data on your site, but only for times when a window was in effect. A window with an earlier **From** date therefore prices past days, and your cost figures for those days use it.

Prices already written stay as they are after an **End** or **Delete**. Minutes that no other window covers keep the old rate through the end of tomorrow.
```

- [ ] **Step 2: Update the README row**

In `README.md`, replace the `local-pricing-collector/` table row with:

```markdown
| `local-pricing-collector/` | Turns rate windows entered in the webapp into price intervals, for suppliers with a fixed tariff. At 23:59 in each site's time zone it pushes the next local day's prices. Within a minute of a new source, a restart or a missed day it also fills what is missing, back to the site's first data. |
```

Remove any other mention of `LOCAL_HORIZON_HOURS` in `README.md`.

- [ ] **Step 3: Record the planning refinements in the spec**

Append to `docs/superpowers/specs/2026-10-14-local-pricing-daily-push-and-backfill-design.md`, before "Replaces in the 2026-10-13 spec":

```markdown
## Refinements made in planning

- The scheduled range is the start of the next local day to the end of the next local day. It reprices no minute of the current day.
- The parent process keeps a small in-memory record per source: the local dates on which its catch-up and its scheduled push last succeeded. A catch-up runs when the source has none for the current local date, which covers a new source, a restart and a day rollover. The scheduled push runs at 23:59 local or later if it has not run on the current local date. A failed run leaves the record alone, so the next tick retries. The record exists so a long range is not rebuilt every minute. A restart starts with no record, so it always does a fresh catch-up and nothing is lost.
- The collector uses the config in the discovery response, so `fetch_source_config` is gone.
- A child process runs one source's work and the parent does not wait for it, so a long history fill never delays other sources. A child is stopped after 2 hours.
- core-api sends the site data floor without a UTC offset, so the collector reads an offset-less floor as UTC.
```

- [ ] **Step 4: Run the docs checks and the style scan**

Run: `cd webapp && npm test && npm run lint`
Expected: all pass, with only the two older lint warnings in `AuthContext.tsx` and `useAvatarImage.ts`.

Scan every sentence you added against `.claude/rules/avoid-ai-writing.md`: no banned vocabulary, no "serves as", no trailing "-ing" clauses, no padded lists of three.

- [ ] **Step 5: Run every suite once**

```bash
(cd core-api && uv run pytest -q)
(cd amber-pricing-collector && uv run pytest -q)
(cd local-pricing-collector && uv run pytest -q)
(cd webapp && npm test)
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A .
git commit -m "docs: describe the daily 23:59 push and the coverage fill

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage**

| Spec section | Task |
|---|---|
| Core-api coverage endpoint | 1 |
| Tick every minute, discovery config reused, child per source | 5 |
| 23:59 scheduled push for the next local day | 3 (when), 4 (what), 5 (run) |
| Forward fill, history fill, window and end limits | 4 |
| 7-day chunks, 120 s timeout, retry on the next tick | 4 (chunking), 5 (push, retry) |
| `LOCAL_HORIZON_HOURS` removed | 2, 6 |
| Docs and README | 6 |
| Testing list (23:59 in Sydney and Adelaide, new source, 3-day outage, 25-hour day, late direction, ended windows, floor limit, chunking, isolation) | 3, 4, 5 |

**Differences from the spec text:** the scheduled range is tomorrow only, and a small in-memory record throttles catch-up work. Both are written into the spec in Task 6.

**Known gaps left alone:** clearing future prices when a window or source ends; the Amber-to-local switch gap; `SiteDataFloorOut` returning a naive timestamp (the collector copes); `LocalSource.config` carries every window on each discovery response, once a minute, which is small for a home site.

**Type consistency:** `Triggers`, `SourceState`, `due`, `after_success` (Task 3) are used with those names in Tasks 4 and 5. `PlannedRange`, `plan_ranges`, `chunk_range` (Task 4) match their use in Task 5. `Coverage` and `LocalSource` (Task 2) are used by Tasks 4 and 5. `channel_intervals` (Task 2) is called with `(windows, channel, tz, start, end)` in Task 5.
