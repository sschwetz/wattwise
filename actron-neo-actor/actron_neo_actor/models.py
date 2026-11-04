"""Typed views of the Core API actor JSON. `from_json` raises KeyError,
TypeError or ValueError on a body of the wrong shape; the client turns those
into CoreApiError."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field


def parse_utc(value: str) -> datetime.datetime:
    """An aware instant as UTC. A value with no offset is rejected."""
    parsed = datetime.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp has no offset: {value!r}")
    return parsed.astimezone(datetime.UTC)


def _parse_utc_or_none(value: str | None) -> datetime.datetime | None:
    return None if value is None else parse_utc(value)


@dataclass(frozen=True)
class Instance:
    instance_id: int
    site_id: int
    name: str | None
    timezone: str
    restore_only: bool
    # The config holds the provider refresh token, so it stays out of repr.
    config: dict = field(repr=False)

    @classmethod
    def from_json(cls, data: dict) -> Instance:
        return cls(
            instance_id=data["instance_id"],
            site_id=data["site_id"],
            name=data["name"],
            timezone=data["timezone"],
            restore_only=data["restore_only"],
            config=dict(data["config"]),
        )


@dataclass(frozen=True)
class Trigger:
    """One of window_start, window_end, time or price_above; the fields that
    do not belong to the type stay None."""

    type: str
    window_id: int | None = None
    time: str | None = None
    days_of_week: list[int] | None = None
    c_per_kwh: float | None = None

    @classmethod
    def from_json(cls, data: dict) -> Trigger:
        return cls(
            type=data["type"],
            window_id=data.get("window_id"),
            time=data.get("time"),
            days_of_week=data.get("days_of_week"),
            c_per_kwh=data.get("c_per_kwh"),
        )


@dataclass(frozen=True)
class Condition:
    reading: str
    op: str
    value: float
    target: str | None = None

    @classmethod
    def from_json(cls, data: dict) -> Condition:
        return cls(reading=data["reading"], op=data["op"], value=data["value"], target=data.get("target"))


@dataclass(frozen=True)
class ActionSpec:
    id: str
    params: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict) -> ActionSpec:
        return cls(id=data["id"], params=dict(data.get("params") or {}))


@dataclass(frozen=True)
class RestoreSpec:
    enabled: bool
    when: str
    minutes: int | None
    time: str | None
    only_if: list[Condition]
    early_if: list[Condition]

    @classmethod
    def from_json(cls, data: dict) -> RestoreSpec:
        return cls(
            enabled=data["enabled"],
            when=data["when"],
            minutes=data.get("minutes"),
            time=data.get("time"),
            only_if=[Condition.from_json(c) for c in data.get("only_if", [])],
            early_if=[Condition.from_json(c) for c in data.get("early_if", [])],
        )


@dataclass(frozen=True)
class RetrySpec:
    max_attempts: int
    interval_seconds: int
    give_up_minutes: int

    @classmethod
    def from_json(cls, data: dict) -> RetrySpec:
        return cls(data["max_attempts"], data["interval_seconds"], data["give_up_minutes"])


@dataclass(frozen=True)
class Schedule:
    id: int
    instance_id: int
    name: str
    enabled: bool
    notify_email: bool
    created_at: datetime.datetime
    trigger: Trigger
    conditions: list[Condition]
    action: ActionSpec
    restore: RestoreSpec
    retry: RetrySpec

    @classmethod
    def from_json(cls, data: dict) -> Schedule:
        return cls(
            id=data["id"],
            instance_id=data["instance_id"],
            name=data["name"],
            enabled=data["enabled"],
            notify_email=data["notify_email"],
            created_at=parse_utc(data["created_at"]),
            trigger=Trigger.from_json(data["trigger"]),
            conditions=[Condition.from_json(c) for c in data["conditions"]],
            action=ActionSpec.from_json(data["action"]),
            restore=RestoreSpec.from_json(data["restore"]),
            retry=RetrySpec.from_json(data["retry"]),
        )


@dataclass(frozen=True)
class WindowOccurrence:
    window_id: int
    label: str
    start: datetime.datetime
    end: datetime.datetime

    @classmethod
    def from_json(cls, data: dict) -> WindowOccurrence:
        return cls(data["window_id"], data["label"], parse_utc(data["start"]), parse_utc(data["end"]))


@dataclass(frozen=True)
class Context:
    now: datetime.datetime
    timezone: str
    outdoor_temp_c: float | None
    battery_soc_pct: float | None
    price_c_per_kwh: float | None
    windows: list[WindowOccurrence]

    @classmethod
    def from_json(cls, data: dict) -> Context:
        return cls(
            now=parse_utc(data["now"]),
            timezone=data["timezone"],
            outdoor_temp_c=data["outdoor_temp_c"],
            battery_soc_pct=data["battery_soc_pct"],
            price_c_per_kwh=data["price_c_per_kwh"],
            windows=[WindowOccurrence.from_json(w) for w in data["windows"]],
        )


@dataclass(frozen=True)
class Run:
    id: int
    schedule_id: int
    kind: str
    trigger_at: datetime.datetime
    started_at: datetime.datetime | None
    finished_at: datetime.datetime | None
    outcome: str
    message: str | None
    attempts: int
    parent_run_id: int | None
    snapshot: dict | None

    @classmethod
    def from_json(cls, data: dict) -> Run:
        return cls(
            id=data["id"],
            schedule_id=data["schedule_id"],
            kind=data["kind"],
            trigger_at=parse_utc(data["trigger_at"]),
            started_at=_parse_utc_or_none(data["started_at"]),
            finished_at=_parse_utc_or_none(data["finished_at"]),
            outcome=data["outcome"],
            message=data["message"],
            attempts=data["attempts"],
            parent_run_id=data["parent_run_id"],
            snapshot=data["snapshot"],
        )
