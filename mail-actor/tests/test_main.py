from __future__ import annotations

import threading

import pytest

from mail_actor import main as main_module


def test_main_starts_the_commit_reporter_before_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORE_API_URL", "http://core-api:8000")
    monkeypatch.setenv("ACTOR_KEY", "actor-secret")
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(main_module, "start_commit_reporter", lambda *args: calls.append(args))
    monkeypatch.setattr(main_module, "install_stop_handlers", lambda stop: None)

    def fake_declare(client: object, providers: object, stop: threading.Event) -> bool:
        calls.append(("declare",))
        return False

    monkeypatch.setattr(main_module, "declare_until_ready", fake_declare)
    main_module.main()
    assert calls[0] == ("http://core-api:8000", "X-Actor-Key", "actor-secret", "mail-actor")
    assert calls[1] == ("declare",)
