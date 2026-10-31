"""Pure structural and artifact checks for code-optimize reports."""

import json
import re
from dataclasses import asdict
from fractions import Fraction
from hashlib import sha256
from math import isfinite
from pathlib import Path

from contract import BASELINE_PREPARE, EvaluateOutput

from .statistics import summarize


def result(valid, message, *, score=None, **details):
    return {"valid": valid, "score": float(valid) if score is None else score, "message": message, "details": details}


def _relative_path(value):
    path = Path(value)
    parts = value.split("/")
    return (
        bool(value)
        and "\x00" not in value
        and not path.is_absolute()
        and "\\" not in value
        and all(part not in ("", ".", "..") for part in parts)
    )


def project_bytes(project):
    return json.dumps(asdict(project), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def validate_project(project, maximum):
    if len(project.prepare) > 16:
        raise ValueError("project exceeds the 16-command preparation limit")
    if not project.files or not project.run:
        raise ValueError("project must include files and a run command")
    names = set()
    for file in project.files:
        if not _relative_path(file.path):
            raise ValueError("project file path must be relative")
        if file.path in names or not isinstance(file.content, str):
            raise ValueError("project paths must be unique and contents must be text")
        names.add(file.path)
    if len(names) > 2000 or len(project_bytes(project)) > maximum:
        raise ValueError("candidate project exceeds the configured size or file limit")
    for command in [*project.prepare, project.run]:
        if not command or not all(isinstance(arg, str) and arg and "\x00" not in arg for arg in command):
            raise ValueError("commands must be nonempty argv arrays of nonempty strings")
        for arg in command:
            if arg.startswith("/") or "\\" in arg or ".." in arg.split("/"):
                raise ValueError("command arguments must not contain absolute or parent paths")
        if "/" in command[0]:
            executable = command[0].removeprefix("./")
            if not _relative_path(executable):
                raise ValueError("command executable must be project-relative")
    if project.run.count("{input_dir}") != 1 or project.run.count("{output_path}") != 1:
        raise ValueError("run argv must include {input_dir} and {output_path} exactly once")
    if any("{" in arg or "}" in arg for command in project.prepare for arg in command):
        raise ValueError("prepare commands do not support placeholders")
    if any(("{" in arg or "}" in arg) and arg not in ("{input_dir}", "{output_path}") for arg in project.run):
        raise ValueError("unsupported run placeholder")


def validate_config_values(config):
    """Validate the supported runtime, policy, paths, and bounded schedule."""
    execution = config.execution
    measurement = config.measurement
    policy = config.policy
    if (
        execution.runtime != "project_v1"
        or execution.interface != "executable_directory_v1"
        or measurement.metric != "wall_time"
        or measurement.order != "alternating"
    ):
        return False, "report requests an unsupported execution or measurement mode"
    numeric = (
        execution.per_trial_timeout_seconds,
        execution.total_timeout_seconds,
        policy.minimum_speedup,
        policy.maximum_case_slowdown,
        policy.maximum_relative_mad,
    )
    if not all(isfinite(value) for value in numeric):
        return False, "report contains non-finite numeric settings"
    if (
        execution.per_trial_timeout_seconds < 1
        or execution.total_timeout_seconds <= 0
        or min(execution.max_source_bytes, execution.max_output_bytes, execution.max_log_bytes) <= 0
        or measurement.warmup_pairs < 0
        or measurement.measured_pairs <= 0
        or policy.minimum_speedup <= 0
        or policy.maximum_case_slowdown <= 0
        or policy.maximum_relative_mad < 0
    ):
        return False, "report contains invalid limits or policy values"
    if policy.maximum_case_slowdown < 1 or policy.maximum_relative_mad >= 1:
        return False, "policy thresholds are outside the supported range"
    trial_count = 2 * len(config.cases) * (measurement.warmup_pairs + measurement.measured_pairs)
    if (
        execution.total_timeout_seconds > 120
        or execution.max_source_bytes > 1024 * 1024
        or execution.max_output_bytes > 4 * 1024 * 1024
        or execution.max_log_bytes > 1024 * 1024
        or trial_count > 256
        or trial_count * (execution.max_output_bytes + execution.max_log_bytes) > 64 * 1024 * 1024
    ):
        return False, "report exceeds the plugin's supported assessment limits"
    required_seconds = Fraction(str(execution.per_trial_timeout_seconds)) * trial_count * Fraction(6, 5)
    if Fraction(str(execution.total_timeout_seconds)) < required_seconds:
        return False, "declared schedule cannot fit inside its total timeout"
    slug = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    if not slug.fullmatch(config.id):
        return False, "problem id is invalid"
    if (
        not config.cases
        or not _relative_path(config.baseline)
        or not _relative_path(config.prompt)
        or config.baseline == "problem.yaml"
        or config.prompt == "problem.yaml"
        or config.baseline == config.prompt
    ):
        return False, "problem configuration paths or cases are invalid"
    case_ids = set()
    case_dirs = []
    for case in config.cases:
        if not slug.fullmatch(case.id) or case.id in case_ids or not _relative_path(case.input_dir):
            return False, "problem case id or input path is invalid"
        case_ids.add(case.id)
        case_dirs.append(case.input_dir.rstrip("/") + "/")
    protected = ("problem.yaml", config.baseline, config.prompt)
    for directory in case_dirs:
        if any(
            path == directory[:-1] or path.startswith(directory) or directory[:-1].startswith(path + "/")
            for path in protected
        ):
            return False, "fixture directory overlaps a configuration or source file"
    if any(a.startswith(b) or b.startswith(a) for index, a in enumerate(case_dirs) for b in case_dirs[index + 1 :]):
        return False, "fixture directories overlap"
    return True, "problem configuration is supported"


def _artifact(root, artifact, maximum=None):
    if not _relative_path(artifact.path):
        raise ValueError("artifact path must be workspace-relative")
    path = root / artifact.path
    cursor = root
    for part in Path(artifact.path).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("artifact path contains a symlink")
    metadata = path.lstat()
    if metadata.st_mode & 0o170000 != 0o100000:
        raise ValueError("artifact is not a regular file")
    limit = 50 * 1024 * 1024 if maximum is None else maximum
    if artifact.size < 0 or artifact.size > limit or metadata.st_size != artifact.size:
        raise ValueError("artifact exceeds its declared size limit")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("artifact exceeds its declared size limit")
    if len(data) != artifact.size or sha256(data).hexdigest() != artifact.sha256:
        raise ValueError("artifact size or SHA-256 does not match report")
    return data


def _schedule(config, trials):
    expected = []
    for case in config.cases:
        for pair in range(config.measurement.warmup_pairs + config.measurement.measured_pairs):
            order = ("baseline", "candidate") if pair % 2 == 0 else ("candidate", "baseline")
            expected.extend(
                (case.id, implementation, pair, pair < config.measurement.warmup_pairs) for implementation in order
            )
    actual = [(trial.case_id, trial.implementation, trial.pair, trial.warmup) for trial in trials]
    return actual == expected


def valid_runtime(runtime):
    fields = (
        runtime.plugin_version,
        runtime.python_implementation,
        runtime.python_version,
        runtime.platform,
        runtime.architecture,
        runtime.executable,
        runtime.clock,
    )
    return (
        all(fields)
        and Path(runtime.executable).is_absolute()
        and runtime.resolution_ns > 0
        and len(runtime.lock_sha256) == 64
        and all(character in "0123456789abcdef" for character in runtime.lock_sha256)
    )


def validate_evaluation(output, workspace, project=None):
    if not isinstance(output, EvaluateOutput):
        return False, "output is not an EvaluateOutput"
    config = output.problem_config
    if output.schema_version != 1 or config.schema_version != 1:
        return False, "unsupported report schema"
    supported, message = validate_config_values(config)
    if not supported:
        return False, message
    execution = config.execution
    runtime = output.runtime
    if not valid_runtime(runtime):
        return False, "runtime identity is invalid"
    if output.problem.origin not in ("bundled", "workspace") or not _relative_path(output.problem.directory):
        return False, "problem reference is invalid"
    slug = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    if (
        not slug.fullmatch(output.assessment_id)
        or not _relative_path(output.report_path)
        or output.report_path != f"assessments/{output.assessment_id}/report.json"
    ):
        return False, "assessment or report path is invalid"
    if not _schedule(config, output.trials):
        return False, "trial schedule is incomplete or duplicated"
    root = Path(workspace)
    evidence_size = (
        output.candidate.size
        + output.baseline.size
        + output.configuration.size
        + output.prompt.size
        + sum(item.size for item in output.inputs)
        + sum(item.command.size + item.stdout.size + item.stderr.size for item in output.preparation)
        + sum(
            trial.stdout.size + trial.stderr.size + (trial.result.size if trial.result is not None else 0)
            for trial in output.trials
        )
    )
    if evidence_size > 64 * 1024 * 1024:
        return False, "assessment evidence exceeds the plugin's supported size"
    candidate = _artifact(root, output.candidate, config.execution.max_source_bytes)
    if project is not None:
        validate_project(project, config.execution.max_source_bytes)
        if candidate != project_bytes(project):
            return False, "candidate artifact does not contain the submitted project"
    candidate_manifest = json.loads(candidate)
    candidate_commands = candidate_manifest["prepare"]
    expected_preparation = [("baseline", i, command) for i, command in enumerate(BASELINE_PREPARE)]
    expected_preparation += [("candidate", i, command) for i, command in enumerate(candidate_commands)]
    if len(output.preparation) != len(expected_preparation):
        return False, "preparation schedule does not match submitted commands"
    for evidence, (implementation, index, command) in zip(output.preparation, expected_preparation, strict=True):
        prefix = f"assessments/{output.assessment_id}/preparation/{implementation}/{index}/"
        if evidence.implementation != implementation or [
            evidence.command.path,
            evidence.stdout.path,
            evidence.stderr.path,
        ] != [prefix + "command.json", prefix + "stdout.log", prefix + "stderr.log"]:
            return False, "preparation artifacts do not match their schedule"
        command_bytes = json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if _artifact(root, evidence.command, config.execution.max_source_bytes) != command_bytes:
            return False, "preparation command does not match its project"
        stdout = _artifact(root, evidence.stdout, config.execution.max_log_bytes)
        stderr = _artifact(root, evidence.stderr, config.execution.max_log_bytes)
        if len(stdout) + len(stderr) > config.execution.max_log_bytes:
            return False, "combined preparation logs exceed the configured limit"
    _artifact(root, output.baseline, config.execution.max_source_bytes)
    _artifact(root, output.configuration, 1024 * 1024)
    _artifact(root, output.prompt, 1024 * 1024)
    if len(output.inputs) + 3 > 2000 or sum(item.size for item in output.inputs) > 32 * 1024 * 1024:
        return False, "fixture snapshot exceeds the supported bundle limits"
    if len({item.path for item in output.inputs}) != len(output.inputs):
        return False, "input artifact paths are duplicated"
    artifact_paths = [output.candidate.path, output.baseline.path, output.configuration.path, output.prompt.path]
    artifact_paths.extend(item.path for item in output.inputs)
    for item in output.preparation:
        artifact_paths.extend((item.command.path, item.stdout.path, item.stderr.path))
    for trial in output.trials:
        artifact_paths.extend([trial.stdout.path, trial.stderr.path])
        if trial.result is not None:
            artifact_paths.append(trial.result.path)
    if len(set(artifact_paths)) != len(artifact_paths):
        return False, "assessment artifacts reuse a workspace path"
    input_case_counts = {case.id: 0 for case in config.cases}
    for item in output.inputs:
        _artifact(root, item)
        input_path = item.path[len(f"assessments/{output.assessment_id}/snapshot/") :]
        for case in config.cases:
            if input_path.startswith(case.input_dir + "/"):
                input_case_counts[case.id] += 1
                break
    if any(count == 0 for count in input_case_counts.values()):
        return False, "fixture snapshot has no files for a configured case"
    result_data = {}
    for trial in output.trials:
        stdout = _artifact(root, trial.stdout, config.execution.max_log_bytes)
        stderr = _artifact(root, trial.stderr, config.execution.max_log_bytes)
        if len(stdout) + len(stderr) > config.execution.max_log_bytes:
            return False, "combined trial logs exceed the configured limit"
        if trial.elapsed_ns <= 0 or trial.implementation not in ("baseline", "candidate"):
            return False, "trial metadata is invalid"
        if Fraction(trial.elapsed_ns, 1_000_000_000) > Fraction(str(execution.per_trial_timeout_seconds)):
            return False, "trial elapsed time exceeds its declared timeout"
        if not trial.cleanup_confirmed:
            return False, "trial worker cleanup is unconfirmed"
        if trial.status not in {
            "ok",
            "timeout",
            "crash",
            "missing_output",
            "output_limit",
            "log_limit",
            "invalid_output",
            "input_modified",
        }:
            return False, "trial status is unsupported"
        if trial.observed_output_bytes is not None and trial.observed_output_bytes < 0:
            return False, "observed result size is negative"
        if trial.result is not None and trial.observed_output_bytes != trial.result.size:
            return False, "observed result size does not match its artifact"
        if trial.status == "output_limit" and (
            trial.result is not None
            or trial.observed_output_bytes is None
            or trial.observed_output_bytes <= execution.max_output_bytes
        ):
            return False, "oversized output evidence is inconsistent"
        if trial.status == "missing_output" and (trial.result is not None or trial.observed_output_bytes is not None):
            return False, "missing output evidence includes an observed result"
        if trial.status == "crash" and trial.exit_code == 0:
            return False, "crash status cannot have a successful exit code"
        if trial.status == "missing_output" and trial.exit_code is not None and trial.exit_code != 0:
            return False, "missing output status cannot have a failing exit code"
        if trial.status == "ok" and (
            trial.exit_code != 0
            or not trial.inputs_unchanged
            or trial.result is None
            or trial.observed_output_bytes != trial.result.size
        ):
            return False, "successful trial evidence is incomplete"
        if trial.implementation == "baseline" and (
            trial.status != "ok"
            or trial.exit_code != 0
            or not trial.inputs_unchanged
            or not trial.cleanup_confirmed
            or trial.result is None
        ):
            return False, "baseline execution failed"
        if trial.result is not None:
            data = _artifact(root, trial.result, config.execution.max_output_bytes)
            result_data[(trial.case_id, trial.implementation, trial.pair)] = data
    baseline_bytes = {}
    byte_matches = {}
    for case in config.cases:
        case_matches = []
        for pair in range(config.measurement.warmup_pairs + config.measurement.measured_pairs):
            key_b = (case.id, "baseline", pair)
            key_c = (case.id, "candidate", pair)
            btrial = next(t for t in output.trials if (t.case_id, t.implementation, t.pair) == key_b)
            ctrial = next(t for t in output.trials if (t.case_id, t.implementation, t.pair) == key_c)
            same = (
                btrial.result is not None
                and ctrial.result is not None
                and result_data.get(key_b) == result_data.get(key_c)
            )
            if btrial.result is not None:
                prior = baseline_bytes.get(case.id)
                if prior is not None and prior != result_data[key_b]:
                    return False, "baseline output changed across identical fixture trials"
                baseline_bytes[case.id] = result_data[key_b]
            trial_prefix = f"assessments/{output.assessment_id}/trials/{case.id}/{pair}/"
            if (
                btrial.stdout.path != trial_prefix + "baseline/stdout.log"
                or btrial.stderr.path != trial_prefix + "baseline/stderr.log"
                or ctrial.stdout.path != trial_prefix + "candidate/stdout.log"
                or ctrial.stderr.path != trial_prefix + "candidate/stderr.log"
                or (btrial.result is not None and btrial.result.path != trial_prefix + "baseline/result.bin")
                or (ctrial.result is not None and ctrial.result.path != trial_prefix + "candidate/result.bin")
            ):
                return False, "trial artifact paths do not match the assessment schedule"
            case_matches.append(same)
        byte_matches[case.id] = all(case_matches)
    expected_summary = summarize(config, output.trials)
    if output.summary != expected_summary:
        return False, "summary does not match raw trials"
    if any(item.outputs_match != byte_matches[item.case_id] for item in output.summary.cases):
        return False, "summary output-equivalence flags do not match result bytes"
    report_path = root / output.report_path
    report_cursor = root
    for part in Path(output.report_path).parts:
        report_cursor = report_cursor / part
        if report_cursor.is_symlink():
            return False, "report path contains a symlink"
    if report_path.is_symlink() or not report_path.is_file():
        return False, "report file is missing or is a symlink"
    try:
        report_metadata = report_path.lstat()
        if report_metadata.st_mode & 0o170000 != 0o100000 or evidence_size + report_metadata.st_size > 64 * 1024 * 1024:
            return False, "report file is not regular or exceeds the verifier size limit"
        with report_path.open("rb") as stream:
            report_bytes = stream.read(50 * 1024 * 1024 + 1)
        if len(report_bytes) > 50 * 1024 * 1024:
            return False, "report file exceeds the verifier size limit"
        report = json.loads(report_bytes.decode("utf-8"))
    except (ValueError, UnicodeError):
        return False, "report file is not valid JSON"
    if report != asdict(output):
        return False, "report JSON does not match the typed assessment"
    if output.candidate.path != f"assessments/{output.assessment_id}/candidate.json":
        return False, "candidate artifact path is not bound to this assessment"
    if output.baseline.path != f"assessments/{output.assessment_id}/snapshot/{config.baseline}":
        return False, "baseline artifact path is not bound to the problem configuration"
    if output.configuration.path != f"assessments/{output.assessment_id}/snapshot/problem.yaml":
        return False, "configuration artifact path is not bound to this assessment"
    if output.prompt.path != f"assessments/{output.assessment_id}/snapshot/{config.prompt}":
        return False, "prompt artifact path is not bound to the problem configuration"
    prefix = f"assessments/{output.assessment_id}/snapshot/"
    if any(not item.path.startswith(prefix) for item in output.inputs):
        return False, "input artifact path is not bound to this assessment"
    for item in output.inputs:
        bundle_path = item.path[len(prefix) :]
        if not _relative_path(bundle_path) or not any(
            bundle_path.startswith(case.input_dir + "/") for case in config.cases
        ):
            return False, "input artifact is outside the configured fixture directories"
    if output.fingerprint != assessment_fingerprint(
        config,
        output.candidate,
        output.baseline,
        output.configuration,
        output.prompt,
        output.inputs,
    ):
        return False, "assessment fingerprint does not match its evidence"
    return True, "evaluation report is internally consistent"


def assessment_fingerprint(config, candidate, baseline, configuration, prompt, inputs):
    """Hash resolved problem data and sorted source bundle identities plus candidate."""
    identities = [configuration, baseline, prompt, *inputs]
    files = [
        {"path": item.path.split("/snapshot/", 1)[1], "sha256": item.sha256, "size": item.size} for item in identities
    ]
    files.sort(key=lambda item: item["path"])
    problem = {"config": asdict(config), "files": files}
    candidate_identity = {"path": "candidate.json", "sha256": candidate.sha256, "size": candidate.size}
    payload = {"problem": problem, "candidate": candidate_identity}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256(encoded).hexdigest()
