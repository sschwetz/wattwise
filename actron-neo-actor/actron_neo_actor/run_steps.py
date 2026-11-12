"""Step bookkeeping for an action run: one result per action, and the run's outcome and message from them.

All steps ok gives `ok`, some gives `partial`, none gives `failed`. A run of one
action keeps the message it had before runs held a list: the step's own text.
"""

from __future__ import annotations

from dataclasses import dataclass

from actron_neo_actor.executor import Attempt, Env, update
from actron_neo_actor.models import ActionSpec, Run

GAVE_UP = "gave up"


@dataclass(frozen=True)
class StepResult:
    index: int
    action: ActionSpec
    ok: bool
    message: str | None
    attempts: int

    def to_json(self) -> dict:
        """The `steps` entry Core API stores (StepResult in core_api/actor_schemas.py)."""
        return {
            "index": self.index,
            "action_id": self.action.id,
            "params": dict(self.action.params),
            "outcome": "ok" if self.ok else "failed",
            "message": self.message,
            "attempts": self.attempts,
        }


def from_attempt(index: int, action: ActionSpec, attempt: Attempt, ok_message: str | None = None) -> StepResult:
    message = ok_message if attempt.ok else attempt.failure()
    return StepResult(index, action, attempt.ok, message, attempt.attempts)


def gave_up(index: int, action: ActionSpec) -> StepResult:
    """An action not started because the run's give-up time had passed."""
    return StepResult(index, action, False, GAVE_UP, 0)


def _label(action: ActionSpec) -> str:
    """The action id and its parameter values: `set_zones Lounge off`, `set_temperature 22.0`."""
    words = [action.id]
    for value in action.params.values():
        if isinstance(value, bool):
            words.append("on" if value else "off")
        elif isinstance(value, list):
            words.append(", ".join(str(v) for v in value))
        else:
            words.append(str(value))
    return " ".join(words)


@dataclass(frozen=True)
class RunResult:
    """The steps of a run, or `error` when it could not start (the snapshot could not be read)."""

    steps: list[StepResult]
    error: str | None = None

    @property
    def ok_count(self) -> int:
        return sum(1 for s in self.steps if s.ok)

    @property
    def outcome(self) -> str:
        if self.error is not None or self.ok_count == 0:
            return "failed"
        return "ok" if self.ok_count == len(self.steps) else "partial"

    @property
    def attempts(self) -> int:
        return sum(s.attempts for s in self.steps)

    def message(self, ok_message: str | None = None) -> str | None:
        if self.error is not None:
            return self.error
        failed = [s for s in self.steps if not s.ok]
        if not failed:
            return ok_message
        if len(self.steps) == 1:
            return failed[0].message
        reasons = "; ".join(f"{_label(s.action)}: {s.message}" for s in failed)
        return f"{self.ok_count} of {len(self.steps)} ran. Failed: {reasons}"

    def steps_json(self) -> list[dict] | None:
        return [s.to_json() for s in self.steps] or None


async def finish_run(env: Env, run: Run, result: RunResult, ok_message: str | None = None) -> Run | None:
    """Set the run's outcome, message, attempts and steps from `result`."""
    return await update(
        env, run, result.outcome, result.message(ok_message), attempts=result.attempts, steps=result.steps_json()
    )
