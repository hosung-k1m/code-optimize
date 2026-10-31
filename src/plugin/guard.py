"""Lifetime guard: remains alive while its evaluator owns the lifetime pipe."""

import asyncio
import json
import os
import select
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from slimconfig import Config, load_config

from plugin.process_control import ProcessExitWatcher, execution_environment, require_supported_platform, signal_group


@dataclass
class GuardConfig(Config):
    stop_at_ns: int
    cleanup_deadline_ns: int
    max_output_bytes: int
    max_log_bytes: int
    lifetime_fd: int


def _status(
    directory: Path,
    status: str,
    exit_code: int | None,
    cleanup: bool,
    stdout: bytes,
    stderr: bytes,
    cleanup_details: dict[str, object],
) -> None:
    (directory / "guard-status.tmp").write_text(
        json.dumps(
            {
                "status": status,
                "exit_code": exit_code,
                "cleanup_confirmed": cleanup,
                "stdout_hex": stdout.hex(),
                "stderr_hex": stderr.hex(),
                "cleanup_details": cleanup_details,
            }
        ),
        encoding="utf-8",
    )

    (directory / "guard-status.tmp").replace(directory / "guard-status.json")


async def _reap_and_confirm_group(
    child: asyncio.subprocess.Process, watcher: ProcessExitWatcher, cfg: GuardConfig, ready: bool
) -> dict[str, object]:
    details: dict[str, object] = {"worker_pid": child.pid, "process_group_id": child.pid, "worker_ready": ready}
    term = await asyncio.to_thread(signal_group, child.pid, signal.SIGTERM)
    details["term_signal"] = {"sent": term.sent, "group_absent": term.process_group_absent, "error": term.error}

    child_reaped = child.returncode is not None
    if not child_reaped:
        grace = min(0.05, max(0.0, (cfg.cleanup_deadline_ns - time.monotonic_ns()) / 1_000_000_000))
        if grace:
            try:
                await asyncio.wait_for(child.wait(), timeout=grace)
                child_reaped = True
            except TimeoutError:
                pass

    kill_result = None
    # The worker process ignores TERM after it establishes the group anchor. Thus a
    # live direct child here proves the PGID still belongs to this trial when KILL is sent.
    if not child_reaped:
        kill_result = await asyncio.to_thread(signal_group, child.pid, signal.SIGKILL)
    details["kill_signal"] = (
        {"sent": kill_result.sent, "group_absent": kill_result.process_group_absent, "error": kill_result.error}
        if kill_result is not None
        else None
    )

    remaining = max(0.0, (cfg.cleanup_deadline_ns - time.monotonic_ns()) / 1_000_000_000)
    exit_observed = False
    if remaining:
        exit_observed = await asyncio.to_thread(watcher.wait, remaining)
    remaining = max(0.0, (cfg.cleanup_deadline_ns - time.monotonic_ns()) / 1_000_000_000)
    if not child_reaped and remaining:
        try:
            await asyncio.wait_for(child.wait(), timeout=remaining)
            child_reaped = True
        except TimeoutError:
            pass
    details["direct_worker_reaped"] = child_reaped
    details["kqueue_note_exit_observed"] = exit_observed
    details["group_kill_signal_sent"] = bool(kill_result is not None and kill_result.sent)
    details["pre_submission_abort"] = bool(not ready and child_reaped and (term.sent or term.process_group_absent))
    return details


async def _run(directory: Path, cfg: GuardConfig) -> None:
    lifetime_fd = cfg.lifetime_fd
    os.set_blocking(lifetime_fd, False)
    ready, _, _ = select.select([lifetime_fd], [], [], 0)
    if ready and os.read(lifetime_fd, 1) == b"":
        os.close(lifetime_fd)
        _status(directory, "controller_lost", None, True, b"", b"", {"worker_started": False})
        return
    child = None
    watcher = None
    status = "ok"
    exit_code: int | None = None
    stdout_log = bytearray()
    stderr_log = bytearray()
    overflow = False
    cleanup = False
    cleanup_details: dict[str, object] = {"worker_started": False}
    out_task = None
    err_task = None

    async def drain(stream: asyncio.StreamReader, retained: bytearray) -> None:
        nonlocal overflow
        while chunk := await stream.read(65536):
            room = max(0, cfg.max_log_bytes - len(stdout_log) - len(stderr_log))
            retained.extend(chunk[:room])
            if len(chunk) > room:
                overflow = True

    try:
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-m",
            "plugin.worker",
            cwd=directory,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=execution_environment(),
            start_new_session=True,
        )
        watcher = ProcessExitWatcher(child.pid)
        cleanup_details = {"worker_started": True, "worker_pid": child.pid, "process_group_id": child.pid}
        out_task = asyncio.create_task(drain(child.stdout, stdout_log))
        err_task = asyncio.create_task(drain(child.stderr, stderr_log))
        while child.returncode is None:
            status_file = directory / "worker-status.json"
            if status_file.is_symlink() or (status_file.exists() and not status_file.is_file()):
                status = "invalid_output"
                break
            if status_file.is_file():
                try:
                    completion = json.loads(status_file.read_text(encoding="utf-8"))
                    exit_code = completion["exit_code"]
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    raise RuntimeError(f"Worker returned malformed completion status: {exc}") from exc
                status = "ok" if exit_code == 0 else "crash"
                break
            if time.monotonic_ns() >= cfg.stop_at_ns:
                status = "timeout"
                break
            ready, _, _ = select.select([lifetime_fd], [], [], 0)
            if ready:
                try:
                    if os.read(lifetime_fd, 1) == b"":
                        status = "controller_lost"
                        break
                except BlockingIOError:
                    pass
            result = directory / "result.bin"
            if result.is_symlink() or (result.exists() and not result.is_file()):
                status = "invalid_output"
                break
            if result.exists() and result.stat().st_size > cfg.max_output_bytes:
                status = "output_limit"
                break
            if overflow:
                status = "log_limit"
                break
            await asyncio.sleep(0.005)
        if child.returncode is not None and status == "ok":
            status = "crash"
            exit_code = child.returncode
        ready_file = directory / "worker-ready"
        worker_ready = ready_file.is_file() and not ready_file.is_symlink()
        cleanup_details = await _reap_and_confirm_group(child, watcher, cfg, worker_ready)
        try:
            remaining = max(0.0, (cfg.cleanup_deadline_ns - time.monotonic_ns()) / 1_000_000_000)
            if remaining:
                await asyncio.wait_for(asyncio.gather(out_task, err_task), timeout=remaining)
                pipes_drained = True
            else:
                pipes_drained = False
        except TimeoutError:
            pipes_drained = False
            out_task.cancel()
            err_task.cancel()
            await asyncio.gather(out_task, err_task, return_exceptions=True)
        cleanup_details["pipes_drained"] = pipes_drained
        group_cleanup_proven = (
            cleanup_details.get("group_kill_signal_sent") is True or cleanup_details.get("pre_submission_abort") is True
        )
        cleanup = (
            cleanup_details["direct_worker_reaped"] is True
            and cleanup_details["kqueue_note_exit_observed"] is True
            and group_cleanup_proven
            and pipes_drained
        )
        if overflow and status == "ok":
            status = "log_limit"
        result = directory / "result.bin"
        if status == "ok":
            if result.is_symlink() or (result.exists() and not result.is_file()):
                status = "invalid_output"
            elif not result.exists():
                status = "missing_output"
            elif result.stat().st_size > cfg.max_output_bytes:
                status = "output_limit"
    except BaseException as exc:
        if child is not None:
            ready_file = directory / "worker-ready"
            worker_ready = ready_file.is_file() and not ready_file.is_symlink()
            if watcher is not None:
                cleanup_details = await asyncio.shield(_reap_and_confirm_group(child, watcher, cfg, worker_ready))
            else:
                term = await asyncio.shield(asyncio.to_thread(signal_group, child.pid, signal.SIGTERM))
                kill = await asyncio.shield(asyncio.to_thread(signal_group, child.pid, signal.SIGKILL))
                try:
                    await asyncio.wait_for(asyncio.shield(child.wait()), timeout=0.5)
                    direct_reaped = True
                except TimeoutError:
                    direct_reaped = False
                cleanup_details = {
                    "worker_pid": child.pid,
                    "worker_ready": worker_ready,
                    "term_signal": term.__dict__,
                    "kill_signal": kill.__dict__,
                    "direct_worker_reaped": direct_reaped,
                    "group_kill_signal_sent": False,
                    "kqueue_note_exit_observed": False,
                }
            for task in (out_task, err_task):
                if task is not None:
                    task.cancel()
            if out_task is not None and err_task is not None:
                await asyncio.shield(asyncio.gather(out_task, err_task, return_exceptions=True))
            group_cleanup_proven = (
                cleanup_details.get("group_kill_signal_sent") is True
                or cleanup_details.get("pre_submission_abort") is True
            )
            cleanup = (
                cleanup_details.get("direct_worker_reaped") is True
                and cleanup_details.get("kqueue_note_exit_observed") is True
                and group_cleanup_proven
            )
        raise RuntimeError(f"Guard supervision failed; cleanup={cleanup_details}: {exc}") from exc
    finally:
        if watcher is not None:
            watcher.close()
        os.close(lifetime_fd)
    _status(directory, status, exit_code, cleanup, bytes(stdout_log), bytes(stderr_log), cleanup_details)


def main() -> None:
    require_supported_platform()
    config = load_config(GuardConfig, ["guard.yaml"])
    asyncio.run(_run(Path.cwd(), config))


if __name__ == "__main__":
    main()
