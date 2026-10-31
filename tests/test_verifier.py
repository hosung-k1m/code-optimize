import json
from dataclasses import asdict, replace
from hashlib import sha256
from statistics import median

import pytest

from contract import (
    BASELINE_PREPARE,
    Artifact,
    CandidateProject,
    CaseConfig,
    EvaluateOutput,
    ExecutionSettings,
    MeasurementSettings,
    Policy,
    Preparation,
    ProblemConfig,
    ProblemReference,
    ProjectFile,
    RuntimeIdentity,
    Trial,
)
from verifier.checks import assessment_fingerprint, project_bytes, validate_config_values, validate_evaluation
from verifier.statistics import summarize


def config(minimum=1.1, slowdown=1.25, mad=0.05):
    return ProblemConfig(
        schema_version=1,
        id="fixture",
        prompt="prompt.md",
        baseline="baseline.cpp",
        execution=ExecutionSettings("wasi_project_v1", "executable_directory_v1", 2, 20, 1000, 1000, 1000, 2147483648, 30000000000),
        measurement=MeasurementSettings("wall_time", 1, 3, "alternating"),
        policy=Policy(minimum, slowdown, mad),
        cases=[CaseConfig("one", "fixtures/one")],
    )


def trials(base, candidate, *, status="ok", same=True):
    from contract import Artifact

    result_b = Artifact("b", "a" * 64, 2)
    result_c = result_b if same else Artifact("c", "b" * 64, 2)
    evidence = []
    for pair in range(4):
        warmup = pair == 0
        pair_trials = {
            "baseline": Trial(
                "one",
                "baseline",
                pair,
                warmup,
                base,
                0,
                "ok",
                result_b,
                Artifact(f"out-{pair}-b", "c" * 64, 0),
                Artifact(f"err-{pair}-b", "d" * 64, 0),
                True,
                True,
                2,
            ),
            "candidate": Trial(
                "one",
                "candidate",
                pair,
                warmup,
                candidate,
                0,
                status if status != "ok" else "ok",
                result_c,
                Artifact(f"out-{pair}-c", "c" * 64, 0),
                Artifact(f"err-{pair}-c", "d" * 64, 0),
                True,
                True,
                2,
            ),
        }
        order = ("baseline", "candidate") if pair % 2 == 0 else ("candidate", "baseline")
        evidence.extend(pair_trials[name] for name in order)
    return evidence


def test_paired_median_and_warmup_exclusion():
    evidence = trials(110, 100)
    evidence[0].elapsed_ns = 10**8  # warmup timing cannot affect the measurement
    evidence[1].elapsed_ns = 1
    summary = summarize(config(), evidence)
    assert summary.reason == "pass"
    assert summary.suite_speedup == 1.1
    assert summary.eligible


def test_median_of_paired_ratios_not_ratio_of_medians():
    evidence = trials(100, 100)
    base = {1: 100, 2: 1000, 3: 1000}
    candidate = {1: 100, 2: 1000, 3: 100}
    for trial in evidence:
        if trial.pair in base:
            trial.elapsed_ns = (base if trial.implementation == "baseline" else candidate)[trial.pair]
    summary = summarize(config(minimum=1), evidence)
    assert summary.reason == "pass"
    assert summary.cases[0].speedup == 1
    assert median([100, 1000, 1000]) / median([100, 1000, 100]) == 10


def test_relative_mad_exact_boundary_passes():
    evidence = trials(100, 100)
    baseline = {1: 95, 2: 100, 3: 105}
    for trial in evidence:
        if trial.implementation == "baseline" and trial.pair in baseline:
            trial.elapsed_ns = baseline[trial.pair]
    summary = summarize(config(minimum=1, mad=0.05), evidence)
    assert summary.reason == "pass"
    assert summary.cases[0].baseline_relative_mad == 0.05


def test_geometric_suite_and_slowdown_equality_pass():
    from contract import CaseConfig

    value = config(minimum=1, slowdown=2)
    value.cases.append(CaseConfig("two", "fixtures/two"))
    evidence = trials(200, 100)
    for pair in range(4):
        pair_trials = {
            "baseline": Trial(
                "two",
                "baseline",
                pair,
                pair == 0,
                100,
                0,
                "ok",
                Artifact(f"b-{pair}", "a" * 64, 2),
                Artifact(f"bo-{pair}", "c" * 64, 0),
                Artifact(f"be-{pair}", "d" * 64, 0),
                True,
                True,
                2,
            ),
            "candidate": Trial(
                "two",
                "candidate",
                pair,
                pair == 0,
                200,
                0,
                "ok",
                Artifact(f"c-{pair}", "a" * 64, 2),
                Artifact(f"co-{pair}", "c" * 64, 0),
                Artifact(f"ce-{pair}", "d" * 64, 0),
                True,
                True,
                2,
            ),
        }
        order = ("baseline", "candidate") if pair % 2 == 0 else ("candidate", "baseline")
        evidence.extend(pair_trials[name] for name in order)
    summary = summarize(value, evidence)
    assert summary.reason == "pass"
    assert summary.suite_speedup == 1
    value.policy.maximum_case_slowdown = 1.999
    assert summarize(value, evidence).reason == "case_regression"


def test_wrong_output_is_a_valid_failed_assessment():
    summary = summarize(config(), trials(200, 100, same=False))
    assert summary.reason == "wrong_output"
    assert summary.eligible is False
    assert summary.cases[0].outputs_match is False


def test_execution_failure_precedes_missing_output_classification():
    summary = summarize(config(), trials(200, 100, status="timeout", same=False))
    assert summary.reason == "execution_failure"


def test_mad_gate_is_deterministic():
    evidence = trials(200, 100)
    for trial in evidence:
        if trial.implementation == "baseline" and not trial.warmup:
            trial.elapsed_ns = {1: 300, 2: 400}.get(trial.pair, 200)
    assert summarize(config(mad=0.01), evidence).reason == "unstable_timing"


def test_case_regression_vetoes_a_fast_suite():
    from contract import CaseConfig

    value = config(minimum=0.5, slowdown=1.25)
    value.cases.append(CaseConfig("two", "fixtures/two"))
    evidence = trials(200, 20)
    for pair in range(4):
        pair_trials = {
            "baseline": Trial(
                "two",
                "baseline",
                pair,
                pair == 0,
                100,
                0,
                "ok",
                Artifact(f"two-b-{pair}", "a" * 64, 2),
                Artifact(f"two-bo-{pair}", "c" * 64, 0),
                Artifact(f"two-be-{pair}", "d" * 64, 0),
                True,
                True,
                2,
            ),
            "candidate": Trial(
                "two",
                "candidate",
                pair,
                pair == 0,
                200,
                0,
                "ok",
                Artifact(f"two-c-{pair}", "a" * 64, 2),
                Artifact(f"two-co-{pair}", "c" * 64, 0),
                Artifact(f"two-ce-{pair}", "d" * 64, 0),
                True,
                True,
                2,
            ),
        }
        order = ("baseline", "candidate") if pair % 2 == 0 else ("candidate", "baseline")
        evidence.extend(pair_trials[name] for name in order)
    summary = summarize(value, evidence)
    assert summary.reason == "case_regression"
    assert summary.suite_speedup > 1


def make_valid_assessment(tmp_path, *, baseline_ns=220, candidate_ns=200):
    root = tmp_path
    assessment_id = "assessment-a"
    prefix = f"assessments/{assessment_id}/snapshot/"

    def save(relative, data):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return Artifact(relative, sha256(data).hexdigest(), len(data))

    candidate_source = CandidateProject(
        [ProjectFile("main.wat", "(module)")], [], ["./main.wat", "{input_dir}", "{output_path}"]
    )
    candidate = save(f"assessments/{assessment_id}/candidate.json", project_bytes(candidate_source))
    baseline = save(prefix + "baseline.cpp", b"int main() {}\n")
    configuration = save(prefix + "problem.yaml", b"schema_version: 1\n")
    prompt = save(prefix + "prompt.md", b"solve\n")
    inputs = [save(prefix + "fixtures/one/input.txt", b"input\n")]
    cfg = config(minimum=1.1)
    trials_list = []
    for pair in range(4):
        order = (
            (("baseline", baseline_ns), ("candidate", candidate_ns))
            if pair % 2 == 0
            else (("candidate", candidate_ns), ("baseline", baseline_ns))
        )
        for implementation, elapsed in order:
            trial_dir = f"assessments/{assessment_id}/trials/one/{pair}/{implementation}"
            result = save(f"{trial_dir}/result.bin", b"ok")
            stdout = save(f"{trial_dir}/stdout.log", b"")
            stderr = save(f"{trial_dir}/stderr.log", b"")
            trials_list.append(
                Trial(
                    "one",
                    implementation,
                    pair,
                    pair == 0,
                    elapsed,
                    0,
                    "ok",
                    result,
                    stdout,
                    stderr,
                    True,
                    True,
                    2,
                )
            )
    preparation = []
    for index, command in enumerate(BASELINE_PREPARE):
        prefix = f"assessments/{assessment_id}/preparation/baseline/{index}/"
        argv = json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        preparation.append(
            Preparation(
                "baseline",
                save(prefix + "command.json", argv),
                save(prefix + "stdout.log", b""),
                save(prefix + "stderr.log", b""),
            )
        )
    runtime = RuntimeIdentity("1", "CPython", "3.12", "darwin", "arm64", "/python", "a" * 64,
                              "perf_counter", 1, [], "50.0.1", "0.14.1", 2147483648, 30000000000)
    fingerprint = assessment_fingerprint(cfg, candidate, baseline, configuration, prompt, inputs)
    output = EvaluateOutput(
        1,
        assessment_id,
        ProblemReference("workspace", "problem"),
        cfg,
        fingerprint,
        candidate,
        baseline,
        configuration,
        prompt,
        inputs,
        runtime,
        preparation,
        trials_list,
        summarize(cfg, trials_list),
        f"assessments/{assessment_id}/report.json",
    )
    report_path = root / output.report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(asdict(output), sort_keys=True), encoding="utf-8")

    return root, output, candidate_source, report_path


def write_report(output, report_path):
    report_path.write_text(json.dumps(asdict(output), sort_keys=True), encoding="utf-8")


def test_report_artifacts_hashes_schedule_and_json_are_bound(tmp_path):
    root, output, source, report_path = make_valid_assessment(tmp_path)
    assert validate_evaluation(output, root, source)[0]
    assert not validate_evaluation(output, root, replace(source, files=[ProjectFile("main.py", "changed")]))[0]

    output.trials[0], output.trials[1] = output.trials[1], output.trials[0]
    assert not validate_evaluation(output, root, source)[0]
    output.trials[0], output.trials[1] = output.trials[1], output.trials[0]
    output.trials.append(output.trials[-1])
    assert not validate_evaluation(output, root, source)[0]
    output.trials.pop()
    original_trials = output.trials
    output.trials = original_trials[:-1]
    assert not validate_evaluation(output, root, source)[0]
    output.trials = original_trials

    output.summary.suite_speedup += 0.1
    assert not validate_evaluation(output, root, source)[0]
    output.summary = summarize(output.problem_config, original_trials)

    result_path = root / output.trials[0].result.path
    result_path.write_bytes(b"no")
    with pytest.raises(ValueError):
        validate_evaluation(output, root, source)
    result_path.write_bytes(b"ok")

    output.candidate.sha256 = "0" * 64
    with pytest.raises(ValueError):
        validate_evaluation(output, root, source)
    output.candidate.sha256 = sha256(project_bytes(source)).hexdigest()

    report_path.write_text("{}", encoding="utf-8")
    assert not validate_evaluation(output, root, source)[0]


def test_binary_trailing_newline_is_a_wrong_output(tmp_path):
    root, output, source, report_path = make_valid_assessment(tmp_path)
    for trial in output.trials:
        if trial.implementation == "candidate":
            path = root / trial.result.path
            data = b"ok\n"
            path.write_bytes(data)
            trial.result.sha256 = sha256(data).hexdigest()
            trial.result.size = len(data)
            trial.observed_output_bytes = len(data)
    output.summary = summarize(output.problem_config, output.trials)
    write_report(output, report_path)
    valid, _ = validate_evaluation(output, root, source)
    assert valid
    assert output.summary.reason == "wrong_output"
    assert not output.summary.cases[0].outputs_match


def test_symlink_and_traversal_artifacts_are_rejected(tmp_path):
    root, output, source, _ = make_valid_assessment(tmp_path)
    log = root / output.trials[0].stdout.path
    log.unlink()
    log.symlink_to(root / output.trials[1].stdout.path)
    with pytest.raises(ValueError):
        validate_evaluation(output, root, source)

    root, output, source, _ = make_valid_assessment(tmp_path / "second")
    output.trials[0].stdout.path = "../../outside"
    with pytest.raises(ValueError):
        validate_evaluation(output, root, source)


def test_baseline_output_must_be_stable_across_trials(tmp_path):
    root, output, source, _ = make_valid_assessment(tmp_path)
    trial = next(item for item in output.trials if item.implementation == "baseline" and item.pair == 2)
    path = root / trial.result.path
    data = b"different"
    path.write_bytes(data)
    trial.result.sha256 = sha256(data).hexdigest()
    trial.result.size = len(data)
    assert not validate_evaluation(output, root, source)[0]


def test_invalid_timing_and_configuration_values_are_rejected(tmp_path):
    root, output, source, _ = make_valid_assessment(tmp_path)
    output.trials[0].elapsed_ns = 0
    assert not validate_evaluation(output, root, source)[0]

    cfg = config()
    cfg.execution.interface = "other"
    assert not validate_config_values(cfg)[0]
    cfg = config()
    cfg.policy.minimum_speedup = float("nan")
    assert not validate_config_values(cfg)[0]
    cfg = config()
    cfg.cases[0].input_dir = "fixtures/../outside"
    assert not validate_config_values(cfg)[0]


def test_observed_output_size_validates_oversized_and_missing_results(tmp_path):
    root, output, source, report_path = make_valid_assessment(tmp_path)
    trial = next(item for item in output.trials if item.implementation == "candidate")
    trial.status = "output_limit"
    trial.result = None
    trial.observed_output_bytes = output.problem_config.execution.max_output_bytes + 1
    output.summary = summarize(output.problem_config, output.trials)
    write_report(output, report_path)
    assert validate_evaluation(output, root, source)[0]
    assert output.summary.reason == "execution_failure"
    trial.observed_output_bytes = output.problem_config.execution.max_output_bytes
    assert not validate_evaluation(output, root, source)[0]
    trial.observed_output_bytes = output.problem_config.execution.max_output_bytes + 1

    trial.status = "missing_output"
    trial.observed_output_bytes = None
    trial.exit_code = 2
    output.summary = summarize(output.problem_config, output.trials)
    write_report(output, report_path)
    assert not validate_evaluation(output, root, source)[0]

    trial.status = "crash"
    trial.exit_code = 0
    output.summary = summarize(output.problem_config, output.trials)
    write_report(output, report_path)
    assert not validate_evaluation(output, root, source)[0]

    trial.status = "controller_lost"
    trial.exit_code = None
    output.summary = summarize(output.problem_config, output.trials)
    write_report(output, report_path)
    assert not validate_evaluation(output, root, source)[0]

    trial.status = "ok"
    trial.result = output.trials[0].result
    trial.observed_output_bytes = -1
    assert not validate_evaluation(output, root, source)[0]


def test_geometric_display_handles_large_exact_products():
    cfg = config()
    cfg.measurement.warmup_pairs = 0
    cfg.measurement.measured_pairs = 1
    cfg.execution.total_timeout_seconds = 120
    cfg.cases = [CaseConfig(f"case-{index}", f"fixtures/case-{index}") for index in range(40)]
    example = trials(2_000_000_000, 1)[:2]
    evidence = [replace(trial, case_id=case.id, pair=0, warmup=False) for case in cfg.cases for trial in example]
    summary = summarize(cfg, evidence)
    assert summary.reason == "pass"
    assert summary.suite_speedup == pytest.approx(2_000_000_000)


def test_input_rejects_empty_and_oversized_sources():
    from contract import EvaluateInput
    from verifier import verify_input

    reference = ProblemReference("bundled", "group-counts")
    assert not verify_input("evaluate_code", EvaluateInput(reference, CandidateProject([], [], [])))["valid"]
    oversized = CandidateProject(
        [ProjectFile("main.py", "x" * 1048577)], [], ["python3", "main.py", "{input_dir}", "{output_path}"]
    )
    assert not verify_input("evaluate_code", EvaluateInput(reference, oversized))["valid"]


def test_preparation_evidence_binds_argv_and_logs(tmp_path):
    root, output, project, report_path = make_valid_assessment(tmp_path)
    assert validate_evaluation(output, root, project)[0]
    step = output.preparation[0]
    log_path = root / step.stdout.path
    log_path.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        validate_evaluation(output, root, project)
    log_path.write_bytes(b"")
    command_path = root / step.command.path
    wrong = b'["c++","--help"]'
    command_path.write_bytes(wrong)
    step.command.sha256 = sha256(wrong).hexdigest()
    step.command.size = len(wrong)
    write_report(output, report_path)
    assert not validate_evaluation(output, root, project)[0]
