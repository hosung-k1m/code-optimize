"""Parent-side guarded execution and installed-runtime preflight."""

import asyncio
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import anyio
import yaml

from plugin.process_control import require_supported_platform


@dataclass(frozen=True)
class ExecutionResult:
    elapsed_ns: int
    exit_code: int | None
    status: str
    stdout: bytes
    stderr: bytes
    cleanup_confirmed: bool


class ExecutionEnvironmentError(RuntimeError):
    """The trusted guard could not prove process cleanup or report execution."""


_probe_cache: set[tuple[str, str]] = set()


def _write_config(path: Path, type_path: str, values: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump({f"_ > {type_path}": None, **values}), encoding="utf-8")


async def run_trial(
    directory: Path, timeout_seconds: float, max_output_bytes: int, max_log_bytes: int
) -> ExecutionResult:
    """Run staged source.py in a fresh worker owned by an EOF lifetime guard.

    `directory` must contain source.py and inputs/; run.yaml is generated here. Timing includes guard
    startup, worker execution, and confirmed cleanup.
    """
    require_supported_platform()
    directory = directory.resolve(strict=True)
    if timeout_seconds <= 0 or max_output_bytes < 0 or max_log_bytes < 0:
        raise ValueError("timeouts and output limits must be positive/nonnegative")
    if not (directory / "source.py").is_file() or not (directory / "inputs").is_dir():
        raise ValueError("trial directory must contain source.py and inputs/")
    _write_config(
        directory / "run.yaml",
        "plugin.worker.WorkerConfig",
        {
            "source": "source.py",
            "input_dir": "inputs",
            "output_path": "result.bin",
        },
    )
    read_fd, write_fd = os.pipe()
    deadline_ns = time.monotonic_ns() + int(timeout_seconds * 1_000_000_000)
    cleanup_reserve_ns = min(400_000_000, int(timeout_seconds * 0.4 * 1_000_000_000))
    _write_config(
        directory / "guard.yaml",
        "plugin.guard.GuardConfig",
        {
            "stop_at_ns": deadline_ns - cleanup_reserve_ns,
            "cleanup_deadline_ns": deadline_ns,
            "max_output_bytes": max_output_bytes,
            "max_log_bytes": max_log_bytes,
            "lifetime_fd": read_fd,
        },
    )
    start = time.perf_counter_ns()
    guard = None
    try:
        with anyio.CancelScope(shield=True):
            guard = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-m",
                "plugin.guard",
                cwd=directory,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env={k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR", "TEMP"}},
                start_new_session=True,
                pass_fds=(read_fd,),
            )
        os.close(read_fd)
        read_fd = -1
        try:
            _, guard_error = await asyncio.wait_for(guard.communicate(), timeout=timeout_seconds + 0.1)
        except TimeoutError as exc:
            os.close(write_fd)
            write_fd = -1
            try:
                await asyncio.wait_for(asyncio.shield(guard.wait()), timeout=0.35)
            except TimeoutError:
                guard.kill()
                await asyncio.shield(guard.wait())
            raise ExecutionEnvironmentError("Lifetime guard exceeded bounded cleanup allowance") from exc
        if guard.returncode != 0:
            raise ExecutionEnvironmentError(f"Lifetime guard failed: {guard_error.decode('utf-8', 'replace')[-2000:]}")
        status_path = directory / "guard-status.json"
        if status_path.is_symlink() or not status_path.is_file():
            raise ExecutionEnvironmentError("Lifetime guard did not provide trusted status")
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("cleanup_confirmed") is not True:
            details = json.dumps(status.get("cleanup_details", {}), sort_keys=True)
            raise ExecutionEnvironmentError(
                f"Lifetime guard could not confirm worker cleanup in {directory}; guard diagnostics: {details}"
            )
        stdout = bytes.fromhex(status["stdout_hex"])
        stderr = bytes.fromhex(status["stderr_hex"])
        if len(stdout) + len(stderr) > max_log_bytes:
            raise ExecutionEnvironmentError("Lifetime guard exceeded the retained log limit")
        elapsed_ns = time.perf_counter_ns() - start
        if elapsed_ns > int(timeout_seconds * 1_000_000_000):
            raise ExecutionEnvironmentError("Guarded trial exceeded its end-to-end deadline")
        return ExecutionResult(
            elapsed_ns=elapsed_ns,
            exit_code=status.get("exit_code"),
            status=status["status"],
            stdout=stdout,
            stderr=stderr,
            cleanup_confirmed=True,
        )
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            if write_fd >= 0:
                os.close(write_fd)
                write_fd = -1
            if guard is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(guard.wait()), timeout=0.5)
                except TimeoutError:
                    guard.kill()
                    await asyncio.shield(guard.wait())
                if guard.returncode not in (0,):
                    raise ExecutionEnvironmentError("Guard cleanup failed after controller cancellation")
                status_path = directory / "guard-status.json"
                if not status_path.is_file() or status_path.is_symlink():
                    raise ExecutionEnvironmentError("Guard did not confirm cleanup after cancellation")
                status = json.loads(status_path.read_text(encoding="utf-8"))
                if status.get("cleanup_confirmed") is not True:
                    raise ExecutionEnvironmentError("Worker cleanup was not confirmed after cancellation")
        raise
    finally:
        if read_fd >= 0:
            os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


async def _stop_probe_evaluator(evaluator: asyncio.subprocess.Process, directory: Path) -> None:
    """Stop an evaluator and require its independently-owned guard to confirm cleanup."""
    if evaluator.returncode is None:
        evaluator.kill()
    try:
        await asyncio.wait_for(asyncio.shield(evaluator.wait()), timeout=1.0)
    except TimeoutError as exc:
        raise ExecutionEnvironmentError("Probe evaluator could not be reaped") from exc
    status_path = directory / "guard-status.json"
    # No guard.yaml means the evaluator had not reached guarded launch; therefore it
    # could not yet have created a worker. Once the config exists, require guard status.
    if not (directory / "guard.yaml").exists() and not status_path.exists():
        return
    deadline = asyncio.get_running_loop().time() + 3.0
    while not status_path.is_file() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)
    if not status_path.is_file() or status_path.is_symlink():
        raise ExecutionEnvironmentError("Probe lifetime guard did not report cleanup")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status.get("status") != "controller_lost" or status.get("cleanup_confirmed") is not True:
        raise ExecutionEnvironmentError("Probe lifetime guard could not confirm evaluator-loss cleanup")
    pid_file = directory / "inputs" / "worker.pid"
    if pid_file.is_file():
        worker_pid = int(pid_file.read_text(encoding="utf-8"))
        try:
            os.kill(worker_pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise ExecutionEnvironmentError("Probe worker remains after guard cleanup")


def _write_probe_failure(
    probe: Path, reason: str, evaluator: asyncio.subprocess.Process | None, lost: Path | None
) -> dict[str, object]:
    worker_pid = None
    if lost is not None:
        pid_file = lost / "inputs" / "worker.pid"
        if pid_file.is_file():
            worker_pid = pid_file.read_text(encoding="utf-8")
    guard_pids = []
    for status_path in probe.glob("*/guard-status.json"):
        status = json.loads(status_path.read_text(encoding="utf-8"))
        cleanup_details = status.get("cleanup_details", {})
        guard_pids.append({"trial": status_path.parent.name, "pid": cleanup_details.get("worker_pid")})
    diagnostics = {
        "state": "incomplete",
        "reason": reason,
        "evaluator_pid": evaluator.pid if evaluator is not None else None,
        "worker_pid": worker_pid,
        "guard_pids": guard_pids,
        "lost_trial_directory": str(lost) if lost is not None else None,
    }
    (probe / "probe-diagnostics.json").write_text(json.dumps(diagnostics, sort_keys=True), encoding="utf-8")
    return diagnostics


async def probe_runtime(workspace: Path) -> None:
    """Verify installed execution, fixture reads, timeout, and evaluator-loss ownership."""
    require_supported_platform()
    implementation = b"".join(
        (Path(__file__).parent / name).read_bytes()
        for name in ("execution.py", "guard.py", "worker.py", "process_control.py")
    )
    project_root = Path(__file__).resolve().parents[2]
    lock_data = b"".join(
        (project_root / name).read_bytes() for name in ("pyproject.toml", "uv.lock") if (project_root / name).is_file()
    )
    version_data = f"{sys.version_info!r}:{sys.executable}".encode()
    key = (str(Path(sys.executable).resolve()), hashlib.sha256(implementation + lock_data + version_data).hexdigest())
    if key in _probe_cache:
        return
    workspace = workspace.resolve(strict=True)
    probe = workspace / f".code-optimize-probe-{os.getpid()}-{time.monotonic_ns()}"
    probe.mkdir()
    import shutil

    evaluator: asyncio.subprocess.Process | None = None
    lost: Path | None = None
    evaluator_cleaned = False
    preserve_probe = False
    try:
        success = probe / "success"
        (success / "inputs").mkdir(parents=True)
        (success / "inputs" / "fixture.txt").write_bytes(b"installed-fixture")
        (success / "source.py").write_text(
            "from pathlib import Path\ndef solve(input_dir, output_path):\n "
            "data = (Path(input_dir) / 'fixture.txt').read_bytes()\n "
            "assert data == b'installed-fixture'\n Path(output_path).write_bytes(data)\n",
            encoding="utf-8",
        )
        success_result = await run_trial(success, 5.0, 64, 4096)
        output = success / "result.bin"
        if (
            success_result.status != "ok"
            or not success_result.cleanup_confirmed
            or not output.is_file()
            or output.is_symlink()
            or output.read_bytes() != b"installed-fixture"
        ):
            raise ExecutionEnvironmentError(f"Installed execution probe failed: {success_result.status}")

        timed = probe / "timeout"
        (timed / "inputs").mkdir(parents=True)
        (timed / "source.py").write_text("import time\ndef solve(i, o): time.sleep(20)\n", encoding="utf-8")
        timeout_result = await run_trial(timed, 1.5, 64, 4096)
        if timeout_result.status != "timeout" or not timeout_result.cleanup_confirmed:
            raise ExecutionEnvironmentError(f"Installed timeout probe failed: {timeout_result.status}")

        lost = probe / "lost"
        (lost / "inputs").mkdir(parents=True)
        (lost / "source.py").write_text(
            "import os, time\nfrom pathlib import Path\ndef solve(i, o):\n "
            "marker = Path(i) / 'heartbeat'\n (Path(i) / 'worker.pid').write_text(str(os.getpid()))\n "
            "while True:\n  with marker.open('a') as f: f.write('x')\n  time.sleep(0.02)\n",
            encoding="utf-8",
        )
        evaluator_code = (
            "import asyncio, pathlib; from plugin.execution import run_trial; "
            f"asyncio.run(run_trial(pathlib.Path({str(lost)!r}), 20, 64, 4096))"
        )
        evaluator_env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR", "TEMP"}}
        with anyio.CancelScope(shield=True):
            evaluator = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-c",
                evaluator_code,
                cwd=lost,
                env=evaluator_env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        heartbeat = lost / "inputs" / "heartbeat"
        pid_file = lost / "inputs" / "worker.pid"
        startup_deadline = asyncio.get_running_loop().time() + 6
        while not heartbeat.is_file() or not pid_file.is_file():
            if asyncio.get_running_loop().time() >= startup_deadline:
                raise ExecutionEnvironmentError("Evaluator-loss probe worker did not start")
            if evaluator.returncode is not None:
                raise ExecutionEnvironmentError("Evaluator-loss probe exited before worker startup")
            await asyncio.sleep(0.01)
        worker_pid = int(pid_file.read_text(encoding="utf-8"))
        evaluator.kill()
        await asyncio.wait_for(asyncio.shield(evaluator.wait()), timeout=1.0)
        await _stop_probe_evaluator(evaluator, lost)
        evaluator_cleaned = True
        stable_until = asyncio.get_running_loop().time() + 0.25
        previous = heartbeat.stat().st_size
        while asyncio.get_running_loop().time() < stable_until:
            await asyncio.sleep(0.05)
            current = heartbeat.stat().st_size
            if current != previous:
                previous = current
                stable_until = asyncio.get_running_loop().time() + 0.25
        try:
            os.kill(worker_pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise ExecutionEnvironmentError("Worker remains after evaluator-loss cleanup")
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            try:
                if evaluator is not None and lost is not None and not evaluator_cleaned:
                    await _stop_probe_evaluator(evaluator, lost)
                    evaluator_cleaned = True
            except ExecutionEnvironmentError as cleanup_error:
                preserve_probe = True
                diagnostics = _write_probe_failure(
                    probe, f"cancelled; cleanup failed: {cleanup_error}", evaluator, lost
                )
                raise ExecutionEnvironmentError(
                    f"Runtime probe cancellation cleanup failed; incomplete diagnostics retained at {probe}; "
                    f"PIDs={json.dumps({key: diagnostics[key] for key in ('evaluator_pid', 'worker_pid', 'guard_pids')}, sort_keys=True)}: {cleanup_error}"
                ) from cleanup_error
        raise
    except Exception as exc:
        with anyio.CancelScope(shield=True):
            if evaluator is not None and lost is not None and not evaluator_cleaned:
                try:
                    await _stop_probe_evaluator(evaluator, lost)
                    evaluator_cleaned = True
                except ExecutionEnvironmentError as cleanup_error:
                    exc = ExecutionEnvironmentError(f"{exc}; evaluator cleanup also failed: {cleanup_error}")
        preserve_probe = True
        diagnostics = _write_probe_failure(probe, f"{type(exc).__name__}: {exc}", evaluator, lost)
        raise ExecutionEnvironmentError(
            f"Runtime probe failed; incomplete diagnostics retained at {probe}; "
            f"PIDs={json.dumps({key: diagnostics[key] for key in ('evaluator_pid', 'worker_pid', 'guard_pids')}, sort_keys=True)}: {exc}"
        ) from exc
    finally:
        with anyio.CancelScope(shield=True):
            if evaluator is not None and lost is not None and not evaluator_cleaned:
                try:
                    await _stop_probe_evaluator(evaluator, lost)
                    evaluator_cleaned = True
                except ExecutionEnvironmentError as cleanup_error:
                    preserve_probe = True
                    diagnostics = _write_probe_failure(probe, f"final cleanup failed: {cleanup_error}", evaluator, lost)
                    raise ExecutionEnvironmentError(
                        f"Runtime probe cleanup failed; incomplete diagnostics retained at {probe}; "
                        f"PIDs={json.dumps({key: diagnostics[key] for key in ('evaluator_pid', 'worker_pid', 'guard_pids')}, sort_keys=True)}: {cleanup_error}"
                    ) from cleanup_error
            if probe.exists() and not preserve_probe:
                shutil.rmtree(probe)
    _probe_cache.add(key)
