"""macOS process-group signaling and direct-child exit observation."""

import os
import select
import sys
from dataclasses import dataclass


def execution_environment() -> dict[str, str]:
    """Preserve runtime paths, dependency settings and configured network proxies."""
    allowed = {
        "PATH",
        "HOME",
        "TMPDIR",
        "TEMP",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    }
    return {key: value for key, value in os.environ.items() if key in allowed or key.startswith("UV_")}


class UnsupportedPlatformError(RuntimeError):
    pass


@dataclass(frozen=True)
class SignalResult:
    sent: bool
    process_group_absent: bool
    error: str | None


class ProcessExitWatcher:
    """kqueue NOTE_EXIT watcher registered while the process is still owned."""

    def __init__(self, pid: int):
        self._queue = select.kqueue()
        event = select.kevent(
            pid,
            filter=select.KQ_FILTER_PROC,
            flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
            fflags=select.KQ_NOTE_EXIT,
        )
        try:
            self._queue.control([event], 0, 0)
        except OSError:
            self._queue.close()
            raise
        self.pid = pid

    def wait(self, timeout: float) -> bool:
        if timeout <= 0:
            return False
        events = self._queue.control(None, 1, timeout)
        return any(
            event.ident == self.pid
            and event.filter == select.KQ_FILTER_PROC
            and event.fflags & select.KQ_NOTE_EXIT
            and not event.flags & select.KQ_EV_ERROR
            for event in events
        )

    def close(self) -> None:
        self._queue.close()


def require_supported_platform() -> None:
    if os.name != "posix" or sys.platform != "darwin":
        raise UnsupportedPlatformError(
            f"Guarded execution is unsupported on {sys.platform}; tested POSIX process ownership is required"
        )


def signal_group(pgid: int, signum: int) -> SignalResult:
    """Send one signal to the anchored worker process group."""
    try:
        os.killpg(pgid, signum)
        return SignalResult(True, False, None)
    except ProcessLookupError:
        return SignalResult(False, True, None)
    except PermissionError as exc:
        return SignalResult(False, False, f"signal {signum}: {exc}")
