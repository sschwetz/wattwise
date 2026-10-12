# Weather Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Weather collector (sub-project 4) — a standalone project that polls a Davis WeatherLink Live's local API every minute and pushes weather readings into the already-shipped Core API. Requires zero Core API changes.

**Architecture:** A new project, `enphase-data/weather-collector/`, structured identically to the Envoy collector: pure mapping logic, a thin WeatherLink HTTP client, a thin Core API HTTP client, and a poll loop. Genuinely simpler than the Envoy collector — no authentication means no token-refresh path at all — but the Envoy collector's final-review lessons (wrap transport errors into one exception type, run mapping inside the poll loop's guarded region, isolate one sensor's failure from another's) are designed in from the start here rather than relearned through a fix pass.

**Tech Stack:** `httpx` (sync client) for all HTTP, `respx` (dev-only) for HTTP-mocking in tests. No XML parsing needed (WeatherLink Live is JSON-only).

**Spec:** `docs/superpowers/specs/2026-09-29-weather-collector-design.md` (see 'WeatherLink Live local API — confirmed field mapping' and 'Collector design' sections).

## Global Constraints

- The collector's own environment variables are exactly `CORE_API_URL`, `CORE_API_SOURCE_ID`, `CORE_API_COLLECTOR_KEY`, `POLL_INTERVAL_SECONDS` (default `60`) — no WeatherLink host env var; that comes from Core API's `GET /sources/{id}/config` at runtime (`config = {"host": "<ip>"}`, no token field).
- `GET http://{host}/v1/current_conditions` — plain HTTP, no authentication, no headers required.
- Unit conversions: `temp_c = (temp - 32) * 5/9`; `wind_speed_kph = wind_speed_avg_last_1_min * 1.609344`; `barometric_pressure_hpa = bar_sea_level * 33.8639`; `humidity_pct`/`wind_direction_deg` are direct, no conversion.
- `solar_radiation_wm2` is only emitted when `solar_rad` is non-null — never coerced to 0.
- Rain: `rainfall_daily` is a tip-**count**, not a physical unit. Convert via the `rain_size` lookup (`1`: 0.01in, `2`: 0.2mm, `3`: 0.1mm, `4`: 0.001in; `0` is reserved/unsupported) to get cumulative mm-since-midnight, then report only the **delta since the last poll** as `rain_mm` — never the raw cumulative value and never a rolling window. A decrease from the previous poll means the daily counter reset at local midnight; treat the new count as the full delta, not a negative number. The very first poll after startup has no baseline — skip emitting `rain_mm` that cycle.
- Core API's HTTP client wraps every `httpx.HTTPError` into one `CoreApiError` type from the first commit — never let a raw `httpx` exception (connection refused, timeout, DNS) escape uncaught.
- Mapping runs inside the poll loop's guarded `try`/`except` region, not after it.
- The ISS (outdoor) reading set and the barometer reading are isolated from each other — a failure parsing one does not discard the other.
- The startup config fetch retries indefinitely (backing off by the poll interval) rather than failing fast.
- A last-resort exception guard wraps the poll loop's call site in `main()`.

## Review Focus

- An ISS entry with `solar_rad: null` (as on the real station) must not error and must not emit a `solar_radiation_wm2` reading at all — not a reading with value `0`.
- An unknown/reserved `rain_size` (e.g. `0`) must not crash the whole collector — it's isolated to dropping that cycle's ISS-derived readings (temp/humidity/wind/rain/solar), while the barometer reading still goes through.
- A `rainfall_daily` value lower than the previous poll's (a local-midnight reset) must report the new count as the delta, not a negative value.
- The very first `poll_once` call for a fresh `RainState` must not emit a `rain_mm` reading — only from the second call onward, once a real baseline exists.
- A malformed/incomplete `current_conditions` response (e.g. no `data_structure_type: 1` entry at all) must not crash the collector — verified via a real (non-monkeypatched) mapping call, mirroring the Envoy collector's C2 lesson, isolated the same way as the `rain_size` case above.

---

## File Structure

- `weather-collector/pyproject.toml` — **create**.
- `weather-collector/.env.example` — **create**.
- `weather-collector/weather_collector/__init__.py` — **create**: empty.
- `weather-collector/weather_collector/config.py` — **create**: env-var settings (same shape as the Envoy collector's).
- `weather-collector/weather_collector/mapping.py` — **create**: `RainState`, `rain_size_mm_per_tip`, `map_iss_readings`, `map_barometer_readings`.
- `weather-collector/weather_collector/weatherlink_client.py` — **create**: `fetch_current_conditions`.
- `weather-collector/weather_collector/core_api_client.py` — **create**: `CoreApiError`, `fetch_source_config`, `push_weather_readings` (transport-error wrapping built in from the start).
- `weather-collector/weather_collector/main.py` — **create**: startup + poll loop, per-sensor isolation, outer exception guard (all built in from the start).
- `weather-collector/tests/fixtures/current_conditions.json` — **create**: the user's real sample data, with the device ID replaced by a placeholder.
- `weather-collector/tests/test_config.py`, `test_mapping.py`, `test_weatherlink_client.py`, `test_core_api_client.py`, `test_main.py` — **create**.
- `weather-collector/Dockerfile` — **create**.

## Interfaces reference (carried through every task)

```python
# weather_collector/config.py
@dataclass(frozen=True)
class Settings:
    core_api_url: str
    source_id: int
    collector_key: str
    poll_interval_seconds: int

def get_settings() -> Settings: ...

# weather_collector/mapping.py
@dataclass
class RainState:
    last_daily_mm: float | None = None

def rain_size_mm_per_tip(rain_size: int) -> float: ...  # raises ValueError for 0 or an unknown value
def map_iss_readings(iss: dict, ts: datetime.datetime, rain_state: RainState) -> list[dict]: ...
def map_barometer_readings(barometer: dict, ts: datetime.datetime) -> list[dict]: ...
# each returned dict is {"ts": datetime, "metric": str, "value": float}; rain_state is mutated in place

# weather_collector/weatherlink_client.py
def fetch_current_conditions(host: str) -> dict: ...  # raises on HTTP failure, JSON error, or a non-null "error" field

# weather_collector/core_api_client.py
class CoreApiError(Exception): ...
def fetch_source_config(base_url: str, source_id: int, collector_key: str) -> dict: ...
def push_weather_readings(base_url: str, source_id: int, collector_key: str, readings: list[dict]) -> None: ...

# weather_collector/main.py
def poll_once(settings: Settings, config: dict, rain_state: RainState) -> None: ...
def main() -> None: ...
```

---

## Task 1: Project scaffold + settings

**Files:**
- Create: `weather-collector/pyproject.toml`, `weather-collector/weather_collector/__init__.py`, `weather-collector/weather_collector/config.py`, `weather-collector/.env.example`
- Test: `weather-collector/tests/test_config.py`

**Interfaces:**
- Produces: `Settings` dataclass, `get_settings() -> Settings`.

- [ ] **Step 1: Create the project directory and files**

From `enphase-data/`, create `weather-collector/`:

```bash
mkdir -p weather-collector/weather_collector weather-collector/tests
touch weather-collector/weather_collector/__init__.py weather-collector/tests/__init__.py
```

`weather-collector/pyproject.toml`:

```toml
[project]
name = "weather-collector"
version = "0.1.0"
description = "Polls a Davis WeatherLink Live's local API and pushes readings into Core API (sub-project 4)."
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27,<1.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0,<9.0",
    "respx>=0.21,<1.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["weather_collector"]
```

`weather-collector/.env.example`:

```
CORE_API_URL=http://localhost:8000
CORE_API_SOURCE_ID=1
CORE_API_COLLECTOR_KEY=change-me
POLL_INTERVAL_SECONDS=60
```

- [ ] **Step 2: Set up the environment**

```bash
cd weather-collector
uv sync
```

- [ ] **Step 3: Write the failing test**

```python
# weather-collector/tests/test_config.py
from __future__ import annotations

import pytest

from weather_collector.config import get_settings


def test_get_settings_reads_env_vars(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000/")
    monkeypatch.setenv("CORE_API_SOURCE_ID", "42")
    monkeypatch.setenv("CORE_API_COLLECTOR_KEY", "secret-key")
    monkeypatch.delenv("POLL_INTERVAL_SECONDS", raising=False)

    settings = get_settings()

    assert settings.core_api_url == "http://localhost:8000"  # trailing slash stripped
    assert settings.source_id == 42
    assert settings.collector_key == "secret-key"
    assert settings.poll_interval_seconds == 60


def test_get_settings_respects_custom_poll_interval(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.setenv("CORE_API_SOURCE_ID", "1")
    monkeypatch.setenv("CORE_API_COLLECTOR_KEY", "k")
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "30")

    assert get_settings().poll_interval_seconds == 30


def test_get_settings_raises_if_required_var_missing(monkeypatch):
    monkeypatch.delenv("CORE_API_URL", raising=False)
    monkeypatch.delenv("CORE_API_SOURCE_ID", raising=False)
    monkeypatch.delenv("CORE_API_COLLECTOR_KEY", raising=False)

    with pytest.raises(RuntimeError):
        get_settings()
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weather_collector.config'`

- [ ] **Step 5: Implement `weather_collector/config.py`**

```python
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    source_id: int
    collector_key: str
    poll_interval_seconds: int


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    source_id = os.environ.get("CORE_API_SOURCE_ID")
    collector_key = os.environ.get("CORE_API_COLLECTOR_KEY")
    if not core_api_url or not source_id or not collector_key:
        raise RuntimeError(
            "CORE_API_URL, CORE_API_SOURCE_ID, and CORE_API_COLLECTOR_KEY must all be set"
        )
    return Settings(
        core_api_url=core_api_url.rstrip("/"),
        source_id=int(source_id),
        collector_key=collector_key,
        poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "60")),
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
cd weather-collector
git add pyproject.toml uv.lock weather_collector/__init__.py weather_collector/config.py tests/__init__.py tests/test_config.py .env.example
git commit -m "Scaffold weather-collector project with env-var settings"
```

---

## Task 2: Pure mapping logic (including rain delta-tracking)

**Files:**
- Create: `weather_collector/mapping.py`, `tests/fixtures/current_conditions.json`
- Test: `tests/test_mapping.py`

**Interfaces:**
- Produces: `RainState`, `rain_size_mm_per_tip(rain_size: int) -> float`, `map_iss_readings(iss, ts, rain_state) -> list[dict]`, `map_barometer_readings(barometer, ts) -> list[dict]`.

- [ ] **Step 1: Save the real fixture data**

`weather-collector/tests/fixtures/current_conditions.json` (the user's real sample — device ID replaced with a placeholder):

```json
{
  "data": {
    "did": "AABBCCDDEEFF",
    "ts": 1790652615,
    "conditions": [
      {
        "lsid": 723522,
        "data_structure_type": 1,
        "txid": 1,
        "temp": 72.8,
        "hum": 57.3,
        "wind_speed_last": 2.00,
        "wind_dir_last": 76,
        "wind_speed_avg_last_1_min": 4.43,
        "wind_dir_scalar_avg_last_1_min": 41,
        "rain_size": 2,
        "rain_rate_last": 0,
        "rainfall_last_15_min": 0,
        "rainfall_daily": 0,
        "rainfall_monthly": 95,
        "rainfall_year": 6430,
        "solar_rad": null,
        "uv_index": null
      },
      {
        "lsid": 723521,
        "data_structure_type": 4,
        "temp_in": 73.1,
        "hum_in": 54.4
      },
      {
        "lsid": 723520,
        "data_structure_type": 3,
        "bar_sea_level": 30.422,
        "bar_trend": -0.072,
        "bar_absolute": 30.336
      }
    ]
  },
  "error": null
}
```

- [ ] **Step 2: Write the failing tests**

```python
# weather-collector/tests/test_mapping.py
from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from weather_collector.mapping import (
    RainState,
    map_barometer_readings,
    map_iss_readings,
    rain_size_mm_per_tip,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
TS = datetime.datetime(2026, 9, 29, 12, 0, tzinfo=datetime.timezone.utc)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def iss_entry(**overrides) -> dict:
    data = load_fixture("current_conditions.json")
    iss = next(c for c in data["data"]["conditions"] if c["data_structure_type"] == 1)
    iss.update(overrides)
    return iss


def barometer_entry() -> dict:
    data = load_fixture("current_conditions.json")
    return next(c for c in data["data"]["conditions"] if c["data_structure_type"] == 3)


def test_rain_size_lookup_matches_documented_table():
    assert rain_size_mm_per_tip(2) == 0.2
    assert rain_size_mm_per_tip(3) == 0.1
    assert rain_size_mm_per_tip(1) == pytest.approx(0.01 * 25.4)
    assert rain_size_mm_per_tip(4) == pytest.approx(0.001 * 25.4)


def test_rain_size_zero_is_reserved_and_raises():
    with pytest.raises(ValueError):
        rain_size_mm_per_tip(0)


def test_temp_humidity_wind_conversions():
    readings = map_iss_readings(iss_entry(), TS, RainState())
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["temp_c"] == pytest.approx((72.8 - 32) * 5 / 9)
    assert by_metric["humidity_pct"] == 57.3
    assert by_metric["wind_speed_kph"] == pytest.approx(4.43 * 1.609344)
    assert by_metric["wind_direction_deg"] == 41


def test_solar_rad_null_is_skipped_not_zeroed():
    readings = map_iss_readings(iss_entry(solar_rad=None), TS, RainState())
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert "solar_radiation_wm2" not in by_metric


def test_solar_rad_present_is_mapped_directly():
    readings = map_iss_readings(iss_entry(solar_rad=123.4), TS, RainState())
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["solar_radiation_wm2"] == 123.4


def test_first_poll_with_fresh_rain_state_emits_no_rain_reading():
    rain_state = RainState()
    readings = map_iss_readings(iss_entry(rainfall_daily=50, rain_size=2), TS, rain_state)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert "rain_mm" not in by_metric
    assert rain_state.last_daily_mm == pytest.approx(50 * 0.2)


def test_second_poll_reports_delta_since_first():
    rain_state = RainState()
    map_iss_readings(iss_entry(rainfall_daily=50, rain_size=2), TS, rain_state)  # establishes baseline: 10.0mm

    readings = map_iss_readings(iss_entry(rainfall_daily=60, rain_size=2), TS, rain_state)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["rain_mm"] == pytest.approx(2.0)  # (60-50)*0.2
    assert rain_state.last_daily_mm == pytest.approx(12.0)


def test_midnight_reset_reports_new_count_as_full_delta():
    rain_state = RainState()
    map_iss_readings(iss_entry(rainfall_daily=60, rain_size=2), TS, rain_state)  # baseline: 12.0mm

    readings = map_iss_readings(iss_entry(rainfall_daily=5, rain_size=2), TS, rain_state)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["rain_mm"] == pytest.approx(5 * 0.2)  # not negative
    assert rain_state.last_daily_mm == pytest.approx(1.0)


def test_map_iss_readings_raises_for_unsupported_rain_size():
    with pytest.raises(ValueError):
        map_iss_readings(iss_entry(rain_size=0), TS, RainState())


def test_map_barometer_readings_converts_inhg_to_hpa():
    readings = map_barometer_readings(barometer_entry(), TS)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["barometric_pressure_hpa"] == pytest.approx(30.422 * 33.8639)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_mapping.py -v`
Expected: FAIL — `weather_collector.mapping` doesn't exist.

- [ ] **Step 4: Implement `weather_collector/mapping.py`**

```python
from __future__ import annotations

import datetime
from dataclasses import dataclass

_RAIN_SIZE_MM_PER_TIP = {
    1: 0.01 * 25.4,
    2: 0.2,
    3: 0.1,
    4: 0.001 * 25.4,
}


@dataclass
class RainState:
    """Tracks the previous poll's cumulative rain-since-midnight (in mm) so
    map_iss_readings can report only the delta. None means no baseline yet —
    the first poll after startup emits no rain_mm reading at all."""

    last_daily_mm: float | None = None


def rain_size_mm_per_tip(rain_size: int) -> float:
    """Per Davis's documented rain_size table. 0 is reserved/unsupported."""
    if rain_size not in _RAIN_SIZE_MM_PER_TIP:
        raise ValueError(f"Unsupported or reserved rain_size: {rain_size}")
    return _RAIN_SIZE_MM_PER_TIP[rain_size]


def map_iss_readings(iss: dict, ts: datetime.datetime, rain_state: RainState) -> list[dict]:
    """Map a data_structure_type=1 (outdoor ISS) entry to {ts, metric, value}
    dicts, and update rain_state in place.

    Uses the 1-minute rolling average for wind (wind_speed_avg_last_1_min /
    wind_dir_scalar_avg_last_1_min), not the instantaneous "last" reading —
    matches the ~1-minute poll cadence and smooths gust noise.
    """
    readings = [
        {"ts": ts, "metric": "temp_c", "value": (iss["temp"] - 32) * 5 / 9},
        {"ts": ts, "metric": "humidity_pct", "value": iss["hum"]},
        {"ts": ts, "metric": "wind_speed_kph", "value": iss["wind_speed_avg_last_1_min"] * 1.609344},
        {"ts": ts, "metric": "wind_direction_deg", "value": iss["wind_dir_scalar_avg_last_1_min"]},
    ]
    if iss.get("solar_rad") is not None:
        readings.append({"ts": ts, "metric": "solar_radiation_wm2", "value": iss["solar_rad"]})

    mm_per_tip = rain_size_mm_per_tip(iss["rain_size"])
    current_daily_mm = iss["rainfall_daily"] * mm_per_tip
    if rain_state.last_daily_mm is not None:
        delta = current_daily_mm - rain_state.last_daily_mm
        if delta < 0:
            # local-midnight reset: the new count IS this cycle's rain, not
            # (new - old_from_yesterday).
            delta = current_daily_mm
        readings.append({"ts": ts, "metric": "rain_mm", "value": delta})
    rain_state.last_daily_mm = current_daily_mm

    return readings


def map_barometer_readings(barometer: dict, ts: datetime.datetime) -> list[dict]:
    """Map a data_structure_type=3 (barometer) entry. Uses bar_sea_level (the
    sea-level-adjusted reading), not bar_absolute — the standard
    meteorological convention for comparing pressure across locations."""
    return [
        {"ts": ts, "metric": "barometric_pressure_hpa", "value": barometer["bar_sea_level"] * 33.8639}
    ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_mapping.py -v`
Expected: PASS (10 tests)

- [ ] **Step 6: Commit**

```bash
git add weather_collector/mapping.py tests/fixtures/current_conditions.json tests/test_mapping.py
git commit -m "Add WeatherLink current_conditions to WeatherReading mapping with rain delta-tracking"
```

---

## Task 3: WeatherLink HTTP client

**Files:**
- Create: `weather_collector/weatherlink_client.py`
- Test: `tests/test_weatherlink_client.py`

**Interfaces:**
- Produces: `fetch_current_conditions(host: str) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# weather-collector/tests/test_weatherlink_client.py
from __future__ import annotations

import httpx
import pytest
import respx

from weather_collector.weatherlink_client import fetch_current_conditions


@respx.mock
def test_fetch_current_conditions_uses_plain_http_no_auth():
    route = respx.get("http://weatherlink.local/v1/current_conditions").mock(
        return_value=httpx.Response(200, json={"data": {"conditions": []}, "error": None})
    )
    result = fetch_current_conditions("weatherlink.local")
    assert route.called
    assert "Authorization" not in route.calls[0].request.headers
    assert result == {"data": {"conditions": []}, "error": None}


@respx.mock
def test_fetch_current_conditions_raises_on_http_error_status():
    respx.get("http://weatherlink.local/v1/current_conditions").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_current_conditions("weatherlink.local")


@respx.mock
def test_fetch_current_conditions_raises_when_device_reports_error_field():
    respx.get("http://weatherlink.local/v1/current_conditions").mock(
        return_value=httpx.Response(200, json={"data": None, "error": "some device fault"})
    )
    with pytest.raises(ValueError):
        fetch_current_conditions("weatherlink.local")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_weatherlink_client.py -v`
Expected: FAIL — `weather_collector.weatherlink_client` doesn't exist.

- [ ] **Step 3: Implement `weather_collector/weatherlink_client.py`**

```python
from __future__ import annotations

import httpx


def fetch_current_conditions(host: str) -> dict:
    """WeatherLink Live's local API needs no authentication at all — plain
    HTTP, no headers. A non-null top-level "error" field is the device's own
    signal that something's wrong (e.g. a sensor fault) even on an HTTP 200,
    so it's treated as a failure the same as a bad status code."""
    response = httpx.get(f"http://{host}/v1/current_conditions", timeout=10)
    response.raise_for_status()
    data = response.json()
    if data.get("error") is not None:
        raise ValueError(f"WeatherLink reported an error: {data['error']}")
    return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_weatherlink_client.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run full suite, then commit**

```bash
uv run pytest -q
git add weather_collector/weatherlink_client.py tests/test_weatherlink_client.py
git commit -m "Add WeatherLink Live local-API HTTP client"
```

---

## Task 4: Core API HTTP client (transport-error wrapping built in from the start)

**Files:**
- Create: `weather_collector/core_api_client.py`
- Test: `tests/test_core_api_client.py`

**Interfaces:**
- Produces: `CoreApiError`, `fetch_source_config(base_url, source_id, collector_key) -> dict`, `push_weather_readings(base_url, source_id, collector_key, readings: list[dict]) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# weather-collector/tests/test_core_api_client.py
from __future__ import annotations

import datetime
import json as _json

import httpx
import pytest
import respx

from weather_collector.core_api_client import CoreApiError, fetch_source_config, push_weather_readings

BASE_URL = "http://localhost:8000"


@respx.mock
def test_fetch_source_config_sends_collector_key_header():
    route = respx.get(f"{BASE_URL}/api/v1/sources/1/config").mock(
        return_value=httpx.Response(200, json={"config": {"host": "weatherlink.local"}})
    )
    result = fetch_source_config(BASE_URL, 1, "my-key")
    assert route.calls[0].request.headers["X-Collector-Key"] == "my-key"
    assert result == {"host": "weatherlink.local"}


@respx.mock
def test_fetch_source_config_raises_on_non_200():
    respx.get(f"{BASE_URL}/api/v1/sources/1/config").mock(return_value=httpx.Response(401))
    with pytest.raises(CoreApiError):
        fetch_source_config(BASE_URL, 1, "wrong-key")


@respx.mock
def test_fetch_source_config_raises_core_api_error_on_connection_failure():
    respx.get(f"{BASE_URL}/api/v1/sources/1/config").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        fetch_source_config(BASE_URL, 1, "my-key")


@respx.mock
def test_push_weather_readings_sends_batch_as_iso_timestamps():
    route = respx.post(f"{BASE_URL}/api/v1/sources/1/readings/weather").mock(
        return_value=httpx.Response(202, json={"ingested": 1})
    )
    ts = datetime.datetime(2026, 9, 29, 12, 0, tzinfo=datetime.timezone.utc)
    push_weather_readings(BASE_URL, 1, "my-key", [{"ts": ts, "metric": "temp_c", "value": 22.7}])

    body = _json.loads(route.calls[0].request.content)
    assert body == {"readings": [{"ts": "2026-09-29T12:00:00+00:00", "metric": "temp_c", "value": 22.7}]}


@respx.mock
def test_push_weather_readings_raises_on_non_202():
    respx.post(f"{BASE_URL}/api/v1/sources/1/readings/weather").mock(return_value=httpx.Response(422))
    with pytest.raises(CoreApiError):
        push_weather_readings(BASE_URL, 1, "my-key", [])


@respx.mock
def test_push_weather_readings_raises_core_api_error_on_connection_failure():
    respx.post(f"{BASE_URL}/api/v1/sources/1/readings/weather").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        push_weather_readings(BASE_URL, 1, "my-key", [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_core_api_client.py -v`
Expected: FAIL — `weather_collector.core_api_client` doesn't exist.

- [ ] **Step 3: Implement `weather_collector/core_api_client.py`**

```python
from __future__ import annotations

import httpx


class CoreApiError(Exception):
    """Raised on any failure talking to Core API — a non-success response or
    a transport failure (connection refused, timeout, DNS, etc.). Callers
    only ever need to catch this one type, never httpx's own exceptions."""


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    try:
        return httpx.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise CoreApiError(f"Failed to reach Core API: {exc}") from exc


def fetch_source_config(base_url: str, source_id: int, collector_key: str) -> dict:
    response = _request(
        "GET",
        f"{base_url}/api/v1/sources/{source_id}/config",
        headers={"X-Collector-Key": collector_key},
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch source config: {response.status_code} {response.text}")
    return response.json()["config"]


def push_weather_readings(base_url: str, source_id: int, collector_key: str, readings: list[dict]) -> None:
    payload = {
        "readings": [
            {"ts": r["ts"].isoformat(), "metric": r["metric"], "value": r["value"]} for r in readings
        ]
    }
    response = _request(
        "POST",
        f"{base_url}/api/v1/sources/{source_id}/readings/weather",
        headers={"X-Collector-Key": collector_key},
        json=payload,
        timeout=10,
    )
    if response.status_code != 202:
        raise CoreApiError(f"Failed to push readings: {response.status_code} {response.text}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_core_api_client.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run full suite, then commit**

```bash
uv run pytest -q
git add weather_collector/core_api_client.py tests/test_core_api_client.py
git commit -m "Add Core API HTTP client with transport-error wrapping"
```

---

## Task 5: Poll loop with per-sensor isolation and startup retry

**Files:**
- Create: `weather_collector/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: `poll_once(settings, config, rain_state) -> None`, `main() -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# weather-collector/tests/test_main.py
from __future__ import annotations

import datetime

import pytest

from weather_collector import main as main_module
from weather_collector.config import Settings
from weather_collector.core_api_client import CoreApiError
from weather_collector.mapping import RainState

SETTINGS = Settings(
    core_api_url="http://localhost:8000", source_id=1, collector_key="key", poll_interval_seconds=60
)

CURRENT_CONDITIONS = {
    "data": {
        "conditions": [
            {
                "data_structure_type": 1,
                "temp": 72.8,
                "hum": 57.3,
                "wind_speed_avg_last_1_min": 4.43,
                "wind_dir_scalar_avg_last_1_min": 41,
                "rain_size": 2,
                "rainfall_daily": 0,
                "solar_rad": None,
            },
            {"data_structure_type": 3, "bar_sea_level": 30.422, "bar_absolute": 30.336},
        ]
    },
    "error": None,
}


def test_poll_once_pushes_iss_and_barometer_readings_on_success(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: CURRENT_CONDITIONS)
    pushed = []
    monkeypatch.setattr(
        main_module, "push_weather_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )

    main_module.poll_once(SETTINGS, {"host": "weatherlink.local"}, RainState())

    assert len(pushed) == 1
    metrics = {r["metric"] for r in pushed[0]}
    assert "temp_c" in metrics
    assert "barometric_pressure_hpa" in metrics
    assert "solar_radiation_wm2" not in metrics  # null in this fixture
    assert "rain_mm" not in metrics  # first poll, no baseline yet


def test_poll_once_isolates_iss_failure_from_barometer(monkeypatch, caplog):
    broken = {
        "data": {
            "conditions": [
                {"data_structure_type": 1, "temp": 72.8, "hum": 57.3, "wind_speed_avg_last_1_min": 1.0,
                 "wind_dir_scalar_avg_last_1_min": 1, "rain_size": 0, "rainfall_daily": 0, "solar_rad": None},
                {"data_structure_type": 3, "bar_sea_level": 30.422, "bar_absolute": 30.336},
            ]
        },
        "error": None,
    }
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: broken)
    pushed = []
    monkeypatch.setattr(
        main_module, "push_weather_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )

    with caplog.at_level("ERROR"):
        main_module.poll_once(SETTINGS, {"host": "weatherlink.local"}, RainState())

    assert len(pushed) == 1
    metrics = {r["metric"] for r in pushed[0]}
    assert metrics == {"barometric_pressure_hpa"}  # ISS dropped (rain_size=0), barometer still pushed


def test_poll_once_isolates_entirely_missing_iss_entry(monkeypatch):
    # No data_structure_type: 1 entry at all (not just malformed) -- the
    # Envoy collector's C2 lesson: a genuinely missing/unexpected response
    # shape must not crash the cycle, real (non-monkeypatched) mapping code
    # is exercised via the real _map_iss_or_empty/map_barometer_readings path.
    no_iss = {
        "data": {
            "conditions": [
                {"data_structure_type": 3, "bar_sea_level": 30.422, "bar_absolute": 30.336},
            ]
        },
        "error": None,
    }
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: no_iss)
    pushed = []
    monkeypatch.setattr(
        main_module, "push_weather_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )

    main_module.poll_once(SETTINGS, {"host": "weatherlink.local"}, RainState())

    assert len(pushed) == 1
    metrics = {r["metric"] for r in pushed[0]}
    assert metrics == {"barometric_pressure_hpa"}  # ISS entirely absent, barometer still pushed


def test_poll_once_isolates_missing_barometer_entry(monkeypatch):
    no_barometer = {
        "data": {
            "conditions": [
                {"data_structure_type": 1, "temp": 72.8, "hum": 57.3, "wind_speed_avg_last_1_min": 1.0,
                 "wind_dir_scalar_avg_last_1_min": 1, "rain_size": 2, "rainfall_daily": 0, "solar_rad": None},
            ]
        },
        "error": None,
    }
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: no_barometer)
    pushed = []
    monkeypatch.setattr(
        main_module, "push_weather_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )

    main_module.poll_once(SETTINGS, {"host": "weatherlink.local"}, RainState())

    assert len(pushed) == 1
    metrics = {r["metric"] for r in pushed[0]}
    assert "barometric_pressure_hpa" not in metrics
    assert "temp_c" in metrics  # ISS readings still pushed


def test_poll_once_skips_cycle_when_weatherlink_unreachable(monkeypatch, caplog):
    def _raise(host):
        raise OSError("network unreachable")

    monkeypatch.setattr(main_module, "fetch_current_conditions", _raise)

    with caplog.at_level("ERROR"):
        main_module.poll_once(SETTINGS, {"host": "weatherlink.local"}, RainState())  # must not raise


def test_poll_once_logs_but_does_not_raise_when_core_api_unreachable(monkeypatch, caplog):
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: CURRENT_CONDITIONS)

    def _raise_core_api(*a, **k):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(main_module, "push_weather_readings", _raise_core_api)

    with caplog.at_level("ERROR"):
        main_module.poll_once(SETTINGS, {"host": "weatherlink.local"}, RainState())  # must not raise


def test_rain_state_persists_across_poll_once_calls(monkeypatch):
    monkeypatch.setattr(main_module, "push_weather_readings", lambda *a, **k: None)
    rain_state = RainState()

    first = {"data": {"conditions": [
        {"data_structure_type": 1, "temp": 70, "hum": 50, "wind_speed_avg_last_1_min": 1.0,
         "wind_dir_scalar_avg_last_1_min": 1, "rain_size": 2, "rainfall_daily": 50, "solar_rad": None},
    ]}, "error": None}
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: first)
    main_module.poll_once(SETTINGS, {"host": "weatherlink.local"}, rain_state)
    assert rain_state.last_daily_mm == pytest.approx(10.0)

    second = {"data": {"conditions": [
        {"data_structure_type": 1, "temp": 70, "hum": 50, "wind_speed_avg_last_1_min": 1.0,
         "wind_dir_scalar_avg_last_1_min": 1, "rain_size": 2, "rainfall_daily": 60, "solar_rad": None},
    ]}, "error": None}
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: second)
    main_module.poll_once(SETTINGS, {"host": "weatherlink.local"}, rain_state)
    assert rain_state.last_daily_mm == pytest.approx(12.0)


def test_fetch_initial_config_retries_until_success(monkeypatch):
    attempts = {"count": 0}

    def fake_fetch(base_url, source_id, key):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise CoreApiError("not yet")
        return {"host": "weatherlink.local"}

    monkeypatch.setattr(main_module, "fetch_source_config", fake_fetch)
    sleeps = []
    monkeypatch.setattr(main_module.time, "sleep", lambda s: sleeps.append(s))

    config = main_module._fetch_initial_config(SETTINGS)

    assert config == {"host": "weatherlink.local"}
    assert attempts["count"] == 3
    assert sleeps == [60, 60]


def test_main_loop_survives_unexpected_exception_from_poll_once(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(main_module, "_fetch_initial_config", lambda settings: {"host": "weatherlink.local"})
    monkeypatch.setattr(
        main_module, "poll_once", lambda settings, config, rain_state: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    calls = {"sleep": 0}

    def _fake_sleep(seconds):
        calls["sleep"] += 1
        if calls["sleep"] >= 2:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert calls["sleep"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_main.py -v`
Expected: FAIL — `weather_collector.main` doesn't exist.

- [ ] **Step 3: Implement `weather_collector/main.py`**

```python
from __future__ import annotations

import datetime
import logging
import time

from weather_collector.config import Settings, get_settings
from weather_collector.core_api_client import CoreApiError, fetch_source_config, push_weather_readings
from weather_collector.mapping import RainState, map_barometer_readings, map_iss_readings
from weather_collector.weatherlink_client import fetch_current_conditions

logger = logging.getLogger("weather_collector")


def _map_iss_or_empty(conditions: list[dict], ts: datetime.datetime, rain_state: RainState) -> list[dict]:
    try:
        iss = next(c for c in conditions if c["data_structure_type"] == 1)
        return map_iss_readings(iss, ts, rain_state)
    except Exception:
        logger.exception("Failed to find/parse ISS (outdoor) data this cycle; continuing without it")
        return []


def _map_barometer_or_empty(conditions: list[dict], ts: datetime.datetime) -> list[dict]:
    try:
        barometer = next(c for c in conditions if c["data_structure_type"] == 3)
        return map_barometer_readings(barometer, ts)
    except Exception:
        logger.exception("Failed to find/parse barometer data this cycle; continuing without it")
        return []


def poll_once(settings: Settings, config: dict, rain_state: RainState) -> None:
    """Runs one poll cycle. Unlike the Envoy collector there's no config to
    refresh (no auth), so config is read-only here."""
    ts = datetime.datetime.now(datetime.timezone.utc)
    try:
        data = fetch_current_conditions(config["host"])
        conditions = data["data"]["conditions"]
    except Exception:
        logger.exception("Failed to poll WeatherLink this cycle")
        return

    readings = _map_iss_or_empty(conditions, ts, rain_state) + _map_barometer_or_empty(conditions, ts)
    if not readings:
        return

    try:
        push_weather_readings(settings.core_api_url, settings.source_id, settings.collector_key, readings)
    except CoreApiError:
        logger.exception("Failed to push readings to Core API this cycle")


def _fetch_initial_config(settings: Settings) -> dict:
    """Retries indefinitely (backing off by the poll interval) rather than
    failing fast, so a Core API outage at collector-restart time doesn't
    require a human to manually restart the process once it recovers."""
    while True:
        try:
            return fetch_source_config(settings.core_api_url, settings.source_id, settings.collector_key)
        except CoreApiError:
            logger.exception(
                "Failed to fetch source config from Core API at startup; retrying in %s seconds",
                settings.poll_interval_seconds,
            )
            time.sleep(settings.poll_interval_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    config = _fetch_initial_config(settings)
    rain_state = RainState()

    while True:
        try:
            poll_once(settings, config, rain_state)
        except Exception:
            logger.exception("Unexpected error in poll cycle; continuing")
        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_main.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Run full suite, then commit**

```bash
uv run pytest -q
git add weather_collector/main.py tests/test_main.py
git commit -m "Add poll loop with per-sensor isolation and startup retry"
```

---

## Task 6: Deployment packaging (build-verified, not just written)

**Files:**
- Create: `Dockerfile`

**Interfaces:** none (packaging only).

- [ ] **Step 1: Write the Dockerfile**

Built correctly from the start this time — two-stage `uv sync` and a direct venv-Python `CMD`, avoiding the Envoy collector's original mistake of running `uv run` at container start (which re-syncs `--no-dev`-excluded packages into the runtime image and requires network access on every restart):

```dockerfile
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY weather_collector ./weather_collector
RUN uv sync --frozen --no-dev

ENV PYTHONUNBUFFERED=1

CMD ["/app/.venv/bin/python", "-m", "weather_collector.main"]
```

- [ ] **Step 2: Build the image**

Run: `docker build -t weather-collector .`
Expected: builds successfully. (Docker/Colima should be available in this environment now — if `docker info` fails, start it first: `colima start`.)

- [ ] **Step 3: Verify no dev dependencies leaked into the image**

```bash
docker run --rm weather-collector /app/.venv/bin/python -c "import pytest"
```

Expected: `ModuleNotFoundError: No module named 'pytest'` (exit code 1) — this is the correct/expected outcome, confirming the image is production-only.

- [ ] **Step 4: Smoke-run the actual entrypoint**

```bash
docker run --rm -e CORE_API_URL=http://localhost:8000 -e CORE_API_SOURCE_ID=1 -e CORE_API_COLLECTOR_KEY=test weather-collector /app/.venv/bin/python -c "from weather_collector.config import get_settings; print(get_settings())"
```

Expected: prints the `Settings` object without error.

- [ ] **Step 5: Clean up the local test image**

```bash
docker rmi weather-collector
```

- [ ] **Step 6: Commit**

```bash
git add Dockerfile
git commit -m "Add Dockerfile for containerized deployment"
```
