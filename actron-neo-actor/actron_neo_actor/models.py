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
class TimeRef:
    """A clock time (`kind` "clock", with `time` as HH:MM) or sunrise or sunset (`kind` "sun", with `event`)
    moved by `offset_minutes`."""

    kind: str
    time: str | None = None
    event: str | None = None
    offset_minutes: int = 0

    @classmethod
    def from_json(cls, data: dict) -> TimeRef:
        return cls(
            kind=data["kind"],
            time=data.get("time"),
            event=data.get("event"),
            offset_minutes=data.get("offset_minutes", 0),
        )


@dataclass(frozen=True)
class Trigger:
    """One of window_start, window_end, time, sun, price_above or temperature; the fields that
    do not belong to the type stay None. A sun trigger has `event` and `offset_minutes` and runs on
    `days_of_week`. A price or temperature trigger may have `rearm`."""

    type: str
    window_id: int | None = None
    time: str | None = None
    days_of_week: list[int] | None = None
    c_per_kwh: float | None = None
    reading: str | None = None
    op: str | None = None
    value: float | None = None
    event: str | None = None
    offset_minutes: int = 0
    rearm: float | None = None

    @classmethod
    def from_json(cls, data: dict) -> Trigger:
        return cls(
            type=data["type"],
            window_id=data.get("window_id"),
            time=data.get("time"),
            days_of_week=data.get("days_of_week"),
            c_per_kwh=data.get("c_per_kwh"),
            reading=data.get("reading"),
            op=data.get("op"),
            value=data.get("value"),
            event=data.get("event"),
            offset_minutes=data.get("offset_minutes", 0),
            rearm=data.get("rearm"),
        )


@dataclass(frozen=True)
class Condition:
    """A reading row (`kind` "reading") or a calendar row: "months", "days", "week", "demand_window" or
    "time_of_day". Calendar rows leave `reading`, `op` and `value` at their defaults unless the kind uses `op`.
    A time_of_day row has `start` and `end`."""

    reading: str = ""
    op: str = "gt"
    value: float = 0.0
    target: str | None = None
    enabled: bool = True
    kind: str = "reading"
    values: tuple[int, ...] = ()
    parity: str | None = None
    window_id: int | None = None
    start: TimeRef | None = None
    end: TimeRef | None = None

    @classmethod
    def from_json(cls, data: dict) -> Condition:
        """A condition stored before the enabled flag or `kind` existed has no key: it counts as enabled
        and as a reading row."""
        enabled = data.get("enabled", True)
        if not isinstance(enabled, bool):
            raise TypeError("condition enabled must be a bool")
        kind = data.get("kind", "reading")
        if kind == "reading":
            return cls(
                reading=data["reading"], op=data["op"], value=data["value"], target=data.get("target"), enabled=enabled
            )
        return cls(
            op=data.get("op", ""),
            enabled=enabled,
            kind=kind,
            values=tuple(data.get("values") or ()),
            parity=data.get("parity"),
            window_id=data.get("window_id"),
            start=None if data.get("start") is None else TimeRef.from_json(data["start"]),
            end=None if data.get("end") is None else TimeRef.from_json(data["end"]),
        )


@dataclass(frozen=True)
class ActionSpec:
    id: str
    params: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict) -> ActionSpec:
        return cls(id=data["id"], params=dict(data.get("params") or {}))


def _parse_actions(data: dict) -> list[ActionSpec]:
    """`actions` when present, else the single legacy `action` as a one-item list."""
    raw = data["actions"] if "actions" in data else [data["action"]]
    return [ActionSpec.from_json(a) for a in raw]


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
class Limits:
    """How often a schedule may fire: `min_gap_minutes` after its last restore or run, and no new
    trigger until the next `run_again_after` after its last run."""

    min_gap_minutes: int = 0
    run_again_after: TimeRef | None = None

    @classmethod
    def from_json(cls, data: dict) -> Limits:
        after = data.get("run_again_after")
        return cls(data.get("min_gap_minutes", 0), None if after is None else TimeRef.from_json(after))


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
    actions: list[ActionSpec]
    restore: RestoreSpec
    retry: RetrySpec
    limits: Limits = field(default_factory=Limits)
    # A price or temperature trigger fires only while armed; Core API holds the flag.
    armed: bool = True

    @classmethod
    def from_json(cls, data: dict, instance_id: int) -> Schedule:
        """Core API does not send instance_id; the caller knows it from the URL."""
        return cls(
            id=data["id"],
            instance_id=instance_id,
            name=data["name"],
            enabled=data["enabled"],
            notify_email=data["notify_email"],
            created_at=parse_utc(data["created_at"]),
            trigger=Trigger.from_json(data["trigger"]),
            conditions=[Condition.from_json(c) for c in data["conditions"]],
            actions=_parse_actions(data),
            restore=RestoreSpec.from_json(data["restore"]),
            retry=RetrySpec.from_json(data["retry"]),
            limits=Limits.from_json(data.get("limits") or {}),
            armed=data.get("armed", True),
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
class SunDay:
    """Sunrise and sunset on a local date, as UTC instants; None when the sun does not rise or set that day."""

    date: datetime.date
    sunrise: datetime.datetime | None
    sunset: datetime.datetime | None

    @classmethod
    def from_json(cls, data: dict) -> SunDay:
        return cls(
            datetime.date.fromisoformat(data["date"]),
            _parse_utc_or_none(data["sunrise"]),
            _parse_utc_or_none(data["sunset"]),
        )


@dataclass(frozen=True)
class Context:
    now: datetime.datetime
    timezone: str
    outdoor_temp_c: float | None
    battery_soc_pct: float | None
    price_c_per_kwh: float | None
    windows: list[WindowOccurrence]
    # The local dates from yesterday to two days ahead; empty when the site has no usable location.
    sun: list[SunDay] = field(default_factory=list)
    sun_estimated: bool = False

    @classmethod
    def from_json(cls, data: dict) -> Context:
        return cls(
            now=parse_utc(data["now"]),
            timezone=data["timezone"],
            outdoor_temp_c=data["outdoor_temp_c"],
            battery_soc_pct=data["battery_soc_pct"],
            price_c_per_kwh=data["price_c_per_kwh"],
            windows=[WindowOccurrence.from_json(w) for w in data["windows"]],
            sun=[SunDay.from_json(d) for d in data.get("sun", [])],
            sun_estimated=data.get("sun_estimated", False),
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
    steps: list[dict] | None = None

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
            steps=data.get("steps"),
        )
