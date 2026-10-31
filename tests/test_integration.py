"""Exercise measured WASI assessments and their deterministic verifier."""

import asyncio
import json
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from contract import CandidateProject, DescribeInput, EvaluateInput, ProblemReference, ProjectFile
from plugin.benchmark import BaselineError, describe_problem, evaluate_code
from plugin.execution import ExecutionEnvironmentError
from verifier import verify_output, verify_task


def cpp_project(source):
    return CandidateProject(
        [ProjectFile("main.cpp", source)],
        [["python3", "-m", "ziglang", "c++", "-target", "wasm32-wasi", "-fno-exceptions", "-O2", "-std=c++17", "main.cpp", "-o", "main.wasm"]],
        ["./main.wasm", "{input_dir}", "{output_path}"],
    )


def wat_project(wat):
    compiler = 'from pathlib import Path; from wasmtime import wat2wasm; Path("main.wasm").write_bytes(wat2wasm(Path("main.wat").read_text()))'
    return CandidateProject(
        [ProjectFile("main.wat", wat)],
        [["python3", "-c", compiler]],
        ["./main.wasm", "{input_dir}", "{output_path}"],
    )


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    root = tmp_path / "problem"
    shutil.copytree(Path(__file__).parents[1] / "src/plugin/problems/group-counts", root)
    cfg = yaml.safe_load((root / "problem.yaml").read_text())
    cfg["execution"]["per_trial_timeout_seconds"] = 4.0
    cfg["measurement"]["warmup_pairs"] = 0
    cfg["measurement"]["measured_pairs"] = 1
    for case in cfg["cases"]:
        (root / case["input_dir"] / "data.txt").write_text("2 1 2 3 1 2")
    (root / "problem.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    return tmp_path, root, ProblemReference("workspace", "problem")


def test_describe_and_exact_baseline_evaluation(bundle):
    workspace, root, reference = bundle
    described = asyncio.run(describe_problem(DescribeInput(reference), workspace))
    assert len(described.case_summaries) == 2
    arguments = EvaluateInput(reference, cpp_project((root / "baseline.cpp").read_text()))
    output = asyncio.run(evaluate_code(arguments, workspace))
    assert len(output.trials) == 4
    assert all(case.outputs_match for case in output.summary.cases)
    assert verify_output("evaluate_code", arguments, output, str(workspace))["valid"]
    assert json.loads((workspace / output.report_path).read_text()) == asdict(output)
    assert all((workspace / trial.stdout.path).is_file() for trial in output.trials)
    changed = EvaluateInput(ProblemReference("workspace", "another"), arguments.project)
    assert not verify_output("evaluate_code", changed, output, str(workspace))["valid"]


@pytest.mark.parametrize("wat", [
    '(module (func (export "_start") unreachable))',
    '(module (func (export "_start")))',
])
def test_failed_wasi_candidate_is_a_valid_report(bundle, wat):
    workspace, _, reference = bundle
    arguments = EvaluateInput(reference, wat_project(wat))
    output = asyncio.run(evaluate_code(arguments, workspace))
    assert output.summary.reason == "execution_failure"
    assert verify_output("evaluate_code", arguments, output, str(workspace))["valid"]
    verdict = verify_task("evaluate-code", arguments, output, None, str(workspace))
    assert not verdict["valid"] and verdict["score"] == 0


def test_wrong_output_is_a_valid_report(bundle):
    workspace, _, reference = bundle
    source = '#include <fstream>\nint main(int, char** v) { std::ofstream(v[2]) << "wrong"; }\n'
    arguments = EvaluateInput(reference, cpp_project(source))
    output = asyncio.run(evaluate_code(arguments, workspace))
    assert output.summary.reason == "wrong_output"
    assert verify_output("evaluate_code", arguments, output, str(workspace))["valid"]
    assert not verify_task("evaluate-code", arguments, output, None, str(workspace))["valid"]


def test_baseline_failure_raises(bundle):
    workspace, root, reference = bundle
    (root / "baseline.cpp").write_text("int main() { return 1; }")
    with pytest.raises(BaselineError, match="baseline failed"):
        asyncio.run(evaluate_code(EvaluateInput(reference, wat_project('(module (func (export "_start")))')), workspace))


def test_compile_failure_then_corrected_executable_is_benchmarked(bundle):
    workspace, root, reference = bundle
    source = (root / "baseline.cpp").read_text()
    with pytest.raises(ExecutionEnvironmentError, match="project preparation failed"):
        asyncio.run(evaluate_code(EvaluateInput(reference, cpp_project('#include <nonexistent_wasi_header.h>\n' + source)), workspace))
    arguments = EvaluateInput(reference, cpp_project(source))
    output = asyncio.run(evaluate_code(arguments, workspace))
    assert all(case.outputs_match for case in output.summary.cases)
    assert all(trial.elapsed_ns > 0 for trial in output.trials)
    assert verify_output("evaluate_code", arguments, output, str(workspace))["valid"]
