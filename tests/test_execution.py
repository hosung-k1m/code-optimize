"""Integration checks for installed guarded process execution."""

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

import anyio
import pytest

from plugin.execution import probe_runtime, run_trial
from plugin.process_control import UnsupportedPlatformError

darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="process ownership is validated only on macOS")


def trial(tmp_path: Path, source: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "inputs").mkdir()
    (tmp_path / "source.py").write_text(source, encoding="utf-8")
    return tmp_path


def execute(path: Path, timeout: float = 3, output_limit: int = 1024, log_limit: int = 1024):
    return asyncio.run(run_trial(path, timeout, output_limit, log_limit))


@darwin_only
def test_success_and_crash(tmp_path: Path) -> None:
    success = trial(tmp_path / "success", "from pathlib import Path\ndef solve(i, o): Path(o).write_bytes(b'ok')\n")
    result = execute(success)
    assert result.status == "ok"
    assert result.cleanup_confirmed
    details = json.loads((success / "guard-status.json").read_text())["cleanup_details"]
    assert details["direct_worker_reaped"] and details["group_kill_signal_sent"]
    assert details["pipes_drained"]
    assert result.elapsed_ns <= 3_000_000_000
    assert (success / "result.bin").read_bytes() == b"ok"

    crash = trial(tmp_path / "crash", "def solve(i, o): raise RuntimeError('expected')\n")
    assert execute(crash).status == "crash"

    dataclass_source = trial(
        tmp_path / "dataclass",
        "from __future__ import annotations\nfrom dataclasses import dataclass\n"
        "from pathlib import Path\n@dataclass\nclass Item:\n value: int\n"
        "def solve(i, o): Path(o).write_bytes(str(Item(7).value).encode())\n",
    )
    result = execute(dataclass_source)
    assert result.status == "ok"
    assert (dataclass_source / "result.bin").read_bytes() == b"7"

    sys_exit = trial(
        tmp_path / "sys-exit-zero",
        "import sys\nfrom pathlib import Path\ndef solve(i, o):\n Path(o).write_bytes(b'exit-zero')\n sys.exit(0)\n",
    )
    result = execute(sys_exit)
    assert result.status == "ok" and result.exit_code == 0
    assert (sys_exit / "result.bin").read_bytes() == b"exit-zero"


@darwin_only
def test_syntax_missing_output_result_and_log_limits(tmp_path: Path) -> None:
    syntax = trial(tmp_path / "syntax", "def solve(:\n")
    assert execute(syntax, log_limit=4096).status == "crash"
    missing = trial(tmp_path / "missing", "def solve(i, o): pass\n")
    assert execute(missing).status == "missing_output"
    symlink = trial(
        tmp_path / "symlink", "from pathlib import Path\ndef solve(i, o): Path(o).symlink_to(Path(i) / 'input')\n"
    )
    (symlink / "inputs" / "input").write_bytes(b"input")
    assert execute(symlink).status == "invalid_output"
    large = trial(tmp_path / "large", "from pathlib import Path\ndef solve(i, o): Path(o).write_bytes(b'x'*4096)\n")
    assert execute(large, output_limit=128).status == "output_limit"
    noisy = trial(tmp_path / "noisy", "def solve(i, o):\n print('x'*10000)\n")
    result = execute(noisy, log_limit=64)
    assert result.status == "log_limit"
    assert len(result.stdout) + len(result.stderr) <= 64
    both = trial(
        tmp_path / "both-streams",
        "import os\ndef solve(i, o):\n while True:\n  os.write(1, b'x'*4096)\n  os.write(2, b'y'*4096)\n",
    )
    result = execute(both, log_limit=64)
    assert result.status == "log_limit"
    assert len(result.stdout) + len(result.stderr) <= 64
    split = trial(
        tmp_path / "split-streams",
        "import os\ndef solve(i, o):\n os.write(1, b'stdout')\n os.write(2, b'stderr')\n",
    )
    result = execute(split)
    assert result.status == "missing_output"
    assert result.stdout == b"stdout"
    assert result.stderr == b"stderr"


@darwin_only
def test_timeout_and_child_cleanup(tmp_path: Path) -> None:
    hanging = trial(tmp_path / "hanging", "import time\ndef solve(i, o): time.sleep(20)\n")
    result = execute(hanging, timeout=0.8)
    assert result.status == "timeout"
    assert result.cleanup_confirmed
    assert result.elapsed_ns <= 800_000_000

    child = trial(
        tmp_path / "child",
        "import subprocess, sys, time\nfrom pathlib import Path\ndef solve(i, o):\n "
        'd = Path(i)\n code = ("import os,time; from pathlib import Path; '
        "d=Path(__import__('sys').argv[1]); (d/'pid').write_text(str(os.getpid())); "
        "(d/'heartbeat').write_text('alive'); time.sleep(20)\")\n "
        "subprocess.Popen([sys.executable, '-c', code, str(d)])\n "
        "deadline = time.monotonic() + 2\n "
        "while not (d / 'pid').exists() and time.monotonic() < deadline: time.sleep(0.01)\n "
        "Path(o).write_bytes(b'ok')\n",
    )
    result = execute(child)
    assert result.status == "ok"
    assert result.cleanup_confirmed
    child_pid = int((child / "inputs" / "pid").read_text())
    heartbeat = child / "inputs" / "heartbeat"
    first = heartbeat.stat().st_size
    time.sleep(0.1)
    assert heartbeat.stat().st_size == first
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


@darwin_only
def test_group_kill_stops_pipe_closed_term_ignoring_descendants(tmp_path: Path) -> None:
    child_code = (
        "import os, signal, sys, time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "sys.stdout.close(); sys.stderr.close()\n"
        "d = Path(sys.argv[1])\n"
        "(d / 'pid').write_text(str(os.getpid()))\n"
        "while True:\n"
        "    (d / 'heartbeat').write_text(str(time.monotonic_ns()))\n"
        "    time.sleep(0.01)\n"
    )
    success_source = (
        "import subprocess, sys, time\nfrom pathlib import Path\ndef solve(i, o):\n "
        f"d = Path(i)\n code = {child_code!r}\n "
        "subprocess.Popen([sys.executable, '-c', code, str(d)])\n "
        "deadline = time.monotonic() + 2\n "
        "while not (d / 'pid').exists() and time.monotonic() < deadline: time.sleep(0.01)\n "
        "Path(o).write_bytes(b'ok')\n"
    )
    success = trial(tmp_path / "descendant-success", success_source)
    result = execute(success)
    assert result.status == "ok" and result.cleanup_confirmed
    status = json.loads((success / "guard-status.json").read_text())
    assert status["cleanup_details"]["group_kill_signal_sent"] is True
    success_pid = int((success / "inputs" / "pid").read_text())
    success_heartbeat = success / "inputs" / "heartbeat"
    success_size = success_heartbeat.stat().st_size
    time.sleep(0.1)
    assert success_heartbeat.stat().st_size == success_size
    with pytest.raises(ProcessLookupError):
        os.kill(success_pid, 0)

    timeout_source = success_source.replace("Path(o).write_bytes(b'ok')", "time.sleep(20)\n Path(o).write_bytes(b'ok')")
    timed = trial(tmp_path / "descendant-timeout", timeout_source)
    result = execute(timed, timeout=1.2)
    assert result.status == "timeout" and result.cleanup_confirmed
    timed_pid = int((timed / "inputs" / "pid").read_text())
    timed_heartbeat = timed / "inputs" / "heartbeat"
    timed_size = timed_heartbeat.stat().st_size
    time.sleep(0.1)
    assert timed_heartbeat.stat().st_size == timed_size
    with pytest.raises(ProcessLookupError):
        os.kill(timed_pid, 0)


@darwin_only
def test_cancellation_triggers_guard_cleanup(tmp_path: Path) -> None:
    path = trial(
        tmp_path / "cancel",
        "import os, time\nfrom pathlib import Path\ndef solve(i, o):\n "
        "d = Path(i)\n (d / 'pid').write_text(str(os.getpid()))\n "
        "while True:\n  with (d / 'heartbeat').open('a') as f: f.write('x')\n  time.sleep(0.02)\n",
    )

    async def scenario():
        with pytest.raises(TimeoutError):
            with anyio.fail_after(0.5):
                await run_trial(path, 15, 1024, 1024)

    anyio.run(scenario)
    status = json.loads((path / "guard-status.json").read_text(encoding="utf-8"))
    assert status["status"] == "controller_lost"
    assert status["cleanup_confirmed"] is True
    worker_pid = int((path / "inputs" / "pid").read_text())
    heartbeat = path / "inputs" / "heartbeat"
    size = heartbeat.stat().st_size
    time.sleep(0.1)
    assert heartbeat.stat().st_size == size
    with pytest.raises(ProcessLookupError):
        os.kill(worker_pid, 0)


@darwin_only
def test_kqueue_exit_observation_survives_unreaped_zombie() -> None:
    import subprocess

    from plugin.process_control import ProcessExitWatcher

    read_fd, write_fd = os.pipe()
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os,sys; os.write(int(sys.argv[1]), b'x'); os.close(int(sys.argv[1]))",
            str(write_fd),
        ],
        pass_fds=(write_fd,),
        start_new_session=True,
    )
    watcher = ProcessExitWatcher(child.pid)
    os.close(write_fd)
    try:
        assert os.read(read_fd, 1) == b"x"
        assert os.read(read_fd, 1) == b""  # Child exited, but this Popen has not reaped it.
        # macOS may expose an unreaped process group as EPERM or already report
        # ESRCH under an embedding SIGCHLD reaper; NOTE_EXIT remains authoritative.
        try:
            os.killpg(child.pid, 0)
        except (PermissionError, ProcessLookupError):
            pass
        child.wait()
        assert watcher.wait(1.0)
    finally:
        os.close(read_fd)
        watcher.close()
        if child.poll() is None:
            child.kill()
        child.wait()


def test_non_posix_is_an_explicit_error(monkeypatch) -> None:
    from plugin import process_control

    for platform in ("win32", "linux"):
        monkeypatch.setattr(process_control, "sys", type("Platform", (), {"platform": platform})())
        with pytest.raises(UnsupportedPlatformError):
            process_control.require_supported_platform()


@darwin_only
def test_forced_evaluator_death_stops_worker(tmp_path: Path) -> None:
    import subprocess
    import time

    path = trial(
        tmp_path / "death",
        """
import os, time
from pathlib import Path
def solve(input_dir, output_path):
    marker = Path(input_dir) / 'alive'
    (Path(input_dir) / 'pid').write_text(str(os.getpid()))
    while True:
        with marker.open('a') as stream: stream.write('x')
        time.sleep(0.03)
""",
    )
    script = (
        "import asyncio, pathlib, sys; "
        "from plugin.execution import probe_runtime, run_trial; "
        f"asyncio.run(run_trial(pathlib.Path({str(path)!r}), 15, 1024, 1024))"
    )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    parent = subprocess.Popen([sys.executable, "-c", script], env=env)
    marker = path / "inputs" / "alive"
    deadline = time.monotonic() + 6
    while not marker.exists() and time.monotonic() < deadline:
        if parent.poll() is not None:
            pytest.fail("evaluator exited before the worker started")
        time.sleep(0.01)
    assert marker.exists(), "worker did not start"
    pid_path = path / "inputs" / "pid"
    while not pid_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    worker_pid = int(pid_path.read_text())
    os.kill(parent.pid, signal.SIGKILL)
    parent.wait(timeout=3)
    time.sleep(0.6)
    snapshot = marker.read_bytes()
    time.sleep(0.2)
    assert marker.read_bytes() == snapshot
    with pytest.raises(ProcessLookupError):
        os.kill(worker_pid, 0)


@darwin_only
def test_failed_runtime_probe_preserves_incomplete_diagnostics(tmp_path: Path, monkeypatch) -> None:
    from plugin import execution

    async def fail_trial(*args, **kwargs):
        raise RuntimeError("forced preflight failure")

    monkeypatch.setattr(execution, "run_trial", fail_trial)
    with pytest.raises(execution.ExecutionEnvironmentError, match="incomplete diagnostics retained at") as exc:
        anyio.run(execution.probe_runtime, tmp_path)
    diagnostic_paths = list(tmp_path.glob(".code-optimize-probe-*/probe-diagnostics.json"))
    assert len(diagnostic_paths) == 1
    diagnostic = json.loads(diagnostic_paths[0].read_text())
    assert diagnostic["state"] == "incomplete"
    assert "forced preflight failure" in diagnostic["reason"]
    assert str(diagnostic_paths[0].parent) in str(exc.value)
    import shutil

    shutil.rmtree(diagnostic_paths[0].parent)


@darwin_only
def test_probe_cancellation_confirms_lost_worker_cleanup(tmp_path: Path) -> None:
    async def scenario() -> int:
        worker_pid = 0
        async with anyio.create_task_group() as group:
            group.start_soon(probe_runtime, tmp_path)
            deadline = asyncio.get_running_loop().time() + 12
            while asyncio.get_running_loop().time() < deadline:
                probes = list(tmp_path.glob(".code-optimize-probe-*"))
                pid_files = list(tmp_path.glob(".code-optimize-probe-*/lost/inputs/worker.pid"))
                if pid_files:
                    worker_pid = int(pid_files[0].read_text())
                    group.cancel_scope.cancel()
                    break
                if probes and any((probe / "lost" / "guard-status.json").exists() for probe in probes):
                    pytest.fail("probe reached successful cleanup before cancellation target")
                await asyncio.sleep(0.01)
            else:
                pytest.fail("runtime probe did not reach its evaluator-loss worker")
        assert worker_pid > 0
        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)
        assert not list(tmp_path.glob(".code-optimize-probe-*"))
        return worker_pid

    anyio.run(scenario)
