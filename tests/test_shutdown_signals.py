"""Every catchable termination signal must reach the all-29 damping shutdown.

Terminating the controller is the only remote stop once developer mode has released
the built-in motion service, so a signal that bypasses `release()` leaves the last
commanded frame latched on a robot nobody is holding upright.
"""

import os
import pathlib
import signal
import subprocess
import sys
import textwrap
import time

import pytest

import summit_signal
from summit_signal.onboard import STOP_SIGNALS

# pytest puts src/ on sys.path in-process only; the subprocess needs it too.
SRC_PATH = str(pathlib.Path(summit_signal.__file__).resolve().parent.parent)


def test_stop_signals_cover_every_catchable_termination() -> None:
    assert signal.SIGINT in STOP_SIGNALS
    assert signal.SIGTERM in STOP_SIGNALS
    # SIGHUP's default action terminates the process without unwinding, so an
    # untrapped SIGHUP (a dropped SSH session) would skip the damping write.
    assert signal.SIGHUP in STOP_SIGNALS


@pytest.mark.parametrize("stop_signal", sorted(STOP_SIGNALS))
def test_damping_release_runs_for_each_stop_signal(tmp_path, stop_signal: int) -> None:
    """Drive the real handler installation in a subprocess and signal it for real."""

    marker = tmp_path / "released"
    ready = tmp_path / "ready"
    program = textwrap.dedent(
        f"""
        import signal, threading, time
        from summit_signal.onboard import STOP_SIGNALS

        stop = threading.Event()
        previous = {{
            sig: signal.signal(sig, lambda s, f: stop.set()) for sig in STOP_SIGNALS
        }}
        try:
            open({str(ready)!r}, "w").close()
            for _ in range(400):
                if stop.is_set():
                    break
                time.sleep(0.01)
        finally:
            # Stands in for adapter.release(): the all-29 damping write.
            with open({str(marker)!r}, "w") as handle:
                handle.write("damped")
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        """
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([SRC_PATH, os.environ.get("PYTHONPATH", "")]).rstrip(
            os.pathsep
        ),
    }
    process = subprocess.Popen([sys.executable, "-c", program], env=env)
    try:
        for _ in range(500):
            if ready.exists():
                break
            time.sleep(0.01)
        else:  # pragma: no cover - only on a stuck subprocess
            pytest.fail("subprocess never started")
        process.send_signal(stop_signal)
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:  # pragma: no cover - cleanup path
            process.kill()
            process.wait(timeout=5)

    assert marker.read_text() == "damped", (
        f"signal {signal.Signals(stop_signal).name} bypassed the damping shutdown"
    )
