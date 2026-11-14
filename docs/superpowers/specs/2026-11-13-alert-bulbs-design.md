# Alert bulbs and kept notices

## Why

An Envoy firmware upgrade replaced the certificate on 2026-11-13. The collector re-pinned it and wrote one log line. The line was lost when the container restarted, and the Dashboard showed nothing. Re-pins matter, so they need a record that stays until someone dismisses it.

Two other things bother the owner about the "Needs attention" tile:

- A problem that clears vanishes at once, so a short outage leaves no trace.
- Each alert takes four lines (label, source, a long message with an IP address, time) plus a link. The tile heading already says it needs attention.

## Goals

- Show each alert as one line with a bulb, on the Dashboard.
- Keep a cleared transient alert for one hour, shown as an unlit bulb.
- Keep certificate and firmware notices until a user dismisses them.
- Tint the tile with the colour of the most serious unresolved bulb.
- Keep the look gentle: slow pulse, no flashing, no large text.

## Non-goals

- No alert history page and no email on re-pin.
- No per-user dismissal. A dismissal removes the notice for the whole site.
- Active failures cannot be dismissed. They clear when their cause goes away.

## Anti-goals

- A tile that draws the eye when nothing is wrong now. Cleared and blue rows must not turn the tile red or amber.
- A pulse faster than about 3 seconds, or any hard blink.

## What the user sees

```
Needs attention (2)                                              tile tinted red
 ● 2026-11-13 20:12  Weatherlink Live - Cannot connect to the API    [Open Collectors]
 ● 2026-11-13 19:28  Envoy - Firmware updated, certificate re-pinned [Dismiss]
 ◯ 2026-11-13 18:40  Envoy - Meters unavailable                      [Open Collectors]
```

Row fields, left to right: bulb, `YYYY-MM-DD HH:MM` in the site's time zone, `Source - message`, one button on the right.

Bulbs (treatment B from the dot samples, pulse length 3s):

| Bulb | Meaning | Pulses |
|------|---------|--------|
| Red | Failure, or a re-pin caused by a firmware upgrade | yes |
| Amber | Partial failure | yes |
| Pastel blue | Notice | no |
| Grey centre, ring in the original colour | Cleared | no |

Red and amber pulse because they are live problems. Blue is lit and still. Red firmware notices also pulse until dismissed. With reduced motion on, nothing pulses.

Tile colour: red if any unresolved red bulb exists, else amber if any unresolved amber, else blue if any unresolved blue, else neutral. A cleared bulb counts as resolved. When only cleared rows remain, the tile stays for their hour and uses the neutral colour.

Button: "Open Collectors" for source and actor alerts, "Open Schedule" for schedule failures, "Dismiss" for kept notices.

## Notice rules (Envoy collector)

| Event | Bulb | Kept until dismissed |
|-------|------|----------------------|
| Certificate changed, firmware differs from the stored version, collector re-pins | Red | yes |
| Certificate changed, old certificate within 100 days of expiry, collector re-pins | Pastel blue | yes |
| Firmware version differs from the stored one and the certificate did not change | Pastel blue | yes |
| Collector refuses to re-pin | Red | no, clears when the cause goes away (existing behaviour) |

If a re-pin matches both the firmware and expiry rules, the firmware rule wins. A first-ever firmware read, with nothing stored, raises no notice. A notice is raised once, at the event, with `since` set to the event time.

## Data model

`alerts` gains three columns. Existing rows get the defaults and behave as they do today.

- `severity`: `failure` (default), `partial` or `notice`. Maps to red, amber and blue.
- `cleared_at`: nullable timestamp. Set when a service clears a transient alert.
- `sticky`: boolean, default false. Sticky rows are kept until a user dismisses them.

### Raise and clear rules (`set_alert`, `clear_alert`)

| Situation | Result |
|-----------|--------|
| Raise, no row | Insert. `since` is now. |
| Raise, row exists, `cleared_at` is null (still unresolved) | Update message and severity. Keep `since`. |
| Raise, row exists, `cleared_at` set (resolved) | Replace the resolved row: `since` is now, `cleared_at` is null, new message and severity. |
| Raise with `sticky=true`, any existing row | Replace it: `since` is now, `cleared_at` is null. |
| Clear, row is not sticky and `cleared_at` is null | Set `cleared_at` to now. |
| Clear, no row or already cleared | Nothing. |
| Clear, row is sticky | Nothing. Only a user removes it. |

The collector clears an alert when the problem ends. A repeat of that problem inside the hour starts a new alert at the current time. A problem that has not resolved never moves its start time.

### Reads

`current_failures` leaves out rows with `cleared_at` more than one hour ago. It needs no cleanup job, because a `(subject, code)` pair has one row and a repeat reuses it.

### API

- `PUT /sources/{id}/alerts/{code}` and `PUT /instances/{id}/alerts/{code}` accept optional `severity` and `sticky` in the body, defaulting to `failure` and false.
- `DELETE /sites/{site_id}/actors/alerts/{alert_id}` dismisses a sticky alert. It uses `require_site_access`, returns 404 for another site's alert and 409 for a non-sticky one.
- `ActorFailure` gains `severity`, `alert_id` (null for schedule failures), `cleared_at` and `sticky`.

## Which alerts are amber

Partial means the source still delivers some data. Meters unavailable (Envoy), ISS and barometer missing (WeatherLink) and schedule runs with a partial outcome are amber. Every other existing alert code is red. The builder confirms the list against the codes in each collector.

## Messages

Collectors send one short line, with no IP address and no advice. Example: `Cannot connect to the API`. The row prepends the source name. Detail stays in the collector log. Schedule failures drop the long labels in `KIND_LABEL` and use a short phrase (`Run failed`, `Still off`, `Not reporting`, `Needs sign-in`).

## Approaches considered

| Approach | Verdict |
|----------|---------|
| A. Extend `alerts` with `severity`, `cleared_at`, `sticky` | Chosen. One table, one list for the tile. Needs a migration and the clear rules above. |
| B. Separate `notices` table for kept items | One table per kind is simpler to read, but the API and tile merge two lists and `alerts` still needs the cleared state. |
| C. Event log with state derived on read | Gives history, which nobody asked for, and makes every tile load do more work. |

## Webapp

`FailuresTile` keeps its data hook and gets a `useDismissAlert` mutation that invalidates the failures query. The row component changes to the one-line layout. Bulb styles come from treatment B in the dot samples, with `--period: 3s`. The pastel blue token is added to the theme in light and dark values. The tile glow class, now fixed to danger red, becomes one of three by tile colour.

Ordering: unresolved rows first, by severity (red, amber, blue), newest first within each. Cleared rows follow. The heading count is the number of unresolved rows.

## Tests

- Core API: the six raise and clear cases, the one-hour cutoff, dismiss auth, 404 and 409, the migration defaults.
- Envoy collector: severity and sticky for each notice rule, the firmware-rule-wins case, no notice on first firmware read, no repeat notice on later polls.
- Webapp: one-line row text, tile colour for each mix of bulbs, cleared rows not counting, Dismiss calls the endpoint.

## Left to the builder

- Exact pastel blue and amber values for both themes.
- Whether `partial` applies to local-pricing marker and timezone alerts. Default to red.
- Wording of each shortened message.
- Docs updates in `docs/core-api.md`, `data-model.md`, `collectors.md` and `webapp.md`.
