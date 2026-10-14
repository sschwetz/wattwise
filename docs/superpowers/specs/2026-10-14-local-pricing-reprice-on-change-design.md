# Local pricing: reprice when a window changes

Date: 2026-10-14

This amends `2026-10-14-local-pricing-daily-push-and-backfill-design.md`. Where the two differ, this document wins. The sections it replaces are listed at the end.

## Problem

The daily push and the fill write only what is missing, so a price that is already stored is never rewritten. That is correct for new sources and new directions, and wrong for a correction:

- An owner types 30 instead of 3.0 on a window dated last month. The fill writes the wrong rate over a month of history.
- Fixing the window (delete it and add it again, or end it and add a new one) does not fix the stored prices. The past keeps the wrong rate, tomorrow's prices are fixed at 23:59, and today's stay wrong.
- A source with stored prices cannot be deleted, so there is no way back.

Under the old 48-hour design a typo touched two days. It can now touch the whole history.

## Decisions

- When a window is created, replaced or deleted, core-api records where the change takes effect. The collector reprices from there to the end of tomorrow and then clears the record.
- The record is durable (in the database). A collector restart, a failed run or a typo fixed after a restart do not lose it.
- Repricing rewrites every direction of the source from the recorded instant. The collector's normal rules still limit each direction to when its windows were in effect and to the site's first data.
- Repricing overwrites prices that a window still prices. It does not remove prices for minutes that no window prices any more (a window deleted with no replacement). Stored prices are never deleted. This is the known gap from the first spec and stays.

## Core-api

### Table `local_reprice_markers`

| Column | Notes |
|---|---|
| `source_id` | Primary key, FK to `sources`. At most one row per source. |
| `reprice_from` | `DateTime(timezone=True)`, the earliest instant whose price may have changed. |
| `version` | Integer, starts at 1 and goes up by 1 on every change to the source's windows. |

One migration. Deleting a source deletes its marker in the same transaction, like its windows.

### Setting the marker

The window routes in `routers/local_rates.py` record a change with `mark_reprice(db, source_id, start)`. `start` is:

- **Create:** the new window's `effective_from`.
- **Delete:** the deleted window's `effective_from`.
- **Replace:** if only `effective_to` changed, the earlier of the old and new `effective_to` (a window left open ended counts as later than any date, so ending an open window marks the new end). Otherwise the earlier of the old and new `effective_from`.

`mark_reprice` keeps the earlier of the existing `reprice_from` and `start`, and adds 1 to `version`. It runs in the same transaction as the window change. Ending a window is the common edit, so its marker is the moment it ends, which keeps the repriced span short.

### Delivery and clearing

- `resolve_source_config` adds `"reprice": {"from": "<UTC ISO>", "version": <int>}` to a local source's config when a marker exists, and leaves the key out otherwise. The config for a local source stays exactly `timezone`, `rate_windows` and, when present, `reprice`.
- `POST /api/v1/sources/{source_id}/prices/reprice-marker/clear` with body `{"version": <int>}` deletes the marker only if its `version` equals the one sent, and returns 204 either way. A change made while the collector was repricing has raised the version, so the marker stays and the next run handles it. It uses the same collector-key and provider checks as the other price endpoints.

## local-pricing-collector

- `Triggers` gains `reprice_from: datetime | None` and `reprice_version: int | None`, set by the loop from the source's discovery config on every tick. A source with a marker is due, whatever its record says.
- The planner adds, for each direction that has windows, the range from `reprice_from` to the end of tomorrow. It is clamped like the other ranges (history start, window start, window end) and merged with them. It is pushed oldest chunk first with the other forward ranges.
- After every planned range has been pushed, the child calls the clear endpoint with the version it read. If any push or the clear call fails, the child reports failure, the marker stays, and the next tick starts again. Repricing is idempotent, so a repeat is safe, and an interrupted run leaves no hole because the marker is only cleared at the end.
- A marker in the future, or one that clamps to an empty range, is cleared without pushing.
- The windows fingerprint still triggers a catch-up. The catch-up fills what is missing and the marker rewrites what changed, in the same pass.

## Webapp

No change. The panel already offers Add, End and Delete. Fixing a typo is Delete then Add.

## Docs

`04-sources.md` ("Changing a rate" and "When changes take effect"): a change to a window reprices from where it takes effect to the end of tomorrow, within about a minute. Correcting a rate on a window dated last month reprices from last month. Deleting a window leaves the prices it wrote for any minute that no other window prices. The earlier statements that "the past keeps the old rate" and that edits reach only the next day's prices are replaced. `README.md` row updated.

## Testing

- Core-api: the migration creates the table; create, replace (rate change, pattern change, end only) and delete each set the right `reprice_from`; a second change keeps the earlier `reprice_from` and raises `version`; a source with no changes has no `reprice` key; the config key appears only for a local source; the clear endpoint clears on a matching version, keeps the marker on a stale one, returns 204 either way, and enforces the key and provider header; deleting a source deletes its marker.
- Collector planning: a marker adds a range from the marker to the end of tomorrow for every direction with windows; it is clamped to the history start and to ended windows; it merges with catch-up and scheduled ranges; a marker later than the end of tomorrow plans nothing.
- Collector loop: a marker starts a child even when the record says the source is caught up; success calls clear with the version read; a failed push leaves the marker uncleared; a failed clear reports failure; an edit during a run (version raised) survives the clear; a marker that clamps to an empty range pushes nothing and is still cleared.
- End to end in tests: the typo case (create a window with rate 30 dated 20 days ago, fill, delete it, add it with rate 3.0 dated the same day) ends with every stored price at 3.0 for the windows' span.

## Rollout order

1. Core-api: migration, model, marker setting in the window routes, config delivery, clear endpoint, tests.
2. Collector: client call, triggers, planner, loop and tests.
3. Docs and README.

## Not included

- Removing stored prices for minutes that no window prices (a deleted window with no replacement, or the unpriced stretch of an ended window).
- A webapp control to reprice by hand, or to choose the date.
- Repricing for changes to the site's timezone.
- Narrowing the repriced span to the minutes whose rate actually changed. A rate change on a window dated last month reprices from last month.

## Replaces in the 2026-10-14 daily-push spec

- "Editing a window changes prices from the next scheduled push. It does not reprice the current day."
- Under Not included: "Repricing the current day after an edit."
- The statement in the docs that the past keeps the old rate after a change.
