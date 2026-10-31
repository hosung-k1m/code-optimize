"""Exercise real guarded multi-case evaluation without asserting fragile timing wins."""

import asyncio
import json
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from contract import CandidateProject, DescribeInput, EvaluateInput, ProblemReference, ProjectFile
from plugin.benchmark import BaselineError, describe_problem, evaluate_code
from verifier import verify_output, verify_task


def cpp_project(source):
    return CandidateProject(
        [ProjectFile("main.cpp", source)],
        [["c++", "-std=c++17", "-O2", "main.cpp", "-o", "main"]],
        ["./main", "{input_dir}", "{output_path}"],
    )


def python_project(source):
    return CandidateProject(
        [ProjectFile("main.py", source + "\nimport sys\nsolve(sys.argv[1], sys.argv[2])\n")],
        [],
        ["python3", "main.py", "{input_dir}", "{output_path}"],
    )


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "problem"
    shutil.copytree(Path(__file__).parents[1] / "src/plugin/problems/group-counts", root)
    cfg = yaml.safe_load((root / "problem.yaml").read_text())
    cfg["execution"]["per_trial_timeout_seconds"] = 4.0
    cfg["measurement"]["warmup_pairs"] = 0
    cfg["measurement"]["measured_pairs"] = 1
    # Tiny fixtures make tests fast; tests assert correctness, not benchmark discrimination.
    for case in cfg["cases"]:
        (root / case["input_dir"] / "data.txt").write_text("2 1 2 3 1 2")
    (root / "problem.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    return tmp_path, root, ProblemReference("workspace", "problem")


def test_describe_and_exact_baseline_evaluation(bundle):
    workspace, root, reference = bundle
    described = asyncio.run(describe_problem(DescribeInput(reference), workspace))
    assert len(described.case_summaries) == 2
    source = cpp_project((root / "baseline.cpp").read_text())
    arguments = EvaluateInput(reference, source)
    output = asyncio.run(evaluate_code(arguments, workspace))
    assert len(output.trials) == 4
    assert all(case.outputs_match for case in output.summary.cases)
    assert verify_output("evaluate_code", arguments, output, str(workspace))["valid"]
    assert json.loads((workspace / output.report_path).read_text()) == asdict(output)
    assert all((workspace / trial.stdout.path).is_file() for trial in output.trials)
    changed = EvaluateInput(ProblemReference("workspace", "another"), source)
    assert not verify_output("evaluate_code", changed, output, str(workspace))["valid"]


@pytest.mark.parametrize(
    "source,reason",
    [
        ("from pathlib import Path\ndef solve(i,o): Path(o).write_text('wrong')", "wrong_output"),
        ("def solve(i,o): raise RuntimeError('candidate failed')", "execution_failure"),
        ("def solve(i,o): pass", "execution_failure"),
        ("def solve(:", "execution_failure"),
        ("from pathlib import Path\ndef solve(i,o): Path(o).write_bytes(b'x'*1100000)", "execution_failure"),
        (
            "from pathlib import Path\ndef solve(i,o):\n (Path(i)/'data.txt').write_text('changed')\n Path(o).write_text('wrong')",
            "execution_failure",
        ),
    ],
)
def test_failed_candidate_is_a_valid_report(bundle, source, reason):
    workspace, _, reference = bundle
    arguments = EvaluateInput(reference, python_project(source))
    output = asyncio.run(evaluate_code(arguments, workspace))
    assert output.summary.reason == reason
    if "1100000" in source:
        assert all(
            t.observed_output_bytes > 1048576 and t.result is None
            for t in output.trials
            if t.implementation == "candidate"
        )
    assert verify_output("evaluate_code", arguments, output, str(workspace))["valid"]
    verdict = verify_task("evaluate-code", arguments, output, None, str(workspace))
    assert not verdict["valid"] and verdict["score"] == 0


def test_baseline_failure_raises(bundle):
    workspace, root, reference = bundle
    (root / "baseline.cpp").write_text("int main() { return 1; }")
    with pytest.raises(BaselineError, match="baseline failed"):
        asyncio.run(evaluate_code(EvaluateInput(reference, python_project("def solve(i,o): pass")), workspace))
