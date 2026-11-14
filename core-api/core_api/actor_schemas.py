from __future__ import annotations

import datetime
import math
import re
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    PlainSerializer,
    Tag,
    computed_field,
    field_validator,
    model_validator,
)

from core_api.models.actors import ActorInstance, RunKind, RunOutcome
from core_api.models.identity import Site, User
from core_api.time_utils import as_utc

_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
HHMM = Annotated[str, Field(pattern=_TIME_PATTERN.pattern)]
# A stored timestamp sent as an ISO string with a UTC offset. SQLite drops the
# offset on write, so a naive value read back is UTC.
AwareUtc = Annotated[datetime.datetime, PlainSerializer(lambda v: as_utc(v).isoformat(), return_type=str)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Schedule specs (owner-submitted, stored as JSON on actor_schedules)


class ClockTime(_Strict):
    kind: Literal["clock"]
    time: HHMM


class SunTime(_Strict):
    """Sunrise or sunset at the site, moved by up to three hours either way."""

    kind: Literal["sun"]
    event: Literal["sunrise", "sunset"]
    offset_minutes: int = Field(default=0, ge=-180, le=180)


TimeRef = Annotated[ClockTime | SunTime, Field(discriminator="kind")]


class WindowStartTrigger(_Strict):
    type: Literal["window_start"]
    window_id: int


class WindowEndTrigger(_Strict):
    type: Literal["window_end"]
    window_id: int


class TimeTrigger(_Strict):
    type: Literal["time"]
    time: HHMM
    days_of_week: list[Annotated[int, Field(ge=0, le=6)]] = Field(
        default_factory=lambda: list(range(7)), min_length=1
    )


class SunTrigger(_Strict):
    type: Literal["sun"]
    event: Literal["sunrise", "sunset"]
    offset_minutes: int = Field(default=0, ge=-180, le=180)
    days_of_week: list[Annotated[int, Field(ge=0, le=6)]] = Field(
        default_factory=lambda: list(range(7)), min_length=1
    )


class PriceAboveTrigger(_Strict):
    """Fires when the price crosses above the level. After firing it waits until the price is at or below
    `rearm`, or, with no `rearm`, until it is no longer above the level."""

    type: Literal["price_above"]
    c_per_kwh: float = Field(allow_inf_nan=False)
    rearm: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def _rearm_is_below_the_level(self) -> PriceAboveTrigger:
        if self.rearm is not None and self.rearm >= self.c_per_kwh:
            raise ValueError("rearm must be below the price level")
        return self


class TemperatureTrigger(_Strict):
    """Fires when the temperature crosses the level in the given direction. After firing it waits until
    the reading is back past `rearm` (below it for gt, above it for lt), or, with no `rearm`, until it
    no longer passes the level."""

    type: Literal["temperature"]
    reading: Literal["indoor_temp_c", "outdoor_temp_c"]
    op: Literal["gt", "lt"]
    value: float = Field(allow_inf_nan=False)
    rearm: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def _rearm_is_back_inside_the_level(self) -> TemperatureTrigger:
        if self.rearm is None:
            return self
        if (self.op == "gt" and self.rearm >= self.value) or (self.op == "lt" and self.rearm <= self.value):
            raise ValueError("rearm must be on the far side of the level from the trigger")
        return self


TriggerSpec = Annotated[
    WindowStartTrigger | WindowEndTrigger | TimeTrigger | SunTrigger | PriceAboveTrigger | TemperatureTrigger,
    Field(discriminator="type"),
]


class ReadingCondition(_Strict):
    """A reading is above or below a value. It carries no `kind`, so rows saved before the calendar rows existed still match."""

    reading: str
    op: Literal["gt", "lt"]
    value: float = Field(allow_inf_nan=False)
    target: str | None = None
    enabled: bool = True


class _MembershipCondition(_Strict):
    op: Literal["in", "not_in"]
    enabled: bool = True


class MonthsCondition(_MembershipCondition):
    """The site's month, 1-12, is or is not one of `values`."""

    kind: Literal["months"]
    values: list[Annotated[int, Field(ge=1, le=12)]]

    @model_validator(mode="after")
    def _needs_a_month(self) -> MonthsCondition:
        if self.enabled and not self.values:
            raise ValueError("pick at least one month")
        return self


class DaysCondition(_MembershipCondition):
    """The site's day of the week, 0 for Monday to 6 for Sunday, is or is not one of `values`."""

    kind: Literal["days"]
    values: list[Annotated[int, Field(ge=0, le=6)]]

    @model_validator(mode="after")
    def _needs_a_day(self) -> DaysCondition:
        if self.enabled and not self.values:
            raise ValueError("pick at least one day")
        return self


class WeekCondition(_Strict):
    """The week is odd or even, counted from the week holding 1970-01-01 (even)."""

    kind: Literal["week"]
    parity: Literal["odd", "even"]
    enabled: bool = True


class DemandWindowCondition(_Strict):
    """A demand window, or the one named by `window_id`, is or is not running now."""

    kind: Literal["demand_window"]
    op: Literal["active", "not_active"]
    window_id: int | None = None
    enabled: bool = True


class TimeOfDayCondition(_Strict):
    """The site's time of day is from `start` up to `end`. When `end` is not later than `start` the span
    runs through midnight into the next day."""

    kind: Literal["time_of_day"]
    start: TimeRef
    end: TimeRef
    enabled: bool = True

    @model_validator(mode="after")
    def _ends_differ(self) -> TimeOfDayCondition:
        if self.start == self.end:
            raise ValueError("start and end must differ")
        return self


def _condition_kind(value: object) -> str | None:
    kind = value.get("kind", "reading") if isinstance(value, dict) else getattr(value, "kind", "reading")
    return kind if isinstance(kind, str) else None


ConditionSpec = Annotated[
    Annotated[ReadingCondition, Tag("reading")]
    | Annotated[MonthsCondition, Tag("months")]
    | Annotated[DaysCondition, Tag("days")]
    | Annotated[WeekCondition, Tag("week")]
    | Annotated[DemandWindowCondition, Tag("demand_window")]
    | Annotated[TimeOfDayCondition, Tag("time_of_day")],
    Discriminator(_condition_kind),
]


def _conditions_with_flag(conditions: list) -> list:
    """Stored conditions saved before the enabled flag existed count as enabled."""
    return [{**c, "enabled": c.get("enabled", True)} if isinstance(c, dict) else c for c in conditions]


def _restore_with_flag(restore: dict) -> dict:
    lists = {k: _conditions_with_flag(v) for k in ("only_if", "early_if") if isinstance(v := restore.get(k), list)}
    return {**restore, **lists}


def _limits_with_defaults(limits: dict) -> dict:
    """Schedules saved before limits existed have an empty dict: no gap and no lockout."""
    return {"min_gap_minutes": 0, "run_again_after": None, **limits}


StoredLimits = Annotated[dict, AfterValidator(_limits_with_defaults)]
StoredConditions = Annotated[list, AfterValidator(_conditions_with_flag)]
StoredRestore = Annotated[dict, AfterValidator(_restore_with_flag)]


class ActionSpec(_Strict):
    id: str
    params: dict[str, float | str | bool | list[str]] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _params_finite(cls, params: dict[str, float | str | bool | list[str]]) -> dict[str, float | str | bool | list[str]]:
        for name, value in params.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"parameter '{name}' must be a finite number")
        return params


class RestoreSpec(_Strict):
    enabled: bool = True
    when: Literal["window_end", "after_minutes", "at_time", "conditions_only"] = "window_end"
    minutes: int | None = Field(default=None, ge=1)
    time: HHMM | None = None
    only_if: list[ConditionSpec] = Field(default_factory=list)
    early_if: list[ConditionSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_when_fields(self) -> RestoreSpec:
        if self.when == "after_minutes" and self.minutes is None:
            raise ValueError("minutes is required when restore happens after a number of minutes")
        if self.when == "at_time" and self.time is None:
            raise ValueError("time is required when restore happens at a set time")
        return self


class LimitsSpec(_Strict):
    """How often a schedule may fire. `min_gap_minutes` is the time that must pass after its last restore
    (or after its last run, if it is not restored) before it can trigger again; 0 turns it off.
    `run_again_after` stops it triggering again until the next time given, counted from its last run."""

    min_gap_minutes: int = Field(default=0, ge=0, le=1440)
    run_again_after: TimeRef | None = None


class RetrySpec(_Strict):
    max_attempts: int = Field(default=3, ge=1)
    interval_seconds: int = Field(default=30, ge=5)
    give_up_minutes: int = Field(default=15, ge=1)


# Capabilities (declared by the actor, stored on actor_instances)


class Option(_Strict):
    value: str
    label: str


class ParamDecl(_Strict):
    name: str
    label: str
    type: Literal["number", "choice", "bool", "multi_choice"]
    min: float | None = None
    max: float | None = None
    unit: str | None = None
    options: list[Option] = Field(default_factory=list)


class ActionDecl(_Strict):
    id: str
    label: str
    restorable: bool = False
    params: list[ParamDecl] = Field(default_factory=list)


class ReadingDecl(_Strict):
    id: str
    label: str
    unit: str | None = None
    type: str = "number"
    target_options: list[Option] | None = None


class Capabilities(_Strict):
    version: Literal[1]
    actions: list[ActionDecl] = Field(default_factory=list)
    readings: list[ReadingDecl] = Field(default_factory=list)


# Run lifecycle (actor-facing)


class RunCreate(_Strict):
    schedule_id: int
    kind: RunKind
    trigger_at: AwareDatetime
    parent_run_id: int | None = None
    snapshot: dict | None = None


class StepResult(_Strict):
    """What one action of a schedule did in a run."""

    index: int = Field(ge=0)
    action_id: str
    params: dict
    outcome: Literal["ok", "failed"]
    message: str | None = None
    attempts: int = Field(ge=0)


class RunUpdate(_Strict):
    outcome: RunOutcome
    message: str | None = None
    attempts: int | None = Field(default=None, ge=0)
    snapshot: dict | None = None
    steps: list[StepResult] | None = None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    schedule_id: int
    kind: RunKind
    trigger_at: AwareUtc
    started_at: AwareUtc | None
    finished_at: AwareUtc | None
    outcome: RunOutcome
    message: str | None
    attempts: int
    parent_run_id: int | None
    snapshot: dict | None
    steps: list[dict] | None


# Owner-facing API (web app)


class ActorInstanceCreate(_Strict):
    provider: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    name: str = Field(min_length=1, max_length=200)


class ActorInstancePatch(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None


class ActorInstanceView(BaseModel):
    """An instance as the Schedule page shows it, to the site owner and to viewers. Never
    carries `config`, which holds the provider refresh token.

    Build it with `instance_view`, which hides the sign-in code from anyone but the site owner.
    It has no `from_attributes`, so a route cannot return the database row in its place."""

    id: int
    site_id: int
    provider: str
    name: str | None
    enabled: bool
    capabilities: dict | None
    capabilities_updated_at: AwareUtc | None
    latest_readings: dict | None
    last_seen_at: AwareUtc | None
    auth_state: dict | None


def shown_auth_state(auth_state: dict | None, is_owner: bool) -> dict | None:
    """The owner gets the stored value. Anyone else gets `needs_sign_in` as is, and a pending
    sign-in code and link become `{"signing_in": true}`, as does any value that is not a dict."""
    if is_owner or auth_state is None:
        return auth_state
    if not isinstance(auth_state, dict):
        return {"signing_in": True}
    if auth_state.get("needs_sign_in") is True:
        return {"needs_sign_in": True}
    return {"signing_in": True}


def instance_view(instance: ActorInstance, site: Site, user: User) -> ActorInstanceView:
    """The instance as `user` may see it at `site`."""
    return ActorInstanceView(
        id=instance.id,
        site_id=instance.site_id,
        provider=instance.provider,
        name=instance.name,
        enabled=instance.enabled,
        capabilities=instance.capabilities,
        capabilities_updated_at=instance.capabilities_updated_at,
        latest_readings=instance.latest_readings,
        last_seen_at=instance.last_seen_at,
        auth_state=shown_auth_state(instance.auth_state, site.owner_user_id == user.id),
    )


class ScheduleCreate(_Strict):
    instance_id: int
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    trigger: TriggerSpec
    conditions: list[ConditionSpec] = Field(default_factory=list)
    actions: list[ActionSpec] = Field(min_length=1, max_length=8)
    restore: RestoreSpec = Field(default_factory=RestoreSpec)
    retry: RetrySpec = Field(default_factory=RetrySpec)
    limits: LimitsSpec = Field(default_factory=LimitsSpec)
    notify_email: bool = False

    def time_refs(self) -> list[ClockTime | SunTime]:
        """Every time reference in the conditions, the restore lists and the limits. A new place that holds one is added here."""
        rows = [*self.conditions, *self.restore.only_if, *self.restore.early_if]
        refs = [ref for row in rows if isinstance(row, TimeOfDayCondition) for ref in (row.start, row.end)]
        return [*refs, *([self.limits.run_again_after] if self.limits.run_again_after else [])]


class SchedulePatch(_Strict):
    """Any field left out keeps its stored value. The merged result is validated as a whole."""

    name: str | None = None
    enabled: bool | None = None
    trigger: dict | None = None
    conditions: list | None = None
    actions: list | None = None
    restore: dict | None = None
    retry: dict | None = None
    limits: dict | None = None
    notify_email: bool | None = None


class ScheduleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    instance_id: int
    name: str
    enabled: bool
    trigger: dict
    conditions: StoredConditions
    actions: list[dict]
    restore: StoredRestore
    retry: dict
    limits: StoredLimits
    notify_email: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def action(self) -> dict:
        """The first action, kept for one release for clients that read a single action."""
        return self.actions[0]


class RunListItem(RunOut):
    schedule_name: str
