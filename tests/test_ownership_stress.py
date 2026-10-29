"""Real-process regression coverage for lifetime-guard group cleanup on macOS."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from plugin.execution import run_trial

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="process-group ownership is tested on macOS")


CHILD_PROGRAM = r"""
import os
from pathlib import Path
import signal
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
directory = Path(sys.argv[1])
(directory / "descendant.pid").write_text(str(os.getpid()))
(directory / "heartbeat").write_text("0")
(directory / "child-ready").write_text("ready")
counter = 1
while True:
    (directory / "heartbeat").write_text(str(counter))
    counter += 1
    time.sleep(0.01)
"""


def _candidate(mode: str) -> str:
    return f'''from pathlib import Path
import subprocess
import sys
import time

def solve(input_dir, output_path):
    directory = Path(input_dir)
    (directory / "candidate.pid").write_text(str(__import__("os").getpid()))
    child = subprocess.Popen(
        [sys.executable, "-c", {CHILD_PROGRAM!r}, str(directory)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    (directory / "descendant.pid").write_text(str(child.pid))
    deadline = time.monotonic() + 5
    while not (directory / "child-ready").exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("descendant did not become ready")
        time.sleep(0.005)
    if {mode!r} == "hang":
        while True:
            time.sleep(0.05)
    Path(output_path).write_bytes(b"ok")
'''


def _trial_directory(root: Path, mode: str, iteration: int) -> tuple[Path, Path]:
    directory = root / f"{mode}-{iteration}"
    inputs = directory / "inputs"
    inputs.mkdir(parents=True)
    (directory / "source.py").write_text(_candidate(mode))
    return directory, inputs


def _read_pids(directory: Path, inputs: Path) -> set[int]:
    pids: set[int] = set()
    for path in (inputs / "candidate.pid", inputs / "descendant.pid", directory / "guard-status.json"):
        try:
            if path.name == "guard-status.json":
                status = json.loads(path.read_text())
                pid = status.get("cleanup_details", {}).get("worker_pid")
                if pid is not None:
                    pids.add(int(pid))
            elif path.is_file():
                pids.add(int(path.read_text()))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return pids


def _process_state(pid: int) -> str:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "stopped"
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
        timeout=1,
    ).stdout.strip()
    if not state or state.startswith("Z"):
        return "stopped"
    return state


def _wait_until_stopped(pids: set[int]) -> None:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        states = {pid: _process_state(pid) for pid in pids}
        if all(state == "stopped" for state in states.values()):
            return
        time.sleep(0.02)
    states = {pid: _process_state(pid) for pid in pids}
    assert states == {pid: "stopped" for pid in pids}, f"owned processes remain: {states}"


def _cleanup_exact_pids(pids: set[int]) -> None:
    surviving: set[int] = set()
    for pid in pids:
        if _process_state(pid) != "stopped":
            surviving.add(pid)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if surviving:
        _wait_until_stopped(surviving)


@pytest.mark.parametrize("mode", ["success", "hang"])
def test_trial_stops_worker_and_stdio_detached_term_ignoring_descendant(tmp_path: Path, mode: str) -> None:
    for iteration in range(3):
        directory, inputs = _trial_directory(tmp_path, mode, iteration)
        try:
            result = asyncio.run(run_trial(directory, 3.0, 1024, 1024))
            assert result.cleanup_confirmed is True
            assert result.status == ("ok" if mode == "success" else "timeout")
            if mode == "success":
                assert result.exit_code == 0
                assert (directory / "result.bin").read_bytes() == b"ok"
            pids = _read_pids(directory, inputs)
            assert len(pids) == 3, f"expected guard worker, solve process, and descendant PIDs; got {pids}"
            states = {pid: _process_state(pid) for pid in pids}
            assert all(state == "stopped" for state in states.values()), f"running after cleanup returned: {states}"
            heartbeat = inputs / "heartbeat"
            first = heartbeat.read_bytes()
            time.sleep(0.1)
            assert heartbeat.read_bytes() == first, "descendant heartbeat continued after guarded cleanup"
        finally:
            _cleanup_exact_pids(_read_pids(directory, inputs))
