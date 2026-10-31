# Code Optimize

A `nanoagent.plugin/v0001` plugin that checks Python optimizations against a
problem bundle's baseline on the current computer. It follows NanoAgent's
reference plugin structure, [arxiv-source](https://github.com/zeyuyang8/arxiv-source).
Correctness, timing calculations and acceptance are decided by deterministic code.

## Tools

| Tool | Does | Code |
|---|---|---|
| `describe_problem` | Reads a problem's prompt, baseline, cases, interface and benchmark policy | `src/plugin/benchmark.py` |
| `evaluate_code` | Runs baseline/candidate pairs and returns exact output comparisons, timing evidence and artifacts | `src/plugin/benchmark.py` |

### Tasks

| Task | Does | Runner |
|---|---|---|
| `describe-problem` | Describes a bundled or workspace optimization problem | `tool`, with `describe_problem` |
| `evaluate-code` | Checks a candidate implementation and measures its speedup | `tool`, with `evaluate_code` |

Both tasks declare `reference: null`: evaluation runs the baseline alongside the
candidate in the same assessment. A failed candidate returns a checked report
with a failed task verdict. Invalid bundles, broken baselines and execution
environment failures raise tool errors.

### Problems and program interface

The bundled problems are `group-counts` and `set-intersection`. A problem reference
explicitly selects its origin and a directory relative to that origin:

```yaml
origin: bundled
directory: group-counts
```

For a staged workspace bundle, use `origin: workspace` and its directory relative
to the workspace. Each bundle supplies `problem.yaml`, `prompt.md`, `baseline.py`
and fixture directories. See the bundled
[group-counts configuration](src/plugin/problems/group-counts/problem.yaml) and
[intersection configuration](src/plugin/problems/set-intersection/problem.yaml).

Baseline and candidate programs define:

```python
def solve(input_dir: str, output_path: str) -> None:
    ...
```

Read the declared input files and write one result file. Its bytes must match the
baseline exactly, including ordering, whitespace and final newlines. Ask the plugin
to describe the problem before writing a candidate, then evaluate and revise the
source using the returned report.

## Layout

```text
.
├── nanoagent.yaml   # manifest: identity, runtime, permissions, tools and tasks
├── contract.py      # typed tool/task inputs, outputs and report evidence
├── README.md
├── LICENSE
├── pyproject.toml
├── uv.lock
├── .gitignore
├── .env             # no environment variables are required
├── src/plugin/      # MCP server, bundle loading, benchmark and process supervision
│   └── problems/    # problem YAML, prompts, baselines and fixtures
├── tests/           # verifier, configuration, execution and integration checks
├── ui/              # output renderers using the host's page kit
├── product/         # catalog description and icon
└── verifier/        # deterministic artifact, scoring and policy checks
```

`contract.py` is the one place the tool and task shapes are declared. Its slimconfig
`Output` classes define the JSON Schemas used by the server, NanoAgent and verifier.
The tools return typed values directly; no model supplies benchmark measurements
or decides a verdict.

Each assessment saves its source, configuration and fixture snapshots under
`assessments/<id>/`, together with bounded logs, accepted results and `report.json`.
The report records hashes, runtime identity, raw trials and the computed summary.
To add another problem, supply a new bundle and baseline tests; the evaluator and
verifier stay the same.

## Develop and run

From the plugin root:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv lock --check
```

After changing dependencies, run `uv lock` and include `uv.lock` with the change.
The plugin uses the installed Python environment, an empty runtime network-host
list and workspace-only writes. It installs no dependencies during a trial.

The installed-package harness, renderer preview and release checks are documented
in [tests/RELEASE.md](tests/RELEASE.md). Run that harness with RUSH Lab's installed
Python to check actual MCP calls and the macOS desktop sandbox.

Version 0.1.1 is `ready` for use in RUSH Lab and supports macOS execution. Windows and Linux
execution fail explicitly. It evaluates cooperative Python submissions within the
existing desktop sandbox; fixtures are public. It enforces execution deadlines,
accepted-result sizes and retained-log limits. CPU/RAM quotas, hard disk-write
quotas and hostile-code isolation are not provided.

RUSH Lab accepts a public GitHub repository. Publish a new manifest version for
each update; published versions cannot be replaced. Keep `status: ready` to make
the plugin available for installation and task execution. The owner accepts any
submission terms themselves.
