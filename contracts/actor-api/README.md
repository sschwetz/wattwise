# Actor API contract fixtures

Sample bodies for the routes under `/api/v1/actor` that the Actron Neo actor
calls. Two test suites read the same files, so a change to a route or to the
actor client that breaks the other side fails a test.

| File | Route |
|------|-------|
| `instances_list_entry.json` | one entry of `GET /actor/instances` |
| `schedules_list_entry.json` | one entry of `GET /actor/instances/{id}/schedules` |
| `context.json` | `GET /actor/instances/{id}/context` |
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
