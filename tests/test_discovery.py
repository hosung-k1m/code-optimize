import shutil
from pathlib import Path

import pytest

from contract import ListProblemsInput, ProblemReference, Tool
from plugin.bundles import discover_problems, load_problem
from verifier import verify_input, verify_output

PROBLEMS = Path(__file__).parents[1] / "src" / "plugin" / "problems"


def test_discovery_returns_explicit_bundled_and_workspace_references(tmp_path):
    shutil.copytree(PROBLEMS / "group-counts", tmp_path / "my-problems" / "custom")
    output = discover_problems(tmp_path)
    entries = {(item.reference.origin, item.reference.directory): item.description for item in output.problems}
    assert ("bundled", "group-counts") in entries
    assert ("workspace", "my-problems/custom") in entries
    assert "Group counts" in entries[("workspace", "my-problems/custom")]
    value = ListProblemsInput()
    assert verify_input(Tool.LIST_PROBLEMS, value)["valid"]
    assert verify_output(Tool.LIST_PROBLEMS, value, output, tmp_path)["valid"]
    output.problems.append(output.problems[0])
    assert not verify_output(Tool.LIST_PROBLEMS, value, output, tmp_path)["valid"]


def test_discovery_skips_assessment_snapshots_only_at_workspace_root(tmp_path):
    shutil.copytree(PROBLEMS / "group-counts", tmp_path / "assessments" / "trial" / "snapshot")
    incomplete = tmp_path / "assessments" / "incomplete" / "snapshot"
    incomplete.mkdir(parents=True)
    (incomplete / "problem.yaml").write_text("unfinished")
    shutil.copytree(PROBLEMS / "group-counts", tmp_path / "other" / "assessments" / "problem")

    entries = {(item.reference.origin, item.reference.directory) for item in discover_problems(tmp_path).problems}
    assert ("workspace", "other/assessments/problem") in entries
    assert all(not directory.startswith("assessments/") for origin, directory in entries if origin == "workspace")


def test_missing_origin_does_not_fall_back_and_suggests_discovery(tmp_path):
    with pytest.raises(ValueError, match="list_problems"):
        load_problem(ProblemReference("workspace", "group-counts"), tmp_path)


@pytest.mark.parametrize("directory", ["../group-counts", "/group-counts", "x/../group-counts", "x//y"])
def test_traversal_is_rejected(tmp_path, directory):
    with pytest.raises(ValueError, match="invalid bundle path"):
        load_problem(ProblemReference("bundled", directory), tmp_path)


def test_symlinked_bundle_is_not_discovered_or_loadable(tmp_path):
    (tmp_path / "linked").symlink_to(PROBLEMS / "group-counts", target_is_directory=True)
    assert all(item.reference.directory != "linked" for item in discover_problems(tmp_path).problems)
    with pytest.raises(ValueError, match="selected origin"):
        load_problem(ProblemReference("workspace", "linked"), tmp_path)


def test_mcp_tools_have_descriptions():
    from contract import TOOLS
    from plugin.__main__ import DESCRIPTIONS

    assert set(DESCRIPTIONS) == set(TOOLS)
    assert all(description.strip() for description in DESCRIPTIONS.values())
