from pathlib import Path

import pytest

from plugin.config import load_problem_config

PROBLEMS = Path(__file__).parents[1] / "src" / "plugin" / "problems"


@pytest.mark.parametrize("name,case_ids", [("group-counts", ["uniform", "skewed"]), ("set-intersection", ["balanced", "overlap"]), ("repeated-shortest-paths", ["mesh", "disconnected"])])
def test_problem_yaml_loads_as_typed_contract(name, case_ids):
    config = load_problem_config(PROBLEMS / name / "problem.yaml")
    assert config.id == name
    assert [case.id for case in config.cases] == case_ids
    assert config.execution.runtime == "wasi_project_v1"
    assert config.execution.wasi_memory_bytes == 2147483648
    assert config.execution.wasi_fuel == 30000000000


def test_unknown_yaml_field_is_rejected(tmp_path):
    source = (PROBLEMS / "group-counts" / "problem.yaml").read_text()
    path = tmp_path / "problem.yaml"
    path.write_text(source + "unexpected_setting: true\n")
    with pytest.raises(ValueError, match="does not declare|unknown|key"):
        load_problem_config(path)


def test_unsupported_runtime_is_rejected(tmp_path):
    source = (PROBLEMS / "group-counts" / "problem.yaml").read_text().replace("runtime: wasi_project_v1", "runtime: python_system")
    path = tmp_path / "problem.yaml"
    path.write_text(source)
    with pytest.raises(ValueError, match="unsupported"):
        load_problem_config(path)


def test_schedule_that_exceeds_deadline_is_rejected(tmp_path):
    source = (PROBLEMS / "group-counts" / "problem.yaml").read_text().replace("total_timeout_seconds: 60.0", "total_timeout_seconds: 10.0")
    path = tmp_path / "problem.yaml"
    path.write_text(source)
    with pytest.raises(ValueError, match="schedule"):
        load_problem_config(path)


@pytest.mark.parametrize("problem_id", ["../escape", "has space", "", "x" * 65])
def test_unsafe_problem_id_is_rejected(tmp_path, problem_id):
    source = (PROBLEMS / "group-counts" / "problem.yaml").read_text().replace("id: group-counts", f"id: {problem_id!r}")
    path = tmp_path / "problem.yaml"
    path.write_text(source)
    with pytest.raises(ValueError, match="id"):
        load_problem_config(path)


@pytest.mark.parametrize("case_id", ["../escape", "has space", "", "x" * 65])
def test_unsafe_case_id_is_rejected(tmp_path, case_id):
    source = (PROBLEMS / "group-counts" / "problem.yaml").read_text().replace(
        "id: uniform", f"id: {case_id!r}"
    )
    path = tmp_path / "problem.yaml"
    path.write_text(source)
    with pytest.raises(ValueError, match="case id"):
        load_problem_config(path)


def test_nonfinite_policy_is_rejected(tmp_path):
    source = (PROBLEMS / "group-counts" / "problem.yaml").read_text().replace(
        "minimum_speedup: 1.10", "minimum_speedup: .nan"
    )
    path = tmp_path / "problem.yaml"
    path.write_text(source)
    with pytest.raises(ValueError, match="non-finite"):
        load_problem_config(path)


def test_fixture_traversal_is_rejected(tmp_path):
    source = (PROBLEMS / "group-counts" / "problem.yaml").read_text().replace(
        "input_dir: fixtures/uniform", "input_dir: ../uniform"
    )
    path = tmp_path / "problem.yaml"
    path.write_text(source)
    with pytest.raises(ValueError, match="input path"):
        load_problem_config(path)


@pytest.mark.parametrize(
    "payload",
    [
        "_default: secret.yaml\n",
        'prompt: "${oc.env:PRIVATE_VALUE}"\n',
        'prompt: "${from_yaml:../../outside.yaml}"\n',
    ],
)
def test_problem_yaml_rejects_defaults_and_interpolations(tmp_path, payload):
    source = (PROBLEMS / "group-counts" / "problem.yaml").read_text()
    path = tmp_path / "problem.yaml"
    path.write_text(source + payload)
    with pytest.raises(ValueError, match="_default|interpolation"):
        load_problem_config(path)


def test_problem_yaml_rejects_wrong_root_schema(tmp_path):
    source = (PROBLEMS / "group-counts" / "problem.yaml").read_text().replace(
        "_ > contract.ProblemConfig:", "_ > contract.Policy:"
    )
    path = tmp_path / "problem.yaml"
    path.write_text(source)
    with pytest.raises(ValueError, match="root schema"):
        load_problem_config(path)


def test_problem_yaml_rejects_wrong_nested_schema(tmp_path):
    source = (PROBLEMS / "group-counts" / "problem.yaml").read_text().replace(
        "execution:\n", "execution:\n  _ > contract.Policy:\n"
    )
    path = tmp_path / "problem.yaml"
    path.write_text(source)
    with pytest.raises(ValueError, match="names the file's own class"):
        load_problem_config(path)


@pytest.mark.parametrize(
    "old,new,reason",
    [
        ("total_timeout_seconds: 60.0", "total_timeout_seconds: 121.0", "supported assessment limits"),
        ("max_source_bytes: 262144", "max_source_bytes: 1048577", "supported assessment limits"),
        ("max_output_bytes: 1048576", "max_output_bytes: 4194305", "supported assessment limits"),
        ("max_log_bytes: 65536", "max_log_bytes: 1048577", "supported assessment limits"),
        ("per_trial_timeout_seconds: 2.0", "per_trial_timeout_seconds: 0.5", "invalid limits"),
        ("wasi_memory_bytes: 2147483648", "wasi_memory_bytes: 1073741824", "invalid limits"),
        ("wasi_fuel: 30000000000", "wasi_fuel: 3000000000", "invalid limits"),
    ],
)
def test_unsupported_resource_limits_are_rejected(tmp_path, old, new, reason):
    source = (PROBLEMS / "group-counts" / "problem.yaml").read_text().replace(old, new)
    path = tmp_path / "problem.yaml"
    path.write_text(source)
    with pytest.raises(ValueError, match=reason):
        load_problem_config(path)


def test_declared_default_cannot_escape_snapshot(tmp_path):
    source = (PROBLEMS / "group-counts" / "problem.yaml").read_text()
    path = tmp_path / "problem.yaml"
    path.write_text(source + "_default > contract.ProblemConfig: /outside.yaml\n")
    with pytest.raises(ValueError, match="_default"):
        load_problem_config(path)
