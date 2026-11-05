import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

SCRIPT = textwrap.dedent(
    """
    import sys

    from actron_neo_actor import main, runner


    class FakeRunner:
        async def round(self):
            print("round", flush=True)

        async def stop(self):
            print("stopped", flush=True)


    runner.production_runner = lambda settings: FakeRunner()
    main.main()
    print("exited", flush=True)
    """
)


def _start(tmp_path: Path) -> subprocess.Popen[str]:
    script = tmp_path / "actor_under_test.py"
    script.write_text(SCRIPT)
    root = str(Path(__file__).resolve().parents[1])
    env = {**os.environ, "CORE_API_URL": "http://core", "ACTOR_KEY": "k", "TICK_SECONDS": "1", "PYTHONPATH": root}
    return subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=root,
    )


def _stop_with(sig: signal.Signals, tmp_path: Path) -> tuple[int, str, str]:
    proc = _start(tmp_path)
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "round"
        proc.send_signal(sig)
        out, err = proc.communicate(timeout=10)
    finally:
        proc.kill()
    return proc.returncode, out, err


def test_sigterm_stops_the_runner_and_exits_cleanly(tmp_path):
    code, out, err = _stop_with(signal.SIGTERM, tmp_path)
    assert "stopped" in out.split()
    assert "exited" in out.split()
    assert code == 0, err


def test_sigint_stops_the_runner_and_exits_cleanly(tmp_path):
    code, out, err = _stop_with(signal.SIGINT, tmp_path)
    assert "stopped" in out.split()
    assert code == 0, err
