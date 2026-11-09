from __future__ import annotations

from local_pricing_collector import main as main_module
from local_pricing_collector.config import Settings

SETTINGS = Settings(core_api_url="http://localhost:8000", collector_key="shared-key")


class FakeProcess:
    instances: list[FakeProcess] = []

    def __init__(self, target, args, daemon):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False
        FakeProcess.instances.append(self)

    def start(self):
        self.started = True


def test_main_starts_the_commit_reporter_process_and_runs_the_poll_loop(monkeypatch):
    FakeProcess.instances = []
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(main_module.multiprocessing, "Process", FakeProcess)
    ran = []
    monkeypatch.setattr(main_module, "run_forever", ran.append)

    main_module.main()

    assert len(FakeProcess.instances) == 1
    process = FakeProcess.instances[0]
    assert process.target is main_module.run_commit_reporter
    assert process.args == ("http://localhost:8000", "shared-key")
    assert process.daemon is True
    assert process.started is True
    assert ran == [SETTINGS]


def test_commit_reporter_process_starts_the_reporter_with_the_collector_key(monkeypatch):
    calls = []

    class FakeThread:
        def join(self):
            calls.append("joined")

    def fake_start(*args):
        calls.append(args)
        return FakeThread()

    monkeypatch.setattr(main_module, "start_commit_reporter", fake_start)

    main_module.run_commit_reporter("http://localhost:8000", "shared-key")

    assert calls == [("http://localhost:8000", "X-Collector-Key", "shared-key", "local-pricing-collector"), "joined"]
