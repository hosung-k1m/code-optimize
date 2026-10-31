import heapq
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
import wasmtime

from contract import ProblemReference
from plugin.bundles import load_problem

PROBLEMS = Path(__file__).parents[1] / "src" / "plugin" / "problems"


def _compile(source, executable):
    subprocess.run(
        [sys.executable, "-m", "ziglang", "c++", "-target", "wasm32-wasi", "-fno-exceptions", "-std=c++17", "-O2", str(source), "-o", str(executable)],
        check=True,
        cwd=executable.parent,
    )


def _run(executable, inputs, output):
    config = wasmtime.Config()
    config.consume_fuel = True
    engine = wasmtime.Engine(config)
    store = wasmtime.Store(engine)
    store.set_limits(memory_size=2147483648)
    store.set_fuel(30000000000)
    wasi = wasmtime.WasiConfig()
    wasi.argv = ["baseline.wasm", "/input", "/output/" + output.name]
    wasi.preopen_dir(str(inputs), "/input")
    wasi.preopen_dir(str(output.parent), "/output")
    store.set_wasi(wasi)
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    module = wasmtime.Module.from_file(engine, str(executable))
    linker.instantiate(store, module).exports(store)["_start"](store)


@pytest.mark.parametrize("problem", ["group-counts", "set-intersection"])
def test_bundled_baseline_matches_independent_golden_outputs(problem, tmp_path):
    bundle = load_problem(ProblemReference(origin="bundled", directory=problem), tmp_path)
    executable = tmp_path / "baseline.wasm"
    _compile(PROBLEMS / problem / "baseline.cpp", executable)
    for case in bundle.config.cases:
        fixture_dir = tmp_path / case.id
        fixture_dir.mkdir()
        for relative, content in bundle.inputs[case.id].items():
            destination = fixture_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        output_path = tmp_path / f"{case.id}.result"
        _run(executable, fixture_dir, output_path)
        if problem == "group-counts":
            values = [int(value) for value in (fixture_dir / "data.txt").read_text().split()]
            expected = repr(sorted(Counter(values).items()))
        else:
            left = {int(value) for value in (fixture_dir / "left.txt").read_text().split()}
            right = set(map(int, (fixture_dir / "right.txt").read_text().split()))
            expected = repr(sorted(left & right))
        assert output_path.read_bytes() == expected.encode("utf-8")


@pytest.fixture(scope="module")
def shortest_paths_baseline(tmp_path_factory):
    executable = tmp_path_factory.mktemp("shortest-paths") / "baseline.wasm"
    _compile(PROBLEMS / "repeated-shortest-paths" / "baseline.cpp", executable)
    return executable


def _shortest_paths_oracle(graph_path):
    numbers = iter(map(int, graph_path.read_text().split()))
    n, m, q = next(numbers), next(numbers), next(numbers)
    graph = [[] for _ in range(n)]
    for _ in range(m):
        u, v, weight = next(numbers), next(numbers), next(numbers)
        graph[u].append((v, weight))
        graph[v].append((u, weight))
    distances = {}
    answers = []
    for _ in range(q):
        source, target = next(numbers), next(numbers)
        if source not in distances:
            best = {source: 0}
            pending = [(0, source)]
            while pending:
                distance, vertex = heapq.heappop(pending)
                if distance != best[vertex]:
                    continue
                for neighbor, weight in graph[vertex]:
                    candidate = distance + weight
                    if candidate < best.get(neighbor, float("inf")):
                        best[neighbor] = candidate
                        heapq.heappush(pending, (candidate, neighbor))
            distances[source] = best
        answers.append(distances[source].get(target, -1))
    return "".join(f"{answer}\n" for answer in answers).encode()


@pytest.mark.parametrize("case", ["mesh", "disconnected"])
def test_shortest_paths_baseline_matches_independent_oracle(shortest_paths_baseline, tmp_path, case):
    bundle = load_problem(ProblemReference(origin="bundled", directory="repeated-shortest-paths"), tmp_path)
    fixture = tmp_path / case
    fixture.mkdir()
    (fixture / "graph.txt").write_bytes(bundle.inputs[case]["graph.txt"])
    output = tmp_path / "result"
    _run(shortest_paths_baseline, fixture, output)
    assert output.read_bytes() == _shortest_paths_oracle(fixture / "graph.txt")


def test_shortest_paths_edge_cases(shortest_paths_baseline, tmp_path):
    (tmp_path / "graph.txt").write_text("5 5 7\n0 1 9\n0 1 2\n1 2 0\n2 2 7\n3 4 1\n0 2\n2 0\n0 0\n0 4\n3 4\n0 2\n4 3\n")
    output = tmp_path / "result"
    _run(shortest_paths_baseline, tmp_path, output)
    assert output.read_bytes() == b"2\n2\n0\n-1\n1\n2\n1\n"


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
    (root / "baseline.cpp").write_bytes(b"#" * 262145)
    with pytest.raises(ValueError, match="max_source_bytes"):
        load_problem(ProblemReference(origin="workspace", directory="bundle"), root.parent)


@pytest.mark.parametrize("case", ["mesh", "disconnected", "edge"])
def test_graph_cpp_candidate_matches_baseline(case, shortest_paths_baseline, tmp_path):
    fixture = tmp_path / "input"
    fixture.mkdir()
    if case == "edge":
        (fixture / "graph.txt").write_text(
            "7 8 11\n0 1 9007199254740993\n0 1 9007199254740994\n1 2 0\n2 2 7\n3 4 1\n"
            "4 5 9223372036854775806\n5 6 9223372036854775807\n4 6 1\n"
            "0 2\n2 0\n0 0\n0 4\n3 4\n0 2\n4 3\n3 5\n5 6\n6 5\n4 6\n"
        )
    else:
        shutil.copy(PROBLEMS / "repeated-shortest-paths" / "fixtures" / case / "graph.txt", fixture)
    baseline_output = tmp_path / "baseline-result"
    candidate_output = tmp_path / "candidate-result"
    _run(shortest_paths_baseline, fixture, baseline_output)
    candidate = tmp_path / "candidate.wasm"
    _compile(Path(__file__).parent / "candidates" / "graph.cpp", candidate)
    _run(candidate, fixture, candidate_output)
    assert candidate_output.read_bytes() == baseline_output.read_bytes() == _shortest_paths_oracle(fixture / "graph.txt")
