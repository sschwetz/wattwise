from __future__ import annotations

from amber_pricing_collector import main as main_module
from amber_pricing_collector.config import Settings

SETTINGS = Settings(core_api_url="http://localhost:8000", collector_key="shared-key")


class FakeProcess:
    """Stands in for multiprocessing.Process so main() doesn't actually start a
    sibling process -- records what it was constructed with."""

    instances: list["FakeProcess"] = []

    def __init__(self, target, args, daemon):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False
        FakeProcess.instances.append(self)

    def start(self):
        self.started = True


def test_main_starts_backfill_and_recent_poll_processes_and_runs_live_poll(monkeypatch):
    """Final review Important finding: the backfill and recent-poll loops must each
    run in a sibling *process*, not a thread. The main thread forks a child per
    source every 5 minutes via live_poll, and fork() from a multi-threaded process
    is a documented deadlock hazard -- the child inherits any lock (e.g. logging's)
    held at fork time with no owner. Keeping the parent single-threaded removes the
    hazard entirely.

    recent_poll_process must be non-daemonic: it forks its own per-source children
    every minute, and Python refuses to let a daemonic process have children."""
    FakeProcess.instances = []
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(main_module.multiprocessing, "Process", FakeProcess)
    live_poll_calls = []
    monkeypatch.setattr(main_module, "run_live_poll_forever", lambda base_url, key: live_poll_calls.append((base_url, key)))

    main_module.main()

    assert len(FakeProcess.instances) == 2
    by_target = {p.target: p for p in FakeProcess.instances}
    assert set(by_target) == {main_module.run_backfill_forever, main_module.run_recent_poll_forever}
    assert by_target[main_module.run_backfill_forever].daemon is True
    assert by_target[main_module.run_recent_poll_forever].daemon is False
    for process in FakeProcess.instances:
        assert process.started is True
        assert process.args == (SETTINGS.core_api_url, SETTINGS.collector_key)
    assert live_poll_calls == [(SETTINGS.core_api_url, SETTINGS.collector_key)]


def test_main_does_not_use_threading(monkeypatch):
    """The parent process must stay single-threaded -- guards against the
    threading.Thread backfill launcher creeping back in."""
    assert not hasattr(main_module, "threading")
