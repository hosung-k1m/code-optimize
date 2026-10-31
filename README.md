# Code Optimize

Optimize repeated shortest-path queries and measure your solution against a naive
C++17 baseline on the current machine. The baseline runs Dijkstra for every query;
candidates can reuse work, preprocess, and use any language with an available
toolchain. Correctness and acceptance are decided by deterministic code.

## Tools

| Tool | Does | Code |
|---|---|---|
| `list_problems` | Discovers bundled and workspace problems with exact references | `src/plugin/bundles.py` |
| `describe_problem` | Shows the statement, C++ baseline, cases, tools and current policy | `src/plugin/benchmark.py` |
| `evaluate_code` | Prepares a candidate project, compares exact bytes and measures speedup | `src/plugin/benchmark.py` |

### Tasks

| Task | Runner | Does |
|---|---|---|
| `list-problems` | `tool`, `list_problems` | Finds available problems without guessing paths |
| `describe-problem` | `tool`, `describe_problem` | Explains the selected challenge and configuration |
| `evaluate-code` | `tool`, `evaluate_code` | Builds and checks a candidate project |

All tasks use `reference: null`; the baseline runs alongside the candidate.

Start a session with:

> List the problems, describe bundled repeated-shortest-paths, then build and
> evaluate a faster solution in the language you choose, including its dependencies.

### Problem and current configuration

The default `repeated-shortest-paths` bundle contains weighted undirected graphs,
repeated source queries, disconnected vertices, parallel edges, self-loops,
zero-weight edges and distances requiring 64-bit arithmetic. Programs receive
`INPUT_DIR OUTPUT_PATH`, read `graph.txt`, and write one integer answer per line.
See its [statement](src/plugin/problems/repeated-shortest-paths/prompt.md).

The graph configuration uses two cases (`mesh`, `disconnected`), one warmup and
five measured pairs per case, alternating baseline/candidate order. Acceptance
requires exact output bytes, geometric suite speedup ≥ 1.10, no case slowdown
above 1.05, and relative timing MAD ≤ 0.10. Each guarded trial has 2 seconds and
the entire assessment, including preparation, has 120 seconds. YAML is the source
of truth: `describe_problem` returns the current validated configuration.

The smaller `group-counts` and `set-intersection` bundles remain available, also
with C++ baselines. Always call `list_problems` first and copy a returned reference:

```yaml
origin: bundled
directory: repeated-shortest-paths
```

`workspace` means a real bundle staged in the session workspace. It does not name
the bundled problems directory. Origins never substitute for each other. Absolute
paths, traversal and symlink bundles are rejected.

### Candidate projects and dependencies

`evaluate_code` takes `problem` and a typed `project`. For a C++ candidate:

```yaml
project:
  files:
    - path: main.cpp
      content: <your complete program>
  prepare:
    - [c++, -O2, -std=c++17, main.cpp, -o, solution]
  run: [./solution, '{input_dir}', '{output_path}']
```

For Python, submit a CLI script and `pyproject.toml`/`uv.lock` when dependencies
are needed, prepare with `uv sync --frozen`, and run
`[.venv/bin/python, main.py, '{input_dir}', '{output_path}']`. A standard-library
script can use `python3` with no preparation. All third-party Python packages
come from uv. Other languages use their own explicit argv commands and project
manifests. There is no language allowlist. `describe_problem` reports tools found
inside the runtime; a missing tool or failed dependency installation fails clearly.
The C++ baseline requires a working `c++` C++17 compiler (macOS Command Line Tools).

Preparation executes once, before measurements, with bounded logs and deadlines.
The plugin permits HTTPS through the host proxy to its declared Python, npm,
Cargo, Go and GitHub package hosts; other hosts are denied. Include lockfiles for
reproducibility. Preparation never changes the plugin's own locked environment.
Each trial receives a separate copy of the prepared project and fixture files,
so writable state cannot carry answers from one trial into the next.

### Measurements and evidence

Timing includes fresh guarded process startup, execution and confirmed cleanup;
it excludes compilation and dependency installation. Every output must match the
baseline byte for byte, including order and newlines. Failed correctness,
execution, stability or speed gates return a report with a failing task verdict.
Invalid bundles, failed preparation and broken baselines raise explicit errors.

Assessments retain the submitted project manifest, source/configuration/fixture
snapshots, bounded diagnostics, result files and raw trials under
`assessments/<id>/`. The verifier checks hashes, exact result bytes, trial schedule
and computed policy outcomes. Fixtures are public; this is a cooperative local
optimization benchmark, with no model deciding correctness.

## Layout

```text
.
├── nanoagent.yaml   # identity, runtime, permissions, tools and tasks
├── contract.py      # typed input/output and evidence contracts
├── README.md
├── LICENSE
├── pyproject.toml
├── uv.lock
├── .gitignore
├── .env
├── src/plugin/      # discovery, projects, benchmarking and process supervision
│   └── problems/    # YAML, prompts, C++ baselines and fixtures
├── tests/           # deterministic tests and installed-package release harness
├── ui/              # result renderers using the host page kit
├── product/         # session/catalog description and icon
└── verifier/        # deterministic evidence and acceptance checks
```

## Develop and run

From the plugin root:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv lock --check
```

After dependency changes, run `uv lock` and include `uv.lock`. Version 0.2.0
replaces the old Python `source`/`solve()` API with an explicit `project` API.
Guarded execution supports macOS; Windows and Linux fail explicitly.
See [tests/RELEASE.md](tests/RELEASE.md) for installed desktop checks.
