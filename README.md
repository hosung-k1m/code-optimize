# Code Optimize

Optimize repeated shortest-path queries against a naive C++ baseline. The plugin
compiles and executes both programs inside embedded WebAssembly/WASI, with fixed
resource limits and a deterministic verifier. It needs no Docker, virtual machine,
external compiler installation, or changes to NanoAgent/RUSH Lab.

## Tools

| Tool | Does | Code |
|---|---|---|
| `list_problems` | Returns exact bundled/workspace problem references | `src/plugin/bundles.py` |
| `describe_problem` | Shows the statement, baseline, fixed sandbox settings and build tools | `src/plugin/benchmark.py` |
| `evaluate_code` | Prepares a WASI project, executes it, and returns correctness/timing evidence | `src/plugin/benchmark.py` |

### Tasks

| Task | Runner | Does |
|---|---|---|
| `list-problems` | `tool`, `list_problems` | Discovers problems without guessing paths |
| `describe-problem` | `tool`, `describe_problem` | Explains the problem and execution configuration |
| `evaluate-code` | `tool`, `evaluate_code` | Builds, runs, measures and verifies a submitted project |

All tasks use `reference: null`; the baseline runs alongside the candidate.
Task descriptions include the complete execution/retry guidance, so they work
with the existing RUSH Lab chat tool interface.

Start with:

> List the problems, describe bundled repeated-shortest-paths and its sandbox
> configuration, then compile and benchmark a faster solution. Fix build errors
> and retry until you have measured results.

### Fixed execution environment

Each baseline/candidate trial has the same enforced WASI settings:

| Setting | Value |
|---|---|
| Guest CPU execution | One thread; shared memory and threads disabled |
| Linear memory | 2,147,483,648 bytes (2 GiB) maximum |
| Instruction fuel | 30,000,000,000 Wasmtime fuel units |
| ABI | wasm32 WASIp1 |
| Runtime / bundled C/C++ compiler | Wasmtime 50.0.1 / Zig 0.14.1 |
| Guest filesystem | Read-only inputs and a separate writable output directory |
| Guest network / process spawning | No capabilities provided |

These are guest limits, not a two-core OS quota or a cap on the Python/JIT
runtime's own memory. Wall-clock times still depend on the host CPU. Fuel limits
Wasm execution; the supervised wall-time deadline also stops blocked host calls.
The problem YAML is the source for memory/fuel settings, which are returned in
the prompt and report. Unsupported resource settings fail validation.

The bundled graph benchmark has 5,000 vertices and 1,444 queries in each of its
`mesh` and `disconnected` cases. Inputs include repeated sources, unreachable
vertices, parallel edges, self-loops, zero weights and 64-bit distances. See its
[statement](src/plugin/problems/repeated-shortest-paths/prompt.md).

The current schedule is one warmup and five measured pairs per case, alternating
execution order, 4 seconds per graph trial and 120 seconds per assessment. Acceptance
requires exact output bytes, suite speedup ≥1.10, no case slowdown above 1.05,
and relative timing MAD ≤0.10. Smaller `group-counts` and `set-intersection`
bundles are also available. Call `list_problems` and copy its exact reference:

```yaml
origin: bundled
directory: repeated-shortest-paths
```

`workspace` refers only to bundles actually staged in that session workspace.
Origins never substitute for one another. Traversal and symlinks are rejected.

### Candidate projects and dependencies

A project supplies UTF-8 files, preparation argv arrays, and a run argv whose
first entry names the generated `.wasm` module. C++ example:

```yaml
project:
  files:
    - path: main.cpp
      content: <complete program with standard headers>
  prepare:
    - [python3, -m, ziglang, c++, -target, wasm32-wasi, -fno-exceptions, -O2, -std=c++17, main.cpp, -o, main.wasm]
  run: [./main.wasm, '{input_dir}', '{output_path}']
```

The plugin installs its pinned Zig and Wasmtime dependencies through uv. Programs
receive `/inputs` and `/output/result.bin` as their two arguments. They read the
problem's files and write the one result file. Do not use nonstandard headers
such as `bits/stdc++.h`; this WASI C++ configuration disables exceptions.

Other languages are accepted when their compiler or interpreter produces a
compatible WASIp1 module. Plain native executables and Python/JavaScript source
are rejected as candidate executables. Language runtimes and runtime libraries
must themselves support WASI; the plugin never substitutes native execution.

Preparation runs outside measurements in the existing host sandbox. Include
locked manifests and install third-party Python **build dependencies** with uv.
For example, prepare a venv with `uv sync --frozen`, run a code-generation script,
and compile its output to WASI. Only declared HTTPS package hosts are reachable
through the host proxy during preparation. Missing tools and failed builds raise
errors with bounded compiler diagnostics. Fix the project and evaluate it again;
printed source or a theoretical speedup is not a benchmark result.

### Measurements and evidence

Each trial uses a fresh guarded process and Wasmtime store. Timing includes
process startup, runtime/module initialization and confirmed cleanup; it excludes
source compilation, dependency installation and file staging. Every output must
match the baseline bytes exactly, including order and newlines.

Reports retain the canonical project, source/config/fixture snapshots, preparation
argv/logs, exact result files, raw elapsed times and the computed verdict under
`assessments/<id>/`. The result card shows baseline/candidate median times, paired
speedups and stability. The verifier recomputes acceptance from artifact hashes,
result bytes, the full schedule and raw trials. No model adjudicates correctness.
Fixtures are public; this is a cooperative optimization benchmark.

## Layout

```text
.
├── nanoagent.yaml   # identity, permissions, tasks and execution guidance
├── contract.py      # typed projects, limits and benchmark evidence
├── README.md
├── LICENSE
├── pyproject.toml
├── uv.lock
├── .gitignore
├── .env
├── src/plugin/      # discovery, WASI execution, guarded compilation and timing
│   └── problems/    # YAML, prompts, C++ baselines and fixtures
├── tests/           # resource, compiler, verifier and installed-package checks
├── ui/              # result renderers using the existing page kit
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

Version 0.3.0 replaces native candidate execution with WASI. Host process
supervision currently supports macOS; Windows/Linux hosts fail explicitly.
No app rebuild is required for this plugin update. See
[tests/RELEASE.md](tests/RELEASE.md) for checks through the installed RUSH Lab runtime.
