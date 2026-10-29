import os
import runpy
import shutil
from collections import Counter
from pathlib import Path

import pytest

from contract import ProblemReference
from plugin.bundles import load_problem

PROBLEMS = Path(__file__).parents[1] / "src" / "plugin" / "problems"


def _load_solve(path: Path):
    return runpy.run_path(str(path))["solve"]


@pytest.mark.parametrize("problem", ["group-counts", "set-intersection"])
def test_bundled_baseline_matches_independent_golden_outputs(problem, tmp_path):
    bundle = load_problem(ProblemReference(origin="bundled", directory=problem), tmp_path)
    solve = _load_solve(PROBLEMS / problem / "baseline.py")
    for case in bundle.config.cases:
        fixture_dir = tmp_path / case.id
        fixture_dir.mkdir()
        for relative, content in bundle.inputs[case.id].items():
            destination = fixture_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        output_path = tmp_path / f"{case.id}.result"
        solve(fixture_dir, output_path)
        if problem == "group-counts":
            values = [int(value) for value in (fixture_dir / "data.txt").read_text().split()]
            expected = repr(sorted(Counter(values).items()))
        else:
            left = {int(value) for value in (fixture_dir / "left.txt").read_text().split()}
            right = set(map(int, (fixture_dir / "right.txt").read_text().split()))
            expected = repr(sorted(left & right))
        assert output_path.read_bytes() == expected.encode("utf-8")


def _workspace_problem(tmp_path: Path, name: str = "group-counts") -> Path:
    root = tmp_path / "workspace" / "bundle"
    shutil.copytree(PROBLEMS / name, root)
    return root


def test_workspace_bundle_rejects_symlink_fixture_file(tmp_path):
    root = _workspace_problem(tmp_path)
    fixture = root / "fixtures" / "uniform" / "data.txt"
    original = fixture.read_bytes()
    fixture.unlink()
    target = tmp_path / "outside.txt"
    target.write_bytes(original)
    fixture.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        load_problem(ProblemReference(origin="workspace", directory="bundle"), root.parent)


def test_workspace_bundle_rejects_symlink_ancestor(tmp_path):
    root = _workspace_problem(tmp_path)
    fixture_directory = root / "fixtures" / "uniform"
    moved = root / "uniform-data"
    fixture_directory.rename(moved)
    fixture_directory.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        load_problem(ProblemReference(origin="workspace", directory="bundle"), root.parent)


def test_workspace_bundle_rejects_special_file(tmp_path):
    root = _workspace_problem(tmp_path)
    os.mkfifo(root / "fixtures" / "uniform" / "pipe")
    with pytest.raises(ValueError, match="special"):
        load_problem(ProblemReference(origin="workspace", directory="bundle"), root.parent)


def test_workspace_bundle_rejects_fixture_path_overlap(tmp_path):
    root = _workspace_problem(tmp_path)
    config_path = root / "problem.yaml"
    config_path.write_text(config_path.read_text().replace("input_dir: fixtures/uniform", "input_dir: prompt.md"))
    with pytest.raises(ValueError, match="overlaps"):
        load_problem(ProblemReference(origin="workspace", directory="bundle"), root.parent)


def test_workspace_bundle_rejects_oversized_baseline(tmp_path):
    root = _workspace_problem(tmp_path)
    (root / "baseline.py").write_bytes(b"#" * 262145)
    with pytest.raises(ValueError, match="max_source_bytes"):
        load_problem(ProblemReference(origin="workspace", directory="bundle"), root.parent)
