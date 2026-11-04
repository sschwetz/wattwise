from __future__ import annotations

import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
HHMM = Annotated[str, Field(pattern=_TIME_PATTERN.pattern)]


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
    days_of_week: list[Annotated[int, Field(ge=0, le=6)]] = Field(default_factory=lambda: list(range(7)))


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
