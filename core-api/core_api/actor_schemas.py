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
    Field,
    PlainSerializer,
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


class PriceAboveTrigger(_Strict):
    type: Literal["price_above"]
    c_per_kwh: float = Field(allow_inf_nan=False)


TriggerSpec = Annotated[
    WindowStartTrigger | WindowEndTrigger | TimeTrigger | PriceAboveTrigger,
    Field(discriminator="type"),
]


class ConditionSpec(_Strict):
    reading: str
    op: Literal["gt", "lt"]
    value: float = Field(allow_inf_nan=False)
    target: str | None = None
    enabled: bool = True


def _conditions_with_flag(conditions: list) -> list:
    """Stored conditions saved before the enabled flag existed count as enabled."""
    return [{**c, "enabled": c.get("enabled", True)} if isinstance(c, dict) else c for c in conditions]


def _restore_with_flag(restore: dict) -> dict:
    lists = {k: _conditions_with_flag(v) for k in ("only_if", "early_if") if isinstance(v := restore.get(k), list)}
    return {**restore, **lists}


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


class RunUpdate(_Strict):
    outcome: RunOutcome
    message: str | None = None
    attempts: int | None = Field(default=None, ge=0)
    snapshot: dict | None = None


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


# Owner-facing API (web app)


class ActorInstanceCreate(_Strict):
    provider: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    name: str = Field(min_length=1, max_length=200)


class ActorInstancePatch(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None


class ActorInstanceView(BaseModel):
    """Owner-facing instance. Never carries `config`, which holds the provider refresh token.

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
    sign-in code and link become `{"signing_in": true}`."""
    if is_owner or auth_state is None:
        return auth_state
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
    action: ActionSpec
    restore: RestoreSpec = Field(default_factory=RestoreSpec)
    retry: RetrySpec = Field(default_factory=RetrySpec)
    notify_email: bool = False


class SchedulePatch(_Strict):
    """Any field left out keeps its stored value. The merged result is validated as a whole."""

    name: str | None = None
    enabled: bool | None = None
    trigger: dict | None = None
    conditions: list | None = None
    action: dict | None = None
    restore: dict | None = None
    retry: dict | None = None
    notify_email: bool | None = None


class ScheduleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    instance_id: int
    name: str
    enabled: bool
    trigger: dict
    conditions: StoredConditions
    action: dict
    restore: StoredRestore
    retry: dict
    notify_email: bool


class RunListItem(RunOut):
    schedule_name: str
