"""Validate and stage explicit, shell-free candidate projects."""

import shutil

from plugin.execution import ExecutionEnvironmentError, run_trial


def stage_project(project, directory):
    directory.mkdir(parents=True)
    for file in project.files:
        target = directory / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.content, encoding="utf-8")


def checked_command(command, directory):
    executable = command[0]
    if "/" in executable:
        path = directory / executable
        if not path.is_file():
            raise ExecutionEnvironmentError(f"project executable is missing: {executable}")
    elif shutil.which(executable) is None:
        raise ExecutionEnvironmentError(
            f"required tool is not installed or not on PATH: {executable}; command={command!r}"
        )
    return command


async def prepare_project(project, directory, control, timeout, max_log):
    results = []
    for index, command in enumerate(project.prepare):
        control_dir = control / str(index)
        (control_dir / "inputs").mkdir(parents=True)
        executed = await run_trial(
            control_dir, timeout, 0, max_log, checked_command(command, directory), directory, prepare=True
        )
        if not executed.cleanup_confirmed or executed.exit_code != 0 or executed.status != "missing_output":
            raise ExecutionEnvironmentError(
                f"project preparation failed: command={command!r}, status={executed.status}, "
                f"exit={executed.exit_code}, stdout={executed.stdout.decode('utf-8', 'replace')!r}, "
                f"stderr={executed.stderr.decode('utf-8', 'replace')!r}"
            )
        results.append(executed)
    return results
