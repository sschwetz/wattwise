from __future__ import annotations

import logging

from envoy_collector.failure_log import FAILURE_LOG_INTERVAL_SECONDS, FailureLog


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _fail(log: FailureLog) -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.failure()


def _records(caplog) -> list[tuple[int, bool, str]]:
    return [(r.levelno, r.exc_info is not None, r.getMessage()) for r in caplog.records]


def test_first_failure_logs_error_with_traceback(caplog):
    caplog.set_level(logging.WARNING, logger="envoy_collector")
    _fail(FailureLog("thing failed", clock=FakeClock()))
    assert _records(caplog) == [(logging.ERROR, True, "thing failed")]


def test_repeats_within_the_interval_are_not_logged(caplog):
    caplog.set_level(logging.WARNING, logger="envoy_collector")
    clock = FakeClock()
    log = FailureLog("thing failed", clock=clock)
    _fail(log)
    clock.now += FAILURE_LOG_INTERVAL_SECONDS - 1
    _fail(log)
    assert len(caplog.records) == 1


def test_repeat_after_the_interval_logs_a_warning_with_a_count(caplog):
    caplog.set_level(logging.WARNING, logger="envoy_collector")
    clock = FakeClock()
    log = FailureLog("thing failed", clock=clock)
    _fail(log)
    for _ in range(2):
        clock.now += 100
        _fail(log)
    clock.now += FAILURE_LOG_INTERVAL_SECONDS
    _fail(log)
    clock.now += 1
    _fail(log)
    assert _records(caplog) == [
        (logging.ERROR, True, "thing failed"),
        (logging.WARNING, False, "thing failed: still failing, 3 failures since last log"),
    ]


def test_success_resets_the_streak(caplog):
    caplog.set_level(logging.WARNING, logger="envoy_collector")
    log = FailureLog("thing failed", clock=FakeClock())
    _fail(log)
    log.success()
    _fail(log)
    assert [r.levelno for r in caplog.records] == [logging.ERROR, logging.ERROR]
