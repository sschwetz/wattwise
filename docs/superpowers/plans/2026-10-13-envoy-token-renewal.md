# Envoy Token Renewal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Envoy collector renews its own local-API token from Enlighten credentials stored on the source, so nobody visits the Enlighten page.

**Architecture:** `username` and `password` live in the source's `config` JSON next to `token`. The collector calls Enlighten (two POSTs) on an Envoy 401 and 14 days before the token's JWT `exp`, then writes the new token back through the existing `PATCH /sources/{id}/discovered-config`. The webapp's Envoy form gets two fields.

**Tech Stack:** Python 3.11+ (httpx, respx, pytest, uv), FastAPI + pydantic + SQLAlchemy, React + TypeScript + vitest.

**Spec:** `docs/superpowers/specs/2026-10-13-envoy-token-renewal-design.md`

## Global Constraints

- Enlighten login: `POST https://enlighten.enphaseenergy.com/login/login.json`, form fields `user[email]`, `user[password]`, response JSON key `session_id`.
- Token request: `POST https://entrez.enphaseenergy.com/tokens`, JSON `{"session_id", "serial_num", "username"}`.
- HTTP timeouts to Enlighten: 10 seconds. Normal CA verification (no custom SSL context).
- Renew when the token's `exp` is less than 14 days away.
- At most one Enlighten call per source per hour (3600 s). A skipped renewal (missing username, password or serial) does not start the wait.
- Never log the password, the token, or the session ID.
- Do not run `git commit`, `git add`, or `git push`. The user has not asked for commits.
- Prose (comments, docs, messages) follows `.claude/rules/avoid-ai-writing.md`.
- No `any` in TypeScript. Functions over 30 lines get split.
- Run tests from each package directory: `cd core-api && uv run pytest`, `cd envoy-collector && uv run pytest`, `cd webapp && npx vitest run`.

## Review Focus

- Source with no `token` key at all but with credentials and a stored serial: renewal still happens (Task 4 test).
- Wrong Enlighten password: one call per hour, not one per 60 s poll (Task 4 test).
- Enlighten login returns HTTP 200 with no `session_id` (its way of saying bad credentials): treated as a rejected login (Task 3 test).
- Token that is not a JWT, or has a non-numeric or missing `exp`: no crash, no renewal (Task 3 and Task 4 tests).
- Core API rejects the token write: the new token is still used this process (Task 4 test).
- No secret appears in any log line or exception message (Task 3 and Task 4 tests).

---

## File Structure

- Modify `core-api/core_api/schemas.py`: `DiscoveredSourceConfig` accepts `token`.
- Modify `core-api/core_api/routers/sources.py`: `username` is a sensitive config key.
- Modify `envoy-collector/envoy_collector/core_api_client.py`: `report_discovered_config` takes `token`.
- Create `envoy-collector/envoy_collector/enlighten_client.py`: Enlighten calls and JWT expiry parsing.
- Modify `envoy-collector/envoy_collector/main.py`: `renew_token`, auth-failure recovery, pre-expiry check, missing-token handling.
- Modify `webapp/src/pages/SourcesPage.tsx`: two new fields on `EnvoyConfigForm`.
- Modify `webapp/src/docs/04-sources.md`, `webapp/src/docs/08-troubleshooting.md`.
- Tests: `core-api/tests/test_collector_config.py`, `core-api/tests/test_sources.py`, `envoy-collector/tests/test_core_api_client.py`, `envoy-collector/tests/test_enlighten_client.py` (new), `envoy-collector/tests/test_main.py`, `webapp/src/pages/SourcesPage.test.tsx`.

---

### Task 1: Core API accepts a reported token and hides the username

**Files:**
- Modify: `core-api/core_api/schemas.py:405-409`
- Modify: `core-api/core_api/routers/sources.py:75`
- Test: `core-api/tests/test_collector_config.py`, `core-api/tests/test_sources.py`

**Interfaces:**
- Produces: `PATCH /api/v1/sources/{id}/discovered-config` accepts `{"token": "<str>"}` (merged into `config`). Non-owner source reads omit `username`, `password` and `token`.

- [ ] **Step 1: Write the failing tests**

Append to `core-api/tests/test_collector_config.py`, after `test_report_discovered_config_can_merge_both_fields_at_once`:

```python
def test_report_discovered_config_merges_token(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url, {"host": "envoy.local", "token": "old", "username": "u@example.com"})
    patch = client.patch(
        f"/api/v1/sources/{source_id}/discovered-config",
        json={"token": "new"},
        headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY},
    )
    assert patch.status_code == 204, patch.text

    get = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY})
    assert get.json()["config"] == {"host": "envoy.local", "token": "new", "username": "u@example.com"}
```

Append to `core-api/tests/test_sources.py`, after `test_list_sources_redacts_the_token_for_a_non_owner_viewer`:

```python
def test_get_source_redacts_enlighten_credentials_for_a_non_owner_viewer(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    config = {"host": "envoy.local", "token": "abc", "username": "u@example.com", "password": "pw"}
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": config},
        headers=auth(owner_id),
    ).json()["id"]
    viewer_id = _grant_viewer_access(db_url, site_id, "viewer-creds@example.com")

    viewer_view = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(viewer_id))
    assert viewer_view.json()["config"] == {"host": "envoy.local"}

    owner_view = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))
    assert owner_view.json()["config"] == config
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_collector_config.py::test_report_discovered_config_merges_token tests/test_sources.py::test_get_source_redacts_enlighten_credentials_for_a_non_owner_viewer -v`
Expected: the first fails with 422 (extra field forbidden); the second fails because `username` is returned to the viewer.

- [ ] **Step 3: Implement**

In `core-api/core_api/schemas.py`, change `DiscoveredSourceConfig`:

```python
class DiscoveredSourceConfig(BaseModel):
    model_config = {"extra": "forbid"}

    serial_num: str | None = None
    cert_fingerprint: str | None = None
    token: str | None = None
```

In `core-api/core_api/routers/sources.py`, change the set and extend its comment:

```python
# Device/provider credentials living in a source's config (an envoy source's
# local-API token and the Enlighten username/password the collector uses to
# renew it) must only reach the site owner: a read-only collaborator's
# SiteAccess grant is meant to expose energy data, not a credential that can
# reach the owner's device or Enphase account.
_SENSITIVE_CONFIG_KEYS = {"token", "api_key", "password", "secret", "username"}
```

(Replace the existing comment block directly above the set; keep the set on one line.)

- [ ] **Step 4: Run the full Core API suite**

Run: `cd core-api && uv run pytest -q`
Expected: all pass.

---

### Task 2: Collector client can report a token

**Files:**
- Modify: `envoy-collector/envoy_collector/core_api_client.py:40-61`
- Test: `envoy-collector/tests/test_core_api_client.py`

**Interfaces:**
- Produces: `report_discovered_config(base_url, source_id, collector_key, *, serial_num=None, cert_fingerprint=None, token=None) -> None`. Only non-None values are sent.

- [ ] **Step 1: Write the failing test**

Append to `envoy-collector/tests/test_core_api_client.py`:

```python
@respx.mock
def test_report_discovered_config_sends_token():
    route = respx.patch(f"{BASE_URL}/api/v1/sources/1/discovered-config").mock(return_value=httpx.Response(204))
    report_discovered_config(BASE_URL, 1, "my-key", token="new-token")
    assert route.calls[0].request.headers["X-Collector-Key"] == "my-key"
    assert json.loads(route.calls[0].request.content) == {"token": "new-token"}
```

Add `import json` to the imports at the top of that file if it is not there.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd envoy-collector && uv run pytest tests/test_core_api_client.py::test_report_discovered_config_sends_token -v`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'token'`.

- [ ] **Step 3: Implement**

Replace `report_discovered_config` in `core_api_client.py`:

```python
def report_discovered_config(
    base_url: str,
    source_id: int,
    collector_key: str,
    *,
    serial_num: str | None = None,
    cert_fingerprint: str | None = None,
    token: str | None = None,
) -> None:
    fields = {"serial_num": serial_num, "cert_fingerprint": cert_fingerprint, "token": token}
    payload = {k: v for k, v in fields.items() if v is not None}
    response = _request(
        "PATCH",
        f"{base_url}/api/v1/sources/{source_id}/discovered-config",
        headers={"X-Collector-Key": collector_key},
        json=payload,
        timeout=10,
    )
    if response.status_code != 204:
        raise CoreApiError(
            f"Failed to report discovered config: {response.status_code} {response.text}"
        )
```

- [ ] **Step 4: Run the file's tests**

Run: `cd envoy-collector && uv run pytest tests/test_core_api_client.py -q`
Expected: all pass.

---

### Task 3: Enlighten client and JWT expiry parser

**Files:**
- Create: `envoy-collector/envoy_collector/enlighten_client.py`
- Test: `envoy-collector/tests/test_enlighten_client.py` (new)

**Interfaces:**
- Produces:
  - `class EnlightenError(Exception)`; `class EnlightenAuthError(EnlightenError)` (login rejected).
  - `fetch_token(username: str, password: str, serial_num: str) -> str`
  - `token_expires_at(token: str | None) -> datetime.datetime | None` (timezone-aware UTC; None when unreadable).

- [ ] **Step 1: Write the failing tests**

Create `envoy-collector/tests/test_enlighten_client.py`:

```python
from __future__ import annotations

import base64
import datetime
import json

import httpx
import pytest
import respx

from envoy_collector.enlighten_client import (
    EnlightenAuthError,
    EnlightenError,
    fetch_token,
    token_expires_at,
)

LOGIN_URL = "https://enlighten.enphaseenergy.com/login/login.json"
TOKENS_URL = "https://entrez.enphaseenergy.com/tokens"


def make_jwt(payload: dict) -> str:
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    return f"{b64(b'{}')}.{b64(json.dumps(payload).encode())}.sig"


@respx.mock
def test_fetch_token_runs_login_then_token_request():
    login = respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={"session_id": "sess-1"}))
    tokens = respx.post(TOKENS_URL).mock(return_value=httpx.Response(200, text="the-token\n"))

    assert fetch_token("u@example.com", "pw", "123456789012") == "the-token"

    assert b"user%5Bemail%5D=u%40example.com" in login.calls[0].request.content
    assert b"user%5Bpassword%5D=pw" in login.calls[0].request.content
    assert json.loads(tokens.calls[0].request.content) == {
        "session_id": "sess-1",
        "serial_num": "123456789012",
        "username": "u@example.com",
    }


@respx.mock
def test_fetch_token_accepts_a_json_token_response():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={"session_id": "s"}))
    respx.post(TOKENS_URL).mock(return_value=httpx.Response(200, json={"token": "json-token"}))
    assert fetch_token("u", "p", "1") == "json-token"


@respx.mock
def test_login_200_without_session_id_is_a_rejected_login():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={"message": "User not found"}))
    with pytest.raises(EnlightenAuthError):
        fetch_token("u", "p", "1")


@respx.mock
def test_login_401_is_a_rejected_login():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(EnlightenAuthError):
        fetch_token("u", "p", "1")


@respx.mock
def test_login_with_non_json_body_is_a_rejected_login():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(EnlightenAuthError):
        fetch_token("u", "p", "1")


@respx.mock
def test_login_server_error_is_a_generic_error_not_an_auth_error():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(EnlightenError) as excinfo:
        fetch_token("u", "p", "1")
    assert not isinstance(excinfo.value, EnlightenAuthError)


@respx.mock
def test_token_endpoint_failure_raises_enlighten_error():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={"session_id": "s"}))
    respx.post(TOKENS_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(EnlightenError):
        fetch_token("u", "p", "1")


@respx.mock
def test_empty_token_body_raises_enlighten_error():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={"session_id": "s"}))
    respx.post(TOKENS_URL).mock(return_value=httpx.Response(200, text="  "))
    with pytest.raises(EnlightenError):
        fetch_token("u", "p", "1")


@respx.mock
def test_transport_failure_raises_enlighten_error_without_secrets():
    respx.post(LOGIN_URL).mock(side_effect=httpx.ConnectError("boom pw-secret"))
    with pytest.raises(EnlightenError) as excinfo:
        fetch_token("u@example.com", "pw-secret", "1")
    assert "pw-secret" not in str(excinfo.value)


@respx.mock
def test_error_messages_never_contain_the_password_or_session_id():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={"session_id": "sess-secret"}))
    respx.post(TOKENS_URL).mock(return_value=httpx.Response(500, text="sess-secret pw-secret"))
    with pytest.raises(EnlightenError) as excinfo:
        fetch_token("u", "pw-secret", "1")
    assert "sess-secret" not in str(excinfo.value)
    assert "pw-secret" not in str(excinfo.value)


def test_token_expires_at_reads_the_exp_claim():
    token = make_jwt({"exp": 1_800_000_000})
    assert token_expires_at(token) == datetime.datetime.fromtimestamp(1_800_000_000, tz=datetime.timezone.utc)


@pytest.mark.parametrize(
    "token",
    [None, "", "not-a-jwt", "a.b.c", "a.!!!.c", make_jwt({}), make_jwt({"exp": "soon"}), make_jwt({"exp": 1e30})],
)
def test_token_expires_at_returns_none_for_unreadable_tokens(token):
    assert token_expires_at(token) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd envoy-collector && uv run pytest tests/test_enlighten_client.py -v`
Expected: collection error, `ModuleNotFoundError: envoy_collector.enlighten_client`.

- [ ] **Step 3: Implement**

Create `envoy-collector/envoy_collector/enlighten_client.py`:

```python
from __future__ import annotations

import base64
import datetime
import json

import httpx

LOGIN_URL = "https://enlighten.enphaseenergy.com/login/login.json"
TOKENS_URL = "https://entrez.enphaseenergy.com/tokens"
_TIMEOUT_SECONDS = 10


class EnlightenError(Exception):
    """Any failure getting a token from Enlighten. Messages never include the
    password, session ID, or token."""


class EnlightenAuthError(EnlightenError):
    """Enlighten rejected the username/password."""


def _post(url: str, **kwargs) -> httpx.Response:
    try:
        return httpx.post(url, timeout=_TIMEOUT_SECONDS, **kwargs)
    except httpx.HTTPError as exc:
        # `from None`: the original exception text can echo request details.
        raise EnlightenError(f"Could not reach {httpx.URL(url).host}: {type(exc).__name__}") from None


def _login(username: str, password: str) -> str:
    response = _post(LOGIN_URL, data={"user[email]": username, "user[password]": password})
    if response.status_code in (401, 403):
        raise EnlightenAuthError("Enlighten rejected the username or password")
    if response.status_code != 200:
        raise EnlightenError(f"Enlighten login returned HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        body = None
    session_id = body.get("session_id") if isinstance(body, dict) else None
    if not session_id:
        # Enlighten answers 200 with an error message instead of a session
        # when the credentials are wrong.
        raise EnlightenAuthError("Enlighten login did not return a session; check the username and password")
    return session_id


def _parse_token_body(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    token = body.get("token") if isinstance(body, dict) else response.text
    token = (token or "").strip()
    if not token:
        raise EnlightenError("Enlighten returned an empty token")
    return token


def fetch_token(username: str, password: str, serial_num: str) -> str:
    """Logs in to Enlighten and requests a one-year Envoy token for `serial_num`."""
    session_id = _login(username, password)
    response = _post(TOKENS_URL, json={"session_id": session_id, "serial_num": serial_num, "username": username})
    if response.status_code != 200:
        raise EnlightenError(f"Enlighten token request returned HTTP {response.status_code}")
    return _parse_token_body(response)


def token_expires_at(token: str | None) -> datetime.datetime | None:
    """Reads the `exp` claim from a JWT without verifying the signature (the
    collector only needs the date). None if the token isn't a readable JWT."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload))["exp"]
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            return None
        return datetime.datetime.fromtimestamp(exp, tz=datetime.timezone.utc)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
```

- [ ] **Step 4: Run the tests**

Run: `cd envoy-collector && uv run pytest tests/test_enlighten_client.py -v`
Expected: all pass. If `"a.!!!.c"` does not return None, check that `urlsafe_b64decode` raising `binascii.Error` (a `ValueError`) is caught; do not widen the except to `Exception`.

---

### Task 4: Renewal in the poll loop

**Files:**
- Modify: `envoy-collector/envoy_collector/main.py`
- Test: `envoy-collector/tests/test_main.py`

**Interfaces:**
- Consumes: `fetch_token`, `token_expires_at`, `EnlightenError`, `EnlightenAuthError` (Task 3); `report_discovered_config(..., token=...)` (Task 2).
- Produces in `main.py`:
  - `renew_token(settings: Settings, source_id: int, config: dict) -> dict` returns `config` unchanged when renewal is skipped or fails, else a copy with the new `token`.
  - `_token_expiring(config: dict, now: datetime.datetime) -> bool`
  - `_recover_from_auth_failure(settings, source_id, config, ts) -> tuple[dict, list[dict] | None]`
  - Module constants `RENEWAL_RETRY_SECONDS = 3600`, `RENEWAL_WINDOW = datetime.timedelta(days=14)`, dict `_last_renewal_attempt: dict[int, float]`.

- [ ] **Step 1: Write the failing tests**

Add to the imports at the top of `envoy-collector/tests/test_main.py`:

```python
import datetime

from envoy_collector.enlighten_client import EnlightenAuthError, EnlightenError
from tests.test_enlighten_client import make_jwt
```

Add this fixture and tests at the end of the file:

```python
@pytest.fixture(autouse=True)
def _clear_renewal_state():
    main_module._last_renewal_attempt.clear()
    yield
    main_module._last_renewal_attempt.clear()


CREDS = {"username": "u@example.com", "password": "pw-secret", "serial_num": "123456789012"}


def _stub_reporting(monkeypatch):
    reported = []
    monkeypatch.setattr(
        main_module, "report_discovered_config", lambda base_url, source_id, key, **kwargs: reported.append(kwargs)
    )
    return reported


def test_renew_token_fetches_and_reports_a_new_token(monkeypatch):
    reported = _stub_reporting(monkeypatch)
    monkeypatch.setattr(main_module, "fetch_token", lambda u, p, s: "fresh")
    config = {"host": "h", "token": "old", **CREDS}

    result = main_module.renew_token(SETTINGS, 1, config)

    assert result["token"] == "fresh"
    assert config["token"] == "old"  # input not mutated
    assert reported == [{"token": "fresh"}]


@pytest.mark.parametrize("missing", ["username", "password", "serial_num"])
def test_renew_token_skips_without_credentials_or_serial(monkeypatch, missing):
    calls = []
    monkeypatch.setattr(main_module, "fetch_token", lambda *a: calls.append(a) or "fresh")
    config = {k: v for k, v in CREDS.items() if k != missing}

    assert main_module.renew_token(SETTINGS, 1, config) == config
    assert calls == []
    assert 1 not in main_module._last_renewal_attempt  # a skip does not start the wait


def test_renew_token_waits_an_hour_after_a_failed_login(monkeypatch, caplog):
    calls = []

    def reject(u, p, s):
        calls.append(1)
        raise EnlightenAuthError("Enlighten rejected the username or password")

    monkeypatch.setattr(main_module, "fetch_token", reject)
    clock = {"now": 1000.0}
    monkeypatch.setattr(main_module.time, "monotonic", lambda: clock["now"])
    config = {"token": "old", **CREDS}

    with caplog.at_level("ERROR"):
        assert main_module.renew_token(SETTINGS, 1, config) == config
        clock["now"] += 60
        assert main_module.renew_token(SETTINGS, 1, config) == config
        assert len(calls) == 1
        clock["now"] += 3600
        main_module.renew_token(SETTINGS, 1, config)
    assert len(calls) == 2
    assert not any("pw-secret" in m for m in caplog.messages)


def test_renew_token_returns_config_unchanged_on_enlighten_outage(monkeypatch):
    def down(u, p, s):
        raise EnlightenError("Enlighten login returned HTTP 503")

    monkeypatch.setattr(main_module, "fetch_token", down)
    config = {"token": "old", **CREDS}
    assert main_module.renew_token(SETTINGS, 1, config) == config


def test_renew_token_keeps_the_new_token_when_core_api_rejects_the_write(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_token", lambda u, p, s: "fresh")

    def fail(*a, **k):
        raise CoreApiError("down")

    monkeypatch.setattr(main_module, "report_discovered_config", fail)
    assert main_module.renew_token(SETTINGS, 1, {"token": "old", **CREDS})["token"] == "fresh"


def test_poll_once_renews_after_a_double_401_and_retries(monkeypatch):
    _stub_reporting(monkeypatch)
    monkeypatch.setattr(main_module, "fetch_token", lambda u, p, s: "fresh")
    seen = []

    def fake_fetch_production(host, token, cert_fingerprint):
        seen.append(token)
        if token != "fresh":
            raise EnvoyAuthError("nope")
        return {"production": []}

    monkeypatch.setattr(main_module, "fetch_production", fake_fetch_production)
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    monkeypatch.setattr(main_module, "map_production_readings", lambda data, ts, **_kwargs: [])
    monkeypatch.setattr(main_module, "map_battery_readings", lambda data, ts: [])
    stored = {"host": "envoy.local", "token": "old", "cert_fingerprint": "pinned", **CREDS}
    monkeypatch.setattr(main_module, "fetch_source_config", lambda base_url, source_id, key: dict(stored))
    pushed = []
    monkeypatch.setattr(main_module, "push_energy_readings", lambda *a, **k: pushed.append(a))

    result = main_module.poll_once(SETTINGS, 1, dict(stored))

    assert result["token"] == "fresh"
    assert seen == ["old", "old", "fresh"]
    assert len(pushed) == 1


def test_poll_once_renews_a_source_with_no_token_key(monkeypatch):
    _stub_reporting(monkeypatch)
    monkeypatch.setattr(main_module, "fetch_token", lambda u, p, s: "fresh")
    seen = []

    def fake_fetch_production(host, token, cert_fingerprint):
        seen.append(token)
        if token != "fresh":
            raise EnvoyAuthError("nope")
        return {"production": []}

    monkeypatch.setattr(main_module, "fetch_production", fake_fetch_production)
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    monkeypatch.setattr(main_module, "map_production_readings", lambda data, ts, **_kwargs: [])
    monkeypatch.setattr(main_module, "map_battery_readings", lambda data, ts: [])
    stored = {"host": "envoy.local", "cert_fingerprint": "pinned", **CREDS}  # no "token" key
    monkeypatch.setattr(main_module, "fetch_source_config", lambda base_url, source_id, key: dict(stored))
    monkeypatch.setattr(main_module, "push_energy_readings", lambda *a, **k: None)

    result = main_module.poll_once(SETTINGS, 1, dict(stored))

    assert result["token"] == "fresh"
    assert seen[0] == ""  # first attempt went out with an empty token and got the 401


def test_poll_once_falls_back_to_the_manual_message_when_renewal_fails(monkeypatch, caplog):
    def reject(u, p, s):
        raise EnlightenAuthError("Enlighten rejected the username or password")

    monkeypatch.setattr(main_module, "fetch_token", reject)
    monkeypatch.setattr(
        main_module, "fetch_production", lambda host, token, cert_fingerprint: (_ for _ in ()).throw(EnvoyAuthError("x"))
    )
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    stored = {"host": "envoy.local", "token": "old", "cert_fingerprint": "pinned", **CREDS}
    monkeypatch.setattr(main_module, "fetch_source_config", lambda base_url, source_id, key: dict(stored))

    with caplog.at_level("ERROR"):
        result = main_module.poll_once(SETTINGS, 1, dict(stored))

    assert result == stored
    assert any("entrez-auth-token" in m for m in caplog.messages)
    assert not any("pw-secret" in m for m in caplog.messages)


NOW = datetime.datetime(2026, 10, 13, tzinfo=datetime.timezone.utc)


@pytest.mark.parametrize(
    ("days_left", "expected"), [(13, True), (14.5, False), (-1, True), (400, False)]
)
def test_token_expiring_uses_a_14_day_window(days_left, expected):
    exp = (NOW + datetime.timedelta(days=days_left)).timestamp()
    assert main_module._token_expiring({"token": make_jwt({"exp": exp})}, NOW) is expected


@pytest.mark.parametrize("token", [None, "", "opaque-token", make_jwt({"exp": "soon"}), make_jwt({})])
def test_token_expiring_ignores_unreadable_tokens(token):
    assert main_module._token_expiring({"token": token}, NOW) is False


def test_refresh_sources_renews_a_token_close_to_expiry(monkeypatch):
    soon = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3)).timestamp()
    expiring = {"host": "h", "token": make_jwt({"exp": soon}), "cert_fingerprint": "pinned", **CREDS}
    monkeypatch.setattr(
        main_module, "fetch_collector_sources", lambda base_url, key: [{"source_id": 1, "config": dict(expiring)}]
    )
    _stub_reporting(monkeypatch)
    monkeypatch.setattr(main_module, "fetch_token", lambda u, p, s: "fresh")

    refreshed = main_module._refresh_sources(SETTINGS, {})

    assert refreshed[1]["token"] == "fresh"


def test_refresh_sources_leaves_a_healthy_token_alone(monkeypatch):
    later = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=200)).timestamp()
    healthy = {"host": "h", "token": make_jwt({"exp": later}), "cert_fingerprint": "pinned", **CREDS}
    monkeypatch.setattr(
        main_module, "fetch_collector_sources", lambda base_url, key: [{"source_id": 1, "config": dict(healthy)}]
    )
    calls = []
    monkeypatch.setattr(main_module, "fetch_token", lambda *a: calls.append(a) or "fresh")

    assert main_module._refresh_sources(SETTINGS, {})[1]["token"] == healthy["token"]
    assert calls == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd envoy-collector && uv run pytest tests/test_main.py -q`
Expected: failures/errors, e.g. `AttributeError: module 'envoy_collector.main' has no attribute '_last_renewal_attempt'`. Existing tests still pass.

- [ ] **Step 3: Implement**

In `envoy-collector/envoy_collector/main.py`:

3a. Imports. Add `EnlightenAuthError, EnlightenError, fetch_token, token_expires_at` from `envoy_collector.enlighten_client`:

```python
from envoy_collector.enlighten_client import EnlightenAuthError, EnlightenError, fetch_token, token_expires_at
```

3b. After `logger = logging.getLogger("envoy_collector")`, add:

```python
# Renew when the token has less than this left. Tokens last a year.
RENEWAL_WINDOW = datetime.timedelta(days=14)
# Minimum gap between Enlighten calls for one source, so a wrong password
# can't lock the owner's Enlighten account by retrying every poll.
RENEWAL_RETRY_SECONDS = 3600
_last_renewal_attempt: dict[int, float] = {}
```

3c. Replace the two `config["token"]` reads (in `_fetch_battery_json_or_none` and `_fetch_and_map`) with `config.get("token", "")`, so a source with no token still reaches the Envoy and gets the 401 that starts renewal.

3d. Add after `_renewal_message`:

```python
def renew_token(settings: Settings, source_id: int, config: dict) -> dict:
    """Gets a new token from Enlighten and reports it to Core API. Returns
    `config` unchanged when renewal is skipped (missing credentials or serial,
    or tried within the last hour) or fails."""
    username, password, serial_num = config.get("username"), config.get("password"), config.get("serial_num")
    if not (username and password and serial_num):
        return config
    now = time.monotonic()
    last = _last_renewal_attempt.get(source_id)
    if last is not None and now - last < RENEWAL_RETRY_SECONDS:
        return config
    _last_renewal_attempt[source_id] = now
    try:
        token = fetch_token(username, password, serial_num)
    except EnlightenAuthError as exc:
        logger.error("Could not renew the Envoy token for source %s: %s. Update the Enlighten credentials.", source_id, exc)
        return config
    except EnlightenError as exc:
        logger.error("Could not renew the Envoy token for source %s: %s. Will retry in an hour.", source_id, exc)
        return config
    try:
        report_discovered_config(settings.core_api_url, source_id, settings.collector_key, token=token)
    except CoreApiError:
        logger.exception("Renewed the Envoy token for source %s but could not save it to Core API", source_id)
    logger.info("Renewed the Envoy token for source %s", source_id)
    return {**config, "token": token}


def _token_expiring(config: dict, now: datetime.datetime) -> bool:
    expires_at = token_expires_at(config.get("token"))
    return expires_at is not None and expires_at - now < RENEWAL_WINDOW
```

3e. Add `_recover_from_auth_failure` above `poll_once`, and shrink `poll_once` to use it:

```python
def _recover_from_auth_failure(
    settings: Settings, source_id: int, config: dict, ts: datetime.datetime
) -> tuple[dict, list[dict] | None]:
    """Runs after the Envoy answered 401: refetch the config (the token may
    have been edited), then renew from Enlighten if that still fails. Returns
    (config to use next cycle, readings or None if this cycle failed)."""
    logger.warning("Envoy rejected our token for source %s; refetching config from Core API", source_id)
    try:
        config = fetch_source_config(settings.core_api_url, source_id, settings.collector_key)
    except CoreApiError:
        logger.exception("Failed to refetch source config after Envoy auth failure for source %s", source_id)
        return config, None
    try:
        return config, _fetch_and_map(config, ts)
    except EnvoyAuthError:
        pass
    except Exception:
        logger.exception("Failed to poll Envoy this cycle (after config refetch) for source %s", source_id)
        return config, None
    renewed = renew_token(settings, source_id, config)
    if renewed.get("token") == config.get("token"):
        logger.error(_renewal_message(config))
        return config, None
    try:
        return renewed, _fetch_and_map(renewed, ts)
    except EnvoyAuthError:
        logger.error(_renewal_message(renewed))
    except Exception:
        logger.exception("Failed to poll Envoy this cycle (after token renewal) for source %s", source_id)
    return renewed, None
```

In `poll_once`, replace the whole `except EnvoyAuthError:` block (the one that refetches config and has the nested try/excepts) with:

```python
    except EnvoyAuthError:
        config, readings = _recover_from_auth_failure(settings, source_id, config, ts)
        if readings is None:
            return config
```

Keep the trailing `except Exception:` and the `push_energy_readings` block as they are.

3f. In `_refresh_sources`, inside the `for entry in discovered:` loop, after the `cert_fingerprint` discovery block, add:

```python
        if _token_expiring(refreshed[source_id], datetime.datetime.now(datetime.timezone.utc)):
            refreshed[source_id] = renew_token(settings, source_id, refreshed[source_id])
```

- [ ] **Step 4: Run the collector suite**

Run: `cd envoy-collector && uv run pytest -q`
Expected: all pass, including the pre-existing `test_poll_once_*` tests. If `test_poll_once_logs_and_returns_unchanged_config_when_still_401_after_refetch` fails, check that the no-credentials path still logs `_renewal_message(config)` and returns the refetched config.

---

### Task 5: Webapp form fields

**Files:**
- Modify: `webapp/src/pages/SourcesPage.tsx:163-199`
- Test: `webapp/src/pages/SourcesPage.test.tsx`

**Interfaces:**
- Produces: `EnvoyConfigForm` saves `{ host, token, username, password, invert_consumption_ct }`. Labels: `Enlighten email` (text) and `Enlighten password` (`type="password"`).

- [ ] **Step 1: Update and add tests**

In `SourcesPage.test.tsx`, the existing test "shows a guided envoy config form..." asserts an exact PATCH body. Change that expected body to:

```ts
body: JSON.stringify({
  config: { host: "envoy.local", token: "new-token", username: "", password: "", invert_consumption_ct: false },
}),
```

Find the other existing tests that assert an envoy config PATCH body (search the file for `invert_consumption_ct`) and add `username: "", password: ""` before `invert_consumption_ct` in each expected body, keeping key order `host, token, username, password, invert_consumption_ct`.

Add this test after the "shows a guided envoy config form" test:

```tsx
  it("lets the owner store Enlighten credentials for automatic token renewal", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([
        {
          id: 1,
          site_id: 7,
          kind: "energy",
          provider: "envoy",
          enabled: true,
          config: { host: "envoy.local", username: "me@example.com", password: "pw" },
        },
      ])
      .mockResolvedValueOnce({
        id: 1,
        site_id: 7,
        kind: "energy",
        provider: "envoy",
        enabled: true,
        config: { host: "envoy.local", username: "me@example.com", password: "new-pw" },
      });
    renderPage();
    await userEvent.click(await screen.findByText("Edit config"));

    expect(screen.getByLabelText("Enlighten email")).toHaveValue("me@example.com");
    expect(screen.getByLabelText("Enlighten email")).toHaveAttribute("type", "text");
    expect(screen.getByLabelText("Enlighten password")).toHaveValue("pw");
    expect(screen.getByLabelText("Enlighten password")).toHaveAttribute("type", "password");
    expect(screen.getByText(/renew the token automatically/i)).toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText("Enlighten password"));
    await userEvent.type(screen.getByLabelText("Enlighten password"), "new-pw");
    await userEvent.click(screen.getByText("Save config"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/sources/1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          config: {
            host: "envoy.local",
            token: "",
            username: "me@example.com",
            password: "new-pw",
            invert_consumption_ct: false,
          },
        }),
      }),
    );
  });
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd webapp && npx vitest run src/pages/SourcesPage.test.tsx`
Expected: the new test and the updated body assertions fail.

- [ ] **Step 3: Implement**

In `SourcesPage.tsx`, change `EnvoyConfigForm`:

```tsx
  const [host, setHost] = useState((config.host as string) ?? "");
  const [token, setToken] = useState((config.token as string) ?? "");
  const [username, setUsername] = useState((config.username as string) ?? "");
  const [password, setPassword] = useState((config.password as string) ?? "");
  const [invertConsumptionCt, setInvertConsumptionCt] = useState(Boolean(config.invert_consumption_ct));

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    onSave({ host, token, username, password, invert_consumption_ct: invertConsumptionCt });
  }
```

and in the JSX, directly after the Token `ConfigField`, add:

```tsx
      <ConfigField field={{ label: "Enlighten email", value: username, onChange: setUsername }} />
      <ConfigField field={{ label: "Enlighten password", value: password, onChange: setPassword, type: "password" }} />
      <p className="text-ink-muted">
        Optional. With both filled in, the collector can renew the token automatically, so Token can stay empty.
      </p>
```

- [ ] **Step 4: Run the webapp checks**

Run: `cd webapp && npx vitest run && npx tsc -b --noEmit`
Expected: all tests pass, no type errors.

---

### Task 6: User docs

**Files:**
- Modify: `webapp/src/docs/04-sources.md:55-67`
- Modify: `webapp/src/docs/08-troubleshooting.md:15-18`

- [ ] **Step 1: Edit `04-sources.md`**

Read lines 45-72 first. In the field table, change the **Token** row to say it is optional when the Enlighten fields are filled in, and add two rows:

```
| **Enlighten email** | Optional. The email you use to sign in to Enlighten (enlighten.enphaseenergy.com). |
| **Enlighten password** | Optional. Shown as dots while you type. |
```

Rewrite the "Getting the token" section so it opens with the automatic route, then keeps the existing manual steps as the fallback:

```
### Getting the token

The simplest route is to fill in **Enlighten email** and **Enlighten password** and leave **Token** empty. The collector finds your Envoy's serial number, signs in to Enlighten, and fetches a token. It renews the token the same way 14 days before it expires. Your email and password are stored in the WattWise database next to the token, and only the site owner can see them.

If you would rather not store your Enlighten password, get the token by hand:

(keep the existing numbered steps here unchanged)

Tokens last about a year. Without stored credentials, readings stop when one expires, and you repeat the steps above.
```

Do not use "seamlessly", "robust" or other words from `.claude/rules/avoid-ai-writing.md`.

- [ ] **Step 2: Edit `08-troubleshooting.md`**

Read lines 8-26. In the checklist row about the token (line 18), change the text to: `Tokens last about a year. If **Enlighten email** and **Enlighten password** are set, the collector renews the token itself. If readings still stop, check the collector log for "Could not renew the Envoy token", usually a wrong Enlighten password. Otherwise [get a new one](04-sources.md#getting-the-token).` In the checklist row at line 15, say Host must be set, and Token or both Enlighten fields.

- [ ] **Step 3: Check the Help viewer still builds**

Run: `cd webapp && npx vitest run && npx tsc -b --noEmit`
Expected: pass. Open `04-sources.md` and confirm the `#getting-the-token` anchor still exists.

---

## Final verification

- [ ] Run all three suites: `cd core-api && uv run pytest -q`, `cd envoy-collector && uv run pytest -q`, `cd webapp && npx vitest run`.
- [ ] `grep -rn "pw-secret\|password" envoy-collector/envoy_collector/` shows `password` used only as a variable name and in `config.get("password")`, never inside a log call's arguments.
- [ ] Do not commit. Report the changed files and test counts.
