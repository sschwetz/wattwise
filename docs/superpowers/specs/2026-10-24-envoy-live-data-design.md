# Envoy live data

Date: 2026-10-24

## Problem

The Envoy collector reads `/production.json` and `/ivp/ensemble/power` once a
minute and stores that single instantaneous sample as the minute's reading.
One sample can land on a spike or a dip, and the battery correction in
`map_production_readings` subtracts a battery sample from the same instant as
the consumption sample it corrects.

The gateway also publishes `/ivp/livedata/status`, the feed behind the Enphase
mobile app's live view. Against the owner's gateway, 10 calls one second apart
returned a new `last_update` on every call (1 to 2 seconds apart). One call
returns PV, grid, load, storage and generator power plus state of charge.

## Goal

Each minute's readings are the average of samples taken every few seconds
from `/ivp/livedata/status`, instead of one instantaneous sample. The current
polls remain as the fallback. The collector also starts storing, once a
minute from `/ivp/meters/readings`, the per-phase grid voltage and the CT
meters' lifetime energy counters. The counters are stored raw, so they can be
studied before anything relies on them.

## Goals and non-goals

- Same metrics, same minute timestamps, same 60 second push to Core API.
- No change to the webapp.
- Not in this change: the `/stream/meter` SSE stream, frequency, per-phase
  power, sub-minute storage, and any chart or calculation that uses the
  energy counters or voltage.
- Not in this change: a third meter. `/ivp/meters/readings` on the owner's
  gateway returns an all-zero third entry that `/ivp/meters` does not list,
  so it is not configured. Entries not listed by `/ivp/meters` are skipped.
  If an EV charger or generator meter is configured later, storing it is a
  follow-up.

## Sampling

A background thread per source calls `GET /ivp/livedata/status` every
`LIVE_SAMPLE_SECONDS` (default 3) and appends a sample to an in-memory list
for that source. It uses the existing pinned-certificate connection and
bearer token (`envoy_client._request`).

A sample keeps: `last_update`, `pv.agg_p_mw`, `grid.agg_p_mw`, the sum of
`storage.agg_p_ph_*_mw`, `soc`, `main_relay_state`, and whether the site has
a battery (see Mapping). A sample whose
`last_update` equals the previous one is dropped, so a frozen feed adds
nothing.

`poll_once` (still every 60 seconds) takes the list, clears it, and averages
it. The average covers the samples since the previous push, not the clock
minute. The timestamp rule is unchanged (`now` truncated to the minute).

## Mapping

Units are milliwatts; divide by 1000 for watts. Signs below are the
convention in `mapping.py` (positive grid = import, positive battery =
discharge).

- `production_w` = mean of `pv.agg_p_mw`.
- `consumption_w` = production + grid + battery (positive = discharge), each
  from the figures below. `load.agg_p_mw` is not used: the gateway computes
  it from the aggregate storage figure, which is the one in doubt (see the
  open check). This matches how `mapping.py` corrects `consumption_w` today
  (total consumption plus battery net power).
- `grid_import_w` / `grid_export_w` = split on the sign of the mean of
  `grid.agg_p_mw`, as the current code does for `net-consumption`.
- `battery_charge_w` / `battery_discharge_w` from the mean of the sum of
  `storage.agg_p_ph_a_mw`, `_ph_b_mw` and `_ph_c_mw`, not `storage.agg_p_mw`.
  Positive is discharge, negative is charge, as `battery_net_w` returns.
- `battery_soc_pct` = last `soc` (matches its `LAST_VALUE` aggregation).
- A site has a battery when `enc_agg_energy`, `acb_agg_energy` or `soc` is
  above zero, or any `storage.agg_p_ph_*_mw` is non-zero. A drained battery
  reports 0 for the first three, so the per-phase power check catches it
  while it draws standby power. Each sampler latches presence: once a sample
  shows a battery, later samples from that sampler count as having one.
  Without one, no battery readings are written and battery power is
  taken as 0 in `consumption_w`. This is a guess about a batteryless
  gateway's response, which has not been seen; the plan tests it only against
  a made-up response.
- `invert_consumption_ct` applies as it does today.
- `LIVE_BATTERY_SOURCE` picks where the live path's battery figures come
  from. `livedata` (the default) uses the per-phase storage figures and `soc`
  from the feed as described above. `ensemble` reads `/ivp/ensemble/power`
  once a minute and takes `battery_charge_w` / `battery_discharge_w`,
  `battery_soc_pct` and the battery term in `consumption_w` from it; if that
  call fails, the minute has no battery rows and battery power counts as 0.

Open check, before trusting the battery figures. In the owner's samples
(battery idle at 10% charge) `storage.agg_p_mw` was +37 to +76 W while the
three per-phase values summed to about -44 W, and
`load = grid + pv + storage` held for the aggregate and for each phase. The
Enphase mobile app shows charge as positive. A negative per-phase sum is a
charge in the identity's terms, which fits a battery drawing standby power at
its floor; a positive aggregate would be a discharge, which does not. This is
a hypothesis. The plan compares the per-phase sum and the aggregate against
`/ivp/ensemble/power` and the app while the battery is charging and while it
is discharging. If the per-phase sum disagrees with `/ivp/ensemble/power`,
battery figures keep coming from `/ivp/ensemble/power` and only `soc` and the
CT figures come from the live feed.

## Fallback

A minute uses the existing path (`_fetch_and_map`, unchanged) when the
sample list is empty, or its newest `last_update` is older than
`LIVE_STALE_SECONDS` (default 30), or the thread's last call failed. That
minute is a single reading, as every minute is today. The existing 401
handling (`_recover_from_auth_failure`) and certificate re-pinning apply to
the live calls the same way.

## Keepalive

`connection.sc_stream` was `enabled` while the mobile app had been in use.
The `sc_SendStreamCtrl` counter suggests the app switches the feed on, and
this is unconfirmed: the gateway may switch it off again when nobody is
watching. If `last_update` stops moving while calls still succeed, the
sampler sends `POST /ivp/livedata/stream` with `{"enable": 1}` and rechecks.
It repeats at most once per `LIVE_STALE_SECONDS`. The plan starts by leaving
the app closed for a few hours to see whether the feed freezes. If it never
does, the keepalive is dropped.

## Restart behavior

The sample list lives in memory only. A restart or crash loses at most the
current minute's samples; that minute falls back to a single reading. The
unsent-batch list (`_pending_readings`) is unchanged: in memory, capped at
5000, dropped on restart. Nothing is written to disk.

## New metrics

Each is one row per source per minute, written to `energy_readings`. The
voltage and counter rows come from one `GET /ivp/meters/readings` call per
minute, made in `poll_once` (one sample, not averaged). `grid_relay_state`
comes from the live feed.

| Metric | Source | Aggregation |
|---|---|---|
| `production_wh_lifetime` | production meter, `actEnergyDlvd` | `LAST_VALUE` |
| `grid_import_wh_lifetime` | grid (net-consumption) meter, `actEnergyDlvd` | `LAST_VALUE` |
| `grid_export_wh_lifetime` | grid (net-consumption) meter, `actEnergyRcvd` | `LAST_VALUE` |
| `grid_voltage_ph_a_v`, `_ph_b_v`, `_ph_c_v` | grid meter `channels[0..2]` `voltage` | `AVERAGE` |
| `grid_relay_state` | live feed `main_relay_state` (0 or 1) | `LAST_VALUE` |

Notes from the owner's live response:

- Delivered and received are separate totals, each only growing. That avoids
  the `production.json` `net-consumption` `whLifetime`, which is net and goes
  negative on an exporting site. House consumption energy is not stored; it is
  production + import - export.
- The top-level `voltage` of a meter is the sum of its phases (717 V on a
  three-phase meter). Use the per-channel values, one metric per channel that
  exists. The number of channels comes from the grid meter's `phaseCount`; a
  single-phase site gets only `grid_voltage_ph_a_v`.
- Counters are stored raw: no sign inversion for `invert_consumption_ct`, no
  battery correction. A reversed CT swaps import and export.
- Wh counters are floats with a fractional part; stored as given.
- The readings response has three entries; the third is all zeros and is not
  listed by `/ivp/meters`. Meters are identified by calling `GET /ivp/meters`
  (confirmed on the owner's gateway: `eid`, `state`, `measurementType` of
  `production` or `net-consumption`, `phaseCount`) and caching the result per
  source. A readings entry whose `eid` is not listed, or whose `state` is not
  `enabled`, is skipped.
- There is no "today" counter on this endpoint. A day's energy is the
  difference between two lifetime readings, so none is stored.
- Frequency (50.0 to 50.12 Hz in the samples) is not stored.

The counters may not roll over cleanly. Nothing reads them yet; once a few
weeks of raw values are stored, a follow-up decides how to turn them into
energy (for example, deltas with reset detection).

A failed `/ivp/meters/readings` call drops only these rows for that minute.

## Core API changes

- Add the new members to `EnergyMetric` in `core_api/models/readings.py` and
  to `ENERGY_METRIC_AGGREGATION` (`LAST_VALUE` for the counters and
  `grid_relay_state`, `AVERAGE` for the voltages).
- An Alembic migration that rebuilds the CHECK constraints
  `ck_energy_readings_metric` and `ck_energy_readings_rollup_5m_metric` with
  the new values, and the downgrade.
- All metric names fit the 32 character column.
- `EnergyReadingIn.metric` is typed `EnergyMetric`, so the collector's push is
  rejected until Core API is deployed. Deploy Core API first.

## Collector changes

- `envoy_client.py`: `fetch_livedata`, `fetch_meters` and
  `fetch_meter_readings`, each `(host, token, cert_fingerprint)`.
- `mapping.py`: `map_livedata_readings(samples, ts, invert_consumption_ct)`
  and `map_meter_readings(meters, readings, ts)` (counters and voltages).
- A sampler module with the thread, the sample list and the keepalive.
- `config.py`: `LIVE_SAMPLE_SECONDS`, `LIVE_STALE_SECONDS`,
  `LIVE_BATTERY_SOURCE`.
- `main.py`: `poll_once` uses live readings when fresh, otherwise
  `_fetch_and_map`; it adds the meter readings either way. The
  `/ivp/meters` result is cached per source and refetched on a 401 or a
  config refresh.

## Tests

Mapping tests from recorded live-feed JSON (including a frozen `last_update`
and an empty list), fallback selection, counter and voltage mapping from the
recorded `/ivp/meters/readings` response (three meters, the third all zero),
and the Core API enum and migration. The sampler thread is tested with a fake
clock and a fake `fetch_livedata`.

## Decisions made without the owner

- The SSE stream is left out. Voltage is read from `/ivp/meters/readings`
  once a minute, so a short over-voltage spike between polls can be missed.
  A follow-up could sample it in the fast loop and store the maximum.
- `grid_relay_state` is stored on every minute the live feed supplies the
  readings, not only on change, so the rollup and charts need no special
  case. Fallback minutes have no relay reading and write no row.
- `LIVE_BATTERY_SOURCE` defaults to `livedata`. The plan first shipped
  `ensemble` as the default until the battery sign check under "Pending live
  verification" passed; the owner chose `livedata` before running the check
  and will set `ensemble` if the figures look wrong.
- Sample interval default of 3 seconds and stale threshold of 30 seconds.
- Counters come from the CT meters, not the inverter totals, matching
  `production_w`.
- `consumption_wh_lifetime` and the "today" counters are not stored; both
  can be derived from the stored lifetime values.

## Pending live verification

The owner still has to run these two checks against the real gateway. No results are recorded yet.

- [ ] Battery sign. While the battery is charging, then while it is discharging, compare the per-phase sum of `storage.agg_p_ph_*_mw`, the aggregate `storage.agg_p_mw` and the summed `real_power_mw` from `/ivp/ensemble/power`. Compare all three with the Enphase app, which shows charge as positive. If the per-phase sum disagrees with `/ivp/ensemble/power`, take battery figures from `/ivp/ensemble/power` and only the state of charge and CT figures from the live feed.
- [ ] Keepalive. Leave the Enphase mobile app closed for a few hours. Check whether `connection.sc_stream` stays enabled and `last_update` keeps advancing. If the feed never freezes, remove the keepalive code.
