"""Isolated entry point for one candidate execution and its group anchor."""

import json
import os
import signal
import sys
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from slimconfig import Config, load_config


@dataclass
class WorkerConfig(Config):
    source: str
    input_dir: str
    output_path: str
    command: list[str] | None = None
    project_dir: str = "."


def _run_submission(config: WorkerConfig) -> None:
    if config.command is not None:
        os.chdir(config.project_dir)
        os.execvp(config.command[0], config.command)
    source = Path(config.source).resolve(strict=True)
    spec = spec_from_file_location("submission", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load source file: {source}")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    solve = getattr(module, "solve", None)
    if not callable(solve):
        raise TypeError("source.py must define callable solve(input_dir, output_path)")
    solve(config.input_dir, config.output_path)


def main() -> None:
    config = load_config(WorkerConfig, ["run.yaml"])
    # Keep the process-group leader alive after solve so its PGID cannot be reused
    # before guard cleanup. The submission child keeps normal signal behavior.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path("worker-ready").write_text(str(os.getpid()), encoding="ascii")
    submission_pid = os.fork()
    if submission_pid == 0:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        _run_submission(config)
        return

    _, wait_status = os.waitpid(submission_pid, 0)
    status_path = Path("worker-status.tmp")
    status_path.write_text(json.dumps({"exit_code": os.waitstatus_to_exitcode(wait_status)}), encoding="utf-8")
    status_path.replace("worker-status.json")
    while True:
        signal.pause()


if __name__ == "__main__":
    main()
