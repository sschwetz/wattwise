from __future__ import annotations

import datetime
from collections.abc import Callable
from typing import Any, Final

import httpx

from actron_neo_actor.models import Context, Instance, Run, Schedule

PROVIDER = "actron_neo"
_TIMEOUT = 10

class _Unset:
    """Type of UNSET, the "argument not given" marker."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = _Unset()


class CoreApiError(Exception):
    """Raised on any failure talking to Core API: a non-success response, a
    transport failure or a body of an unexpected shape. Callers only catch
    this type (or CoreApiConflict)."""


class CoreApiConflict(CoreApiError):
    """A 409 response. `code` is the `code` field of the response body, if any."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def _error_code(response: httpx.Response) -> str | None:
    try:
        code = response.json().get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) else None


def _utc_iso(value: datetime.datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(datetime.UTC).isoformat()


def _parsed[T](what: str, body: object, build: Callable[[Any], T]) -> T:
    """Never put `body` in the message: instance bodies carry a refresh token."""
    try:
        return build(body)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CoreApiError(f"Unexpected {what} body") from exc


class CoreApiClient:
    def __init__(self, base_url: str, actor_key: str):
        self._base = f"{base_url.rstrip('/')}/api/v1/actor"
        self._headers = {"X-Actor-Key": actor_key, "X-Actor-Provider": PROVIDER}

    def _request(
        self, method: str, path: str, what: str, expected: int, *, json: object = None, params: dict | None = None
    ) -> object:
        """Returns the decoded JSON body, or None for a 204. Error messages
        carry the response text but never the request body."""
        try:
            response = httpx.request(
                method, f"{self._base}{path}", headers=self._headers, json=json, params=params, timeout=_TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise CoreApiError(f"Failed to reach Core API: {type(exc).__name__}") from exc
        if response.status_code != expected:
            message = f"Failed to {what}: {response.status_code} {response.text}"
            if response.status_code == 409:
                raise CoreApiConflict(message, _error_code(response))
            raise CoreApiError(message)
        if expected == 204:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise CoreApiError(f"Unexpected {what} body") from exc

    def list_instances(self) -> list[Instance]:
        body = self._request("GET", "/instances", "list instances", 200, params={"provider": PROVIDER})
        return _parsed("instances", body, lambda b: [Instance.from_json(i) for i in b])

    def patch_discovered(
        self, instance_id: int, config: dict | None = None, auth_state: dict | None | _Unset = UNSET
    ) -> None:
        """An omitted `auth_state` is not sent; an explicit None clears it."""
        payload: dict = {}
        if config is not None:
            payload["config"] = config
        if not isinstance(auth_state, _Unset):
            payload["auth_state"] = auth_state
        self._request(
            "PATCH", f"/instances/{instance_id}/discovered-config", "report discovered config", 204, json=payload
        )

    def put_capabilities(self, instance_id: int, capabilities: dict) -> None:
        self._request("PUT", f"/instances/{instance_id}/capabilities", "put capabilities", 204, json=capabilities)

    def set_alert(
        self, instance_id: int, code: str, message: str, severity: str = "failure", sticky: bool = False
    ) -> None:
        """Shows `message` on the Dashboard until clear_alert is called with the same code.

        `severity` is "failure", "partial" or "notice". A sticky alert is kept after the cause goes away until it is dismissed."""
        body = {"message": message, "severity": severity, "sticky": sticky}
        self._request("PUT", f"/instances/{instance_id}/alerts/{code}", "set alert", 204, json=body)

    def clear_alert(self, instance_id: int, code: str) -> None:
        self._request("DELETE", f"/instances/{instance_id}/alerts/{code}", "clear alert", 204)

    def get_schedules(self, instance_id: int) -> list[Schedule]:
        body = self._request("GET", f"/instances/{instance_id}/schedules", "fetch schedules", 200)
        return _parsed("schedules", body, lambda b: [Schedule.from_json(s, instance_id) for s in b])

    def set_schedule_armed(self, instance_id: int, schedule_id: int, armed: bool) -> None:
        """Clears the flag when a price or temperature trigger fires and sets it when the trigger may fire again."""
        self._request(
            "PATCH", f"/instances/{instance_id}/schedules/{schedule_id}", "set schedule armed", 204, json={"armed": armed}
        )

    def get_context(self, instance_id: int) -> Context:
        body = self._request("GET", f"/instances/{instance_id}/context", "fetch context", 200)
        return _parsed("context", body, Context.from_json)

    def put_readings(self, instance_id: int, state: dict) -> None:
        self._request("PUT", f"/instances/{instance_id}/readings", "put readings", 204, json={"state": state})

    def create_run(
        self,
        instance_id: int,
        schedule_id: int,
        kind: str,
        trigger_at: datetime.datetime,
        parent_run_id: int | None = None,
        snapshot: dict | None = None,
    ) -> Run:
        payload = {
            "schedule_id": schedule_id,
            "kind": kind,
            "trigger_at": _utc_iso(trigger_at),
            "parent_run_id": parent_run_id,
            "snapshot": snapshot,
        }
        body = self._request("POST", f"/instances/{instance_id}/runs", "create run", 201, json=payload)
        return _parsed("run", body, Run.from_json)

    def update_run(
        self,
        instance_id: int,
        run_id: int,
        outcome: str,
        message: str | None = None,
        attempts: int | None = None,
        snapshot: dict | None = None,
        steps: list[dict] | None = None,
    ) -> Run:
        """Sends the outcome and each of message, attempts, snapshot and steps that is not None.
        A None is left out rather than sent, so this call cannot clear a field."""
        given = {"message": message, "attempts": attempts, "snapshot": snapshot, "steps": steps}
        payload = {"outcome": outcome, **{k: v for k, v in given.items() if v is not None}}
        body = self._request("PATCH", f"/instances/{instance_id}/runs/{run_id}", "update run", 200, json=payload)
        return _parsed("run", body, Run.from_json)

    def _list_runs(self, instance_id: int, params: dict) -> list[Run]:
        body = self._request("GET", f"/instances/{instance_id}/runs", "list runs", 200, params=params)
        return _parsed("runs", body, lambda b: [Run.from_json(r) for r in b])

    def list_unfinished_runs(self, instance_id: int) -> list[Run]:
        return self._list_runs(instance_id, {"unfinished": "true"})

    def list_pending_test_runs(self, instance_id: int) -> list[Run]:
        return self._list_runs(instance_id, {"pending_test": "true"})

    def list_recent_runs(self, instance_id: int, since: datetime.datetime) -> list[Run]:
        return self._list_runs(instance_id, {"since": _utc_iso(since)})
