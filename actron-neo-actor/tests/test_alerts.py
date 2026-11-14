import asyncio

from actron_neo_actor.alerts import ALERT_AFTER_FAILURES, InstanceAlerts
from actron_neo_actor.async_core import AsyncCore
from tests.fake_core import FakeCore


def _alerts() -> tuple[InstanceAlerts, FakeCore]:
    core = FakeCore(lambda: None)  # type: ignore[arg-type]
    return InstanceAlerts(AsyncCore(core)), core  # type: ignore[arg-type]


def test_failure_sends_severity_and_sticky_once_the_streak_is_long_enough():
    async def scenario() -> None:
        alerts, core = _alerts()
        for _ in range(ALERT_AFTER_FAILURES):
            await alerts.failure(1, "tick", "msg", severity="partial")
        assert core.alert_options[(1, "tick")] == ("partial", False)

    asyncio.run(scenario())


def test_a_raise_is_resent_when_only_the_severity_changes():
    async def scenario() -> None:
        alerts, core = _alerts()
        for severity in ("partial", "partial", "failure"):
            for _ in range(ALERT_AFTER_FAILURES):
                await alerts.failure(1, "tick", "msg", severity=severity)
        assert core.alert_calls == [("set", 1, "tick"), ("set", 1, "tick")]
        assert core.alert_options[(1, "tick")] == ("failure", False)

    asyncio.run(scenario())
