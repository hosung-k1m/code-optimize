"""Snapshot, execute and report an explicitly selected optimization assessment."""

import hashlib
import json
import math
import platform
import shutil
import stat
import sys
import time
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import anyio
from slimconfig import plain_output

import contract
from contract import (
    Artifact,
    CandidateProject,
    DescribeOutput,
    EvaluateOutput,
    Preparation,
    ProjectFile,
    RuntimeIdentity,
    Trial,
)
from plugin.bundles import load_problem
from plugin.execution import ExecutionEnvironmentError, probe_runtime, run_trial
from plugin.process_control import require_supported_platform
from plugin.project import checked_command, prepare_project, stage_project
from verifier.checks import assessment_fingerprint, project_bytes, validate_project
from verifier.statistics import summarize


class BaselineError(RuntimeError):
    """The supplied expected-output oracle could not establish a stable result."""


def runtime_identity(execution, commands=()):
    lock = Path(contract.__file__).with_name("uv.lock")
    if not lock.is_file():
        raise ExecutionEnvironmentError("installed plugin root must contain uv.lock")
    clock = time.get_clock_info("perf_counter")
    if not clock.monotonic:
        raise ExecutionEnvironmentError("perf_counter is not monotonic")
    return RuntimeIdentity(
        plugin_version=version("nanoagent-code-optimize"),
        python_implementation=platform.python_implementation(),
        python_version=platform.python_version(),
        platform=sys.platform,
        architecture=platform.machine(),
        executable=sys.executable,
        lock_sha256=hashlib.sha256(lock.read_bytes()).hexdigest(),
        clock=clock.implementation,
        resolution_ns=max(1, math.ceil(clock.resolution * 1_000_000_000)),
        tools=[f"{name}={shutil.which(name) or 'unavailable'}" for name in sorted(set(commands))],
        wasmtime_version=version("wasmtime"),
        zig_version=version("ziglang"),
        wasi_memory_bytes=execution.wasi_memory_bytes,
        wasi_fuel=execution.wasi_fuel,
    )


def _artifact(workspace, path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return Artifact(path.relative_to(workspace).as_posix(), hashlib.sha256(data).hexdigest(), len(data))


def _input_files(path, expected):
    if path.is_symlink() or not path.is_dir():
        return None
    result = {}
    directories = {parent.as_posix() for name in expected for parent in Path(name).parents if parent != Path(".")}
    for item in path.rglob("*"):
        mode = item.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            return None
        if stat.S_ISDIR(mode) and item.relative_to(path).as_posix() not in directories:
            return None
        if stat.S_ISREG(mode):
            name = item.relative_to(path).as_posix()
            if name not in expected or item.stat().st_size != len(expected[name]):
                return None
            with item.open("rb") as stream:
                result[name] = stream.read(len(expected[name]) + 1)
    return result


async def describe_problem(value, workspace):
    bundle = load_problem(value.problem, workspace)
    return DescribeOutput(
        prompt=bundle.prompt.decode("utf-8") + (
            "\n\n## Benchmark runtime\n"
            f"WASI runs with one execution thread, {bundle.config.execution.wasi_memory_bytes} bytes "
            f"of linear memory, and {bundle.config.execution.wasi_fuel} instruction fuel per trial. "
            "Wall time depends on the host CPU and competing workloads.\n"
        ),
        baseline_source=bundle.baseline.decode("utf-8"),
        problem_config=bundle.config,
        runtime=runtime_identity(bundle.config.execution, ("python3",)),
        case_summaries=[
            f"{case.id}: {len(bundle.inputs[case.id])} input file(s), "
            f"{sum(map(len, bundle.inputs[case.id].values()))} bytes"
            for case in bundle.config.cases
        ],
    )


async def evaluate_code(value, workspace):
    require_supported_platform()
    started = time.perf_counter()
    workspace = workspace.resolve(strict=True)
    bundle = load_problem(value.problem, workspace)
    config = bundle.config
    validate_project(value.project, config.execution.max_source_bytes)
    source = project_bytes(value.project)
    baseline_project = CandidateProject(
        files=[ProjectFile(path="baseline.cpp", content=bundle.baseline.decode("utf-8"))],
        prepare=contract.BASELINE_PREPARE,
        run=["./baseline.wasm", "{input_dir}", "{output_path}"],
    )
    trial_count = 2 * len(config.cases) * (config.measurement.warmup_pairs + config.measurement.measured_pairs)
    build_commands = [*baseline_project.prepare, *value.project.prepare]
    snapshot_bytes = (
        len(bundle.configuration)
        + len(bundle.prompt)
        + len(bundle.baseline)
        + sum(len(data) for case in bundle.inputs.values() for data in case.values())
    )
    maximum_evidence = (
        snapshot_bytes
        + len(source)
        + 1024 * 1024
        + sum(
            len(json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            for command in build_commands
        )
        + len(build_commands) * config.execution.max_log_bytes
        + trial_count * (config.execution.max_output_bytes + config.execution.max_log_bytes)
    )
    if maximum_evidence > 64 * 1024 * 1024:
        raise ValueError("preparation and trial evidence exceed the 64 MiB assessment limit")
    assessment_id = uuid4().hex
    folder = workspace / "assessments" / assessment_id
    # An existing symlink at the assessment root must never redirect writes.
    if (workspace / "assessments").is_symlink():
        raise ValueError("assessments directory is a symlink")
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "incomplete.txt").write_text(
        "Assessment incomplete. No report or verdict has been published.\n", encoding="utf-8"
    )
    candidate = _artifact(workspace, folder / "candidate.json", source)
    snapshot = folder / "snapshot"
    baseline = _artifact(workspace, snapshot / config.baseline, bundle.baseline)
    configuration = _artifact(workspace, snapshot / "problem.yaml", bundle.configuration)
    prompt = _artifact(workspace, snapshot / config.prompt, bundle.prompt)
    inputs = []
    for case in config.cases:
        for name, data in sorted(bundle.inputs[case.id].items()):
            path = snapshot / case.input_dir / name
            if any(item.path == path.relative_to(workspace).as_posix() for item in inputs):
                continue
            inputs.append(_artifact(workspace, path, data))
    commands = ["python3", *(command[0] for command in value.project.prepare), value.project.run[0]]
    identity = runtime_identity(config.execution, (name for name in commands if "/" not in name))
    trials = []
    preparation = []
    try:
        with anyio.fail_after(config.execution.total_timeout_seconds - (time.perf_counter() - started)):
            await probe_runtime(workspace)
            projects = {"baseline": baseline_project, "candidate": value.project}
            prepared = {}
            for name, project in projects.items():
                project_dir = folder / "prepared" / name
                stage_project(project, project_dir)
                results = await prepare_project(
                    project,
                    project_dir,
                    folder / "build-control" / name,
                    min(30.0, config.execution.total_timeout_seconds),
                    config.execution.max_log_bytes,
                )
                for index, (command, executed) in enumerate(zip(project.prepare, results, strict=True)):
                    evidence_dir = folder / "preparation" / name / str(index)
                    argv = json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    preparation.append(
                        Preparation(
                            name,
                            _artifact(workspace, evidence_dir / "command.json", argv),
                            _artifact(workspace, evidence_dir / "stdout.log", executed.stdout),
                            _artifact(workspace, evidence_dir / "stderr.log", executed.stderr),
                        )
                    )
                checked_command(project.run, project_dir)
                prepared[name] = project_dir
            for case in config.cases:
                expected = None
                count = config.measurement.warmup_pairs + config.measurement.measured_pairs
                for pair in range(count):
                    order = ("baseline", "candidate") if pair % 2 == 0 else ("candidate", "baseline")
                    for implementation in order:
                        directory = folder / "trials" / case.id / str(pair) / implementation
                        directory.mkdir(parents=True)
                        project_dir = directory / "project"
                        shutil.copytree(prepared[implementation], project_dir, symlinks=False)
                        for name, data in bundle.inputs[case.id].items():
                            target = directory / "inputs" / name
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(data)
                        executed = await run_trial(
                            directory,
                            config.execution.per_trial_timeout_seconds,
                            config.execution.max_output_bytes,
                            config.execution.max_log_bytes,
                            projects[implementation].run,
                            project_dir,
                            wasi_memory_bytes=config.execution.wasi_memory_bytes,
                            wasi_fuel=config.execution.wasi_fuel,
                        )
                        if not executed.cleanup_confirmed or executed.status == "controller_lost":
                            raise ExecutionEnvironmentError("guard did not complete a supervised execution")
                        result_dir = directory / "result"
                        invalid_result_directory = any(item.name != "result.bin" for item in result_dir.iterdir())
                        if (result_dir / "result.bin").exists() or (result_dir / "result.bin").is_symlink():
                            (result_dir / "result.bin").rename(directory / "result.bin")
                        if not invalid_result_directory:
                            result_dir.rmdir()
                        unchanged = _input_files(directory / "inputs", bundle.inputs[case.id]) == bundle.inputs[case.id]
                        status = executed.status if unchanged else "input_modified"
                        allowed = {
                            "project",
                            "inputs",
                            "run.yaml",
                            "guard.yaml",
                            "guard-status.json",
                            "guard-status.tmp",
                            "worker-ready",
                            "worker-status.json",
                            "worker-status.tmp",
                            "__pycache__",
                            "result.bin",
                        }
                        if invalid_result_directory or any(item.name not in allowed for item in directory.iterdir()):
                            status = "invalid_output"
                        result = None
                        data = None
                        observed_output_bytes = None
                        output_path = directory / "result.bin"
                        if output_path.exists() or output_path.is_symlink():
                            info = output_path.lstat()
                            if not stat.S_ISREG(info.st_mode):
                                status = "invalid_output"
                            elif info.st_size > config.execution.max_output_bytes:
                                observed_output_bytes = info.st_size
                                status = "output_limit"
                            else:
                                observed_output_bytes = info.st_size
                                data = output_path.read_bytes()
                                result = Artifact(
                                    output_path.relative_to(workspace).as_posix(),
                                    hashlib.sha256(data).hexdigest(),
                                    len(data),
                                )
                        if implementation == "baseline":
                            if status != "ok" or result is None:
                                raise BaselineError(f"baseline failed in {case.id}, pair {pair}: {status}")
                            if expected is None:
                                expected = data
                            elif data != expected:
                                raise BaselineError(f"baseline outputs are inconsistent in {case.id}")
                        trials.append(
                            Trial(
                                case_id=case.id,
                                implementation=implementation,
                                pair=pair,
                                warmup=pair < config.measurement.warmup_pairs,
                                elapsed_ns=executed.elapsed_ns,
                                exit_code=executed.exit_code,
                                status=status,
                                result=result,
                                stdout=_artifact(workspace, directory / "stdout.log", executed.stdout),
                                stderr=_artifact(workspace, directory / "stderr.log", executed.stderr),
                                inputs_unchanged=unchanged,
                                cleanup_confirmed=executed.cleanup_confirmed,
                                observed_output_bytes=observed_output_bytes,
                            )
                        )
                        # Only the bounded result and captured streams are public trial artifacts.
                        for item in list(directory.iterdir()):
                            if item.name in {"stdout.log", "stderr.log"} or (item.name == "result.bin" and result):
                                continue
                            if item.is_dir() and not item.is_symlink():
                                shutil.rmtree(item)
                            else:
                                item.unlink()
    except TimeoutError as error:
        raise ExecutionEnvironmentError("assessment exceeded total_timeout_seconds; workers were cleaned up") from error
    shutil.rmtree(folder / "prepared")
    shutil.rmtree(folder / "build-control")
    output = EvaluateOutput(
        schema_version=1,
        assessment_id=assessment_id,
        problem=value.problem,
        problem_config=config,
        fingerprint=assessment_fingerprint(config, candidate, baseline, configuration, prompt, inputs),
        candidate=candidate,
        baseline=baseline,
        configuration=configuration,
        prompt=prompt,
        inputs=inputs,
        runtime=identity,
        preparation=preparation,
        trials=trials,
        summary=summarize(config, trials),
        report_path=(folder / "report.json").relative_to(workspace).as_posix(),
    )
    report = json.dumps(plain_output(output), ensure_ascii=False, indent=2)
    if time.perf_counter() - started >= config.execution.total_timeout_seconds:
        raise ExecutionEnvironmentError("assessment budget expired before report publication")
    (folder / "report.json").write_text(report, encoding="utf-8")
    (folder / "incomplete.txt").unlink()
    return output
