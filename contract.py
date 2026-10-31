"""Typed tool and task declarations for code-optimize."""

from dataclasses import dataclass
from enum import Enum
from typing import Annotated

from slimconfig import Output


class Tool(Enum):
    LIST_PROBLEMS = "list_problems"
    DESCRIBE_PROBLEM = "describe_problem"
    EVALUATE_CODE = "evaluate_code"


class Task(Enum):
    LIST_PROBLEMS = "list-problems"
    DESCRIBE_PROBLEM = "describe-problem"
    EVALUATE_CODE = "evaluate-code"


@dataclass
class ProblemReference(Output):
    origin: Annotated[str, "Explicitly select bundled or workspace problem files"]
    directory: Annotated[str, "Problem directory relative to the selected origin"]


@dataclass
class ListProblemsInput(Output):
    pass


@dataclass
class ProblemListing(Output):
    reference: Annotated[ProblemReference, "Exact reference for describing or evaluating this bundle"]
    description: Annotated[str, "Problem description"]


@dataclass
class ListProblemsOutput(Output):
    problems: Annotated[list[ProblemListing], "Available bundled and workspace problem bundles"]


@dataclass
class DescribeInput(Output):
    problem: Annotated[ProblemReference, "Problem bundle to describe"]


@dataclass
class ProjectFile(Output):
    path: Annotated[str, "Project-relative file path"]
    content: Annotated[str, "UTF-8 file contents"]


@dataclass
class CandidateProject(Output):
    files: Annotated[list[ProjectFile], "Candidate project files"]
    prepare: Annotated[list[list[str]], "Preparation commands as argument arrays"]
    run: Annotated[list[str], "Run command with {input_dir} and {output_path} arguments"]


@dataclass
class EvaluateInput(Output):
    problem: Annotated[ProblemReference, "Problem bundle to evaluate"]
    project: Annotated[CandidateProject, "Candidate source files and explicit preparation and run commands"]


@dataclass
class Artifact(Output):
    path: Annotated[str, "Workspace-relative artifact path"]
    sha256: Annotated[str, "SHA-256 digest of artifact bytes"]
    size: Annotated[int, "Artifact size in bytes"]


@dataclass
class CaseConfig(Output):
    id: Annotated[str, "Unique case identifier"]
    input_dir: Annotated[str, "Fixture directory relative to the problem bundle"]


@dataclass
class ExecutionSettings(Output):
    runtime: Annotated[str, "Required runtime; supports project_v1"]
    interface: Annotated[str, "Required program interface; supports executable_directory_v1"]
    per_trial_timeout_seconds: Annotated[float, "Maximum guarded execution duration"]
    total_timeout_seconds: Annotated[float, "Maximum complete assessment duration"]
    max_source_bytes: Annotated[int, "Maximum candidate source size"]
    max_output_bytes: Annotated[int, "Maximum accepted result file size"]
    max_log_bytes: Annotated[int, "Maximum retained combined stdout and stderr bytes"]


@dataclass
class MeasurementSettings(Output):
    metric: Annotated[str, "Measurement metric; v1 supports wall_time"]
    warmup_pairs: Annotated[int, "Warm-up baseline/candidate pairs per case"]
    measured_pairs: Annotated[int, "Measured baseline/candidate pairs per case"]
    order: Annotated[str, "Execution ordering policy; v1 supports alternating"]


@dataclass
class Policy(Output):
    minimum_speedup: Annotated[float, "Minimum geometric suite speedup"]
    maximum_case_slowdown: Annotated[float, "Largest permitted per-case regression ratio"]
    maximum_relative_mad: Annotated[float, "Largest permitted relative median absolute deviation"]


@dataclass
class ProblemConfig(Output):
    schema_version: Annotated[int, "Problem schema version"]
    id: Annotated[str, "Problem identifier"]
    prompt: Annotated[str, "Prompt file path relative to bundle"]
    baseline: Annotated[str, "Baseline source file path relative to bundle"]
    execution: Annotated[ExecutionSettings, "Execution limits and interface"]
    measurement: Annotated[MeasurementSettings, "Measurement schedule"]
    policy: Annotated[Policy, "Acceptance thresholds"]
    cases: Annotated[list[CaseConfig], "Ordered cases in the benchmark"]


@dataclass
class RuntimeIdentity(Output):
    plugin_version: Annotated[str, "Installed plugin version"]
    python_implementation: Annotated[str, "Python implementation"]
    python_version: Annotated[str, "Python version"]
    platform: Annotated[str, "Operating system platform"]
    architecture: Annotated[str, "Machine architecture"]
    executable: Annotated[str, "Absolute interpreter path"]
    lock_sha256: Annotated[str, "Dependency lock file SHA-256"]
    clock: Annotated[str, "Elapsed-time clock name"]
    resolution_ns: Annotated[int, "Elapsed-time clock resolution in nanoseconds"]
    tools: Annotated[list[str], "Resolved available tool executables"]


BASELINE_PREPARE = [["c++", "--version"], ["c++", "-std=c++17", "-O2", "baseline.cpp", "-o", "baseline"]]


@dataclass
class Preparation(Output):
    implementation: Annotated[str, "baseline or candidate"]
    command: Annotated[Artifact, "Canonical argv JSON artifact"]
    stdout: Annotated[Artifact, "Bounded captured standard output"]
    stderr: Annotated[Artifact, "Bounded captured standard error"]


@dataclass
class Trial(Output):
    case_id: Annotated[str, "Case identifier"]
    implementation: Annotated[str, "baseline or candidate"]
    pair: Annotated[int, "Pair index, starting at zero"]
    warmup: Annotated[bool, "Whether this trial is excluded from scoring"]
    elapsed_ns: Annotated[int, "Parent-measured guarded elapsed time"]
    exit_code: Annotated[int | None, "Trusted process exit status, when available"]
    status: Annotated[str, "Execution outcome classification"]
    result: Annotated[Artifact | None, "Result artifact metadata, when produced"]
    stdout: Annotated[Artifact, "Bounded captured stdout artifact"]
    stderr: Annotated[Artifact, "Bounded captured stderr artifact"]
    inputs_unchanged: Annotated[bool, "Whether fixture inputs match their snapshot"]
    cleanup_confirmed: Annotated[bool, "Whether worker cleanup was confirmed"]
    observed_output_bytes: Annotated[int | None, "Observed regular result size, including rejected oversized results"]


@dataclass
class CaseSummary(Output):
    case_id: Annotated[str, "Case identifier"]
    speedup: Annotated[float, "Median paired baseline/candidate speedup"]
    baseline_relative_mad: Annotated[float, "Baseline timing relative MAD"]
    candidate_relative_mad: Annotated[float, "Candidate timing relative MAD"]
    outputs_match: Annotated[bool, "Whether all outputs matched baseline bytes"]


@dataclass
class Summary(Output):
    reason: Annotated[str, "Pass or failure classification"]
    eligible: Annotated[bool, "Whether all correctness and stability gates passed"]
    suite_speedup: Annotated[float, "Geometric mean of case speedups"]
    cases: Annotated[list[CaseSummary], "Per-case deterministic measurements"]


@dataclass
class DescribeOutput(Output):
    prompt: Annotated[str, "Problem prompt text"]
    baseline_source: Annotated[str, "Naive baseline source code"]
    problem_config: Annotated[ProblemConfig, "Validated resolved problem configuration"]
    runtime: Annotated[RuntimeIdentity, "Current runtime identity"]
    case_summaries: Annotated[list[str], "Case identifiers and fixture summaries"]


@dataclass
class EvaluateOutput(Output):
    schema_version: Annotated[int, "Assessment report schema version"]
    assessment_id: Annotated[str, "Unique assessment identifier"]
    problem: Annotated[ProblemReference, "Explicit problem bundle reference"]
    problem_config: Annotated[ProblemConfig, "Validated resolved problem configuration"]
    fingerprint: Annotated[str, "Canonical assessment input fingerprint"]
    candidate: Annotated[Artifact, "Submitted candidate source artifact"]
    baseline: Annotated[Artifact, "Snapshotted baseline source artifact"]
    configuration: Annotated[Artifact, "Snapshotted YAML configuration artifact"]
    prompt: Annotated[Artifact, "Snapshotted prompt artifact"]
    inputs: Annotated[list[Artifact], "Snapshotted fixture files"]
    runtime: Annotated[RuntimeIdentity, "Runtime identity used for the assessment"]
    preparation: Annotated[list[Preparation], "Ordered preparation command and log evidence"]
    trials: Annotated[list[Trial], "Ordered raw baseline and candidate trial evidence"]
    summary: Annotated[Summary, "Deterministic result summary"]
    report_path: Annotated[str, "Workspace-relative report artifact path"]


TOOLS = {
    Tool.LIST_PROBLEMS: (ListProblemsInput, ListProblemsOutput),
    Tool.DESCRIBE_PROBLEM: (DescribeInput, DescribeOutput),
    Tool.EVALUATE_CODE: (EvaluateInput, EvaluateOutput),
}
TASKS = {
    Task.LIST_PROBLEMS: (ListProblemsInput, ListProblemsOutput),
    Task.DESCRIBE_PROBLEM: (DescribeInput, DescribeOutput),
    Task.EVALUATE_CODE: (EvaluateInput, EvaluateOutput),
}
