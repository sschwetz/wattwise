from __future__ import annotations

import threading

import httpx
import pytest

from mail_actor import main as main_module


def test_main_starts_the_commit_reporter_before_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORE_API_URL", "http://core-api:8000")
    monkeypatch.setenv("MAIL_ACTOR_KEY", "actor-secret")
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


def test_main_closes_both_http_clients_and_runs_the_worker_with_a_stop_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORE_API_URL", "http://core-api:8000")
    monkeypatch.setenv("MAIL_ACTOR_KEY", "actor-secret")
    monkeypatch.setattr(main_module, "start_commit_reporter", lambda *args: None)
    installed: list[object] = []
    monkeypatch.setattr(main_module, "install_stop_handlers", installed.append)
    monkeypatch.setattr(main_module, "declare_until_ready", lambda client, providers, stop: True)
    ran: dict[str, object] = {}

    def fake_run_forever(client, providers, http, stop, state) -> None:  # type: ignore[no-untyped-def]
        ran.update(client=client, http=http, stop=stop)

    monkeypatch.setattr(main_module, "run_forever", fake_run_forever)
    main_module.main()

    assert isinstance(ran["stop"], main_module.StopFlag)
    assert installed == [ran["stop"]]
    http = ran["http"]
    core_http = ran["client"]._http  # type: ignore[attr-defined]
    assert isinstance(http, httpx.Client) and isinstance(core_http, httpx.Client)
    assert http is not core_http
    assert http.is_closed and core_http.is_closed
