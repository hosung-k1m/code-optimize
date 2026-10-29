"""Deterministic validation and policy verification for code-optimize."""

from pathlib import Path

from contract import DescribeInput, DescribeOutput, EvaluateInput, EvaluateOutput, Task, Tool

from .checks import result, valid_runtime, validate_config_values, validate_evaluation


def verify_input(tool, value):
    name = Tool(tool)
    if (
        name is Tool.DESCRIBE_PROBLEM
        and isinstance(value, DescribeInput)
        or name is Tool.EVALUATE_CODE
        and isinstance(value, EvaluateInput)
    ):
        reference = value.problem
    else:
        return result(False, "tool input does not match the declared tool")
    path = Path(reference.directory)
    if reference.origin not in ("bundled", "workspace"):
        return result(False, "problem origin must be bundled or workspace")
    components = reference.directory.split("/")
    if (
        not reference.directory
        or "\x00" in reference.directory
        or path.is_absolute()
        or not path.parts
        or any(part in ("", ".", "..") for part in components)
        or "\\" in reference.directory
    ):
        return result(False, "problem directory must stay within its selected origin")
    if isinstance(value, EvaluateInput) and (not value.source or len(value.source.encode("utf-8")) > 1024 * 1024):
        return result(False, "candidate source is empty or exceeds the supported source limit")
    return result(True, "tool input is valid")


def verify_output(tool, input_value, output_value, workspace):
    if Tool(tool) is Tool.DESCRIBE_PROBLEM:
        if not isinstance(input_value, DescribeInput) or not isinstance(output_value, DescribeOutput):
            return result(False, "describe-problem output does not match its contract")
        if not output_value.prompt.strip() or not output_value.baseline_source.strip():
            return result(False, "problem prompt and baseline must be nonempty")
        if len(output_value.case_summaries) != len(output_value.problem_config.cases):
            return result(False, "case summaries do not match the problem configuration")
        if any(not isinstance(item, str) or not item.strip() for item in output_value.case_summaries):
            return result(False, "case summaries must be nonempty text")
        supported, message = validate_config_values(output_value.problem_config)
        if not supported:
            return result(False, message)
        if not valid_runtime(output_value.runtime):
            return result(False, "runtime identity is invalid")
        return result(True, "problem description is valid")
    if Tool(tool) is not Tool.EVALUATE_CODE or not isinstance(input_value, EvaluateInput):
        return result(False, "tool input does not match the declared tool")
    if not isinstance(output_value, EvaluateOutput):
        return result(False, "evaluation output is invalid")
    try:
        if output_value.problem != input_value.problem:
            return result(False, "assessment does not match the requested problem reference")
        valid, message = validate_evaluation(output_value, workspace, input_value.source)
    except (OSError, ValueError, StopIteration, TypeError):
        return result(False, "evaluation artifacts or evidence are invalid")
    return result(valid, message)


def verify_task(task, arguments, output, reference, workspace):
    task_name = Task(task)
    if task_name is Task.DESCRIBE_PROBLEM:
        if not isinstance(arguments, DescribeInput) or not isinstance(output, DescribeOutput):
            return result(False, "problem description output is invalid")
        return verify_output(Tool.DESCRIBE_PROBLEM, arguments, output, workspace)
    if task_name is not Task.EVALUATE_CODE or not isinstance(arguments, EvaluateInput):
        return result(False, "task arguments do not match the declared task")
    if not isinstance(output, EvaluateOutput):
        return result(False, "assessment output is invalid")
    try:
        if output.problem != arguments.problem:
            return result(False, "assessment does not match the requested problem reference")
        valid, message = validate_evaluation(output, workspace, arguments.source)
    except (OSError, ValueError, StopIteration, TypeError):
        return result(False, "evaluation artifacts or evidence are invalid")
    if not valid:
        return result(False, message)
    return result(
        output.summary.eligible,
        output.summary.reason,
        speedup=output.summary.suite_speedup,
        cases=output.summary.cases,
        assessment_id=output.assessment_id,
        fingerprint=output.fingerprint,
    )
