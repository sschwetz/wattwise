# Local pricing: daily push and coverage backfill

Date: 2026-10-14

This amends `2026-10-13-local-pricing-collector-design.md`. Where the two differ, this document wins. The sections it replaces are listed at the end.

## Problem

`local-pricing-collector` pushes the next 48 hours of prices every 5 minutes. That is more writing than the prices need: a fixed tariff does not change from one hour to the next. The wanted behaviour is one push a day, at 23:59 in the site's timezone, for the next day.

A once-a-day push leaves three cases without prices until the next 23:59:

- A source created mid-day.
- A collector restart or outage that skips a 23:59 push.
- A direction (import, export, controlled load) whose first window is added later.

It also never prices past days, so cost for history shows as missing price.

## Decisions

- The scheduled push happens at 23:59 on the site's local clock. It covers the next local calendar day, 00:00 to the following 00:00. A literal 24 hours would end an hour early on the 25-hour clocks-go-back day.
- The collector works out what is missing from core-api's record of what is already priced, and fills it. It keeps no state of its own, so a restart cannot lose its place.
- The fill reaches back to the first data on the site, and only where a window was in effect. A window with an earlier **From** date therefore prices past days.
- Editing a window changes prices from the next scheduled push. It does not reprice the current day.
- The collector wakes every minute and decides per source. 23:59 is fixed, not configurable.

## Core-api

One new read-only endpoint, using the same collector-key and provider checks as the other price endpoints for a pricing source:

`GET /api/v1/sources/{source_id}/prices/coverage`

```json
{
  "channels": {
    "buy": {"first": "2026-06-01T00:00:00Z", "last": "2026-10-15T00:00:00Z"},
    "sell": {"first": "2026-06-01T00:00:00Z", "last": "2026-10-15T00:00:00Z"}
  }
}
```

- `first` is the earliest `interval_start` and `last` is the latest `interval_end` stored for that channel and source.
- A channel with no stored prices is left out of `channels`.
- Timestamps are `UtcDatetime`.
- The existing `/site-data-floor` endpoint supplies the start of history. The existing `/prices/gaps` endpoint is not used, because it works in whole UTC days and ignores a channel that has no prices yet.

No migration. No change to ingestion.

## local-pricing-collector

### Tick

Every minute, aligned to the minute plus a short buffer, the collector lists its sources. Discovery already returns each source's timezone and rate windows, so no extra config request is needed. For each source it runs in its own process, as now, and:

1. Parses the timezone and windows. A source with no windows does nothing.
2. Fetches the coverage and the site data floor.
3. Plans a range to push (below). If the range is empty, it stops.
4. Builds intervals for the range with the existing rules and pushes them in chunks.

### Planning

All times are instants. "End of today" and "end of tomorrow" are local midnights in the site's timezone.

For each channel that has at least one window:

- **Scheduled push.** If the site's local time is 23:59, the range is now to the end of the next local day, whatever the coverage says. This is also how prices already written for tomorrow get replaced after a window edit.
- **Forward fill.** If the channel's `last` is missing or earlier than the end of today, the range is `last` (or the start of history if the channel has no coverage) to the end of tomorrow. This covers a new source, a restart, an outage, and a late-added direction.
- **History fill.** If the channel's `first` is missing or later than the start of history, the range also includes the start of history to `first`.

The start of history is the later of the site data floor and the earliest `effective_from` among the channel's windows. The end of any range is no later than the latest `effective_to` among the channel's windows, if every window on the channel has one. These two limits keep a source whose windows have all ended from rebuilding months of empty minutes every tick.

The channel ranges merge into one pass. A range that produces no intervals pushes nothing.

### Building and pushing

- Intervals use the existing rules: at most 60 minutes, closed at each top of the UTC hour and wherever the rate changes, minutes without a rate left out, sell pushed negative and zero pushed as `0`.
- A range longer than 7 days is cut into 7-day chunks, one request each, with a 120-second timeout, the way the Amber backfill does. A failed chunk is logged and the rest of the range is retried on the next tick, because the coverage then shows where it stopped.
- Pushes are upserts, so repeating one is safe.

### Settings

`LOCAL_HORIZON_HOURS` is removed. The collector needs only `CORE_API_URL` and `COLLECTOR_KEY`.

## Webapp

No change to the panel. The hint under the add form stays. The "Adding a window with today's date starts at the current moment" rule still stops an End followed by an Add from overlapping.

## Docs

`04-sources.md`:

- "When changes take effect" says the collector writes the next day's prices at 23:59 site time, fills what is missing within a minute of seeing a new source or a restart, and reaches back to your first data.
- The **From** field says an earlier date prices past days back to the first data on the site.
- "Changing a rate" says a new rate shows from the next 23:59 push. Today's prices keep the old rate.

`README.md`: the `local-pricing-collector` row changes to match, and `LOCAL_HORIZON_HOURS` goes.

## Testing

- Core-api: the coverage endpoint returns first and last per channel, omits a channel with no prices, is scoped to its source, enforces the collector key and provider header, and returns an empty `channels` for a new source.
- Collector planning, with fixed clocks:
  - 23:59 local in `Australia/Sydney` and in a half-hour zone (`Australia/Adelaide`) triggers a push, 23:58 and 00:00 do not.
  - A new source (no coverage) gets history, today and tomorrow in one pass.
  - A collector that was down for three days gets the hole filled from `last`.
  - The 25-hour clocks-go-back day is fully priced by the 23:59 push before it.
  - A direction added later is filled from the start of history while the others are untouched.
  - All windows ended: nothing is built.
  - A window whose **From** is earlier than the site data floor starts at the floor.
  - A range over 7 days is chunked, and a failed chunk leaves the rest for the next tick.
- Loop: a source that raises does not stop the others.

## Rollout order

1. Core-api coverage endpoint and tests.
2. Collector: client call, planner, chunked push, loop and tests.
3. Docs and README.

## Not included

- Repricing the current day after an edit.
- Changing 23:59 or the 7-day chunk size through settings.
- Filling holes in the middle of the covered span. A hole before `first` or after `last` is filled, and the forward fill from `last` repairs an outage.
- Clearing future prices when a window ends (still the known gap in the earlier spec).

## Refinements made in planning

- The scheduled push for a target date covers the whole of that date, local midnight to local midnight. At 23:59 on day D it prices D+1. It reprices no minute of the current day, except on a retry (see below).
- The scheduled push stays owed until 02:00 local on its target date. If it fails, the collector retries it every minute until then. A retry after midnight prices the whole target date again.
- The parent process keeps a small in-memory record per source: the local dates on which its catch-up and its scheduled push last succeeded. A catch-up runs when the source has none for the current local date, which covers a new source, a restart and a day rollover. A failed run leaves the record alone, so the next tick retries. The record exists so a long range is not rebuilt every minute. A restart starts with no record, so it always does a fresh catch-up and nothing is lost.
- A range of more than 7 days is pushed in 7-day chunks. A history fill is pushed newest chunk first, so an interrupted fill resumes correctly.
- A direction whose windows leave minutes without a rate, for example seasonal windows, is planned once per local day and per restart. It builds nothing for the unpriced stretch and sends no requests for it. Such a direction costs a little CPU once a day and once per restart, and sends no requests.
- A change to a source's rate windows triggers a catch-up within a minute. The parent keeps a fingerprint of the windows from the last successful catch-up, in memory with the rest of the per-source record. The catch-up fills what is missing and does not reprice ranges that already have prices.
- The collector uses the config in the discovery response, so `fetch_source_config` is gone.
- A child process runs one source's work and the parent does not wait for it, so a long history fill never delays other sources. A child is stopped after 2 hours.
- core-api sends the site data floor without a UTC offset, so the collector reads an offset-less floor as UTC.

## Replaces in the 2026-10-13 spec

- "Pushes from the top of the current UTC hour to `LOCAL_HORIZON_HOURS` ahead", "every 5 minutes", and the 48-hour default.
- "Editing a rate in place re-prices the current hour and everything after it."
- "Backfilling local prices for dates before a source existed" under Not included.
