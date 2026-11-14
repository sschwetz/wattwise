# Actor API contract fixtures

Sample bodies for the routes under `/api/v1/actor` that the Actron Neo actor
calls. Two test suites read the same files, so a change to a route or to the
actor client that breaks the other side fails a test.

| File | Route |
|------|-------|
| `instances_list_entry.json` | one entry of `GET /actor/instances` |
| `schedules_list_entry.json` | one entry of `GET /actor/instances/{id}/schedules` |
| `context.json` | `GET /actor/instances/{id}/context`, including `sun` and `sun_estimated` |
| `run_created.json` | `POST /actor/instances/{id}/runs` response |
| `run_list_item.json` | one entry of `GET /actor/instances/{id}/runs` |
| `capabilities_request.json` | `PUT /actor/instances/{id}/capabilities` body |
| `readings_request.json` | `PUT /actor/instances/{id}/readings` body |

How the tests use them:

- `core-api/tests/test_actor_contract.py` builds each response with the real
  routes and checks that its keys and JSON value types match the fixture. It
  compares shapes, not values. It also sends the two request bodies and
  checks Core API accepts them.
- `actron-neo-actor/tests/test_contract.py` serves the response fixtures with
  respx and parses them through the real `CoreApiClient`. It checks that the
  actor builds request bodies of the same shape as the two request fixtures.

The shape check treats whole numbers and decimals as the same type. A list in
a fixture with the same length as the real one is compared item by item;
otherwise every real item is compared with the fixture's first item.

The `refresh_token` value is a placeholder. Never put a real token, key or
reading from a real site in these files.

## Condition rows

`conditions`, `restore.only_if` and `restore.early_if` hold rows of one of six
kinds. A row without `kind` is a reading row. All rows carry `enabled`.

| `kind` | Other fields |
|--------|--------------|
| none (reading) | `reading`, `op` (`gt` or `lt`), `value`, `target` |
| `months` | `op` (`in` or `not_in`), `values`: months 1-12 |
| `days` | `op` (`in` or `not_in`), `values`: days 0-6, Monday is 0 |
| `week` | `parity`: `odd` or `even` |
| `demand_window` | `op` (`active` or `not_active`), `window_id`: a window of the site, or null for any |
| `time_of_day` | `start` and `end`, each a time reference |

Months, days and weeks are the site's. Week 0 is the week holding 1970-01-01
(Mon 1969-12-29 to Sun 1970-01-04) and is even; weeks then alternate. A switched-off
row may have empty `values` or an unknown `window_id`.

A time reference is `{"kind": "clock", "time": "HH:MM"}` or
`{"kind": "sun", "event": "sunrise" or "sunset", "offset_minutes": -180 to 180}`.
`time_of_day` passes from `start` up to `end`. When `end` is not later than
`start` the span runs through midnight. `start` and `end` must differ.

`GET /actor/instances/{id}/schedules` entries also carry `limits`
(`min_gap_minutes` and `run_again_after`, a time reference or null) and `armed`.
`context.json` has `sun`, one `{date, sunrise, sunset}` entry per local date from
yesterday to two days ahead, and `sun_estimated`. Field meanings are in
[Actors](../../docs/actors.md#schedules).
