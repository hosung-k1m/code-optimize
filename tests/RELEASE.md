# Code Optimize release checks

Version 0.3.0 is a plugin-only update: Zig and Wasmtime are locked plugin
dependencies. Existing RUSH Lab runs it without Docker, a VM or host changes.

From the plugin root:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv lock --check
```

The unit/integration suite compiles actual C++ baselines to WASI, compares them
with independent golden outputs, verifies compile failure followed by measured
retry, and checks wrong/missing/trapping outputs. Resource tests exercise fuel
exhaustion, memory initialization/growth rejection, shared-memory rejection,
read-only input access and directory escape denial.

Run [release_check.py](release_check.py) with the installed regular **RUSH Lab**
engine's Python from the plugin root. Its path is under
`~/Library/Application Support/lab.mochi.lab/app/<stamp>/mochi-lab/nanoagent/.venv/bin/python`.
It mirrors the app's tool PATH, installs the packed plugin, enables actual macOS
Seatbelt, and invokes MCP tasks and the restricted verifier. YAML settings select
the evidence directory, outer timeout and pinned Python build dependency.
Preserve each previous evidence directory before running again.

The installed harness checks:

- Listing and descriptions of all problems, including exact sandbox limits.
- An actual compiler error, then a corrected C++ project producing measured output.
- Wrong and trapping WASI candidates with deterministic failed verdicts.
- The optimized C++ graph solver compiled by bundled Zig and executed by Wasmtime.
- A pinned Python build dependency installed with uv to generate a required header
  before compilation, proving dependencies work independently of the plugin venv.
- Workspace bundle discovery and descriptions, artifact hashes and tamper rejection.

A correct output alone is not an accepted optimization: speed and stability gates
must also pass. Preserve failed measurements instead of rerunning to obtain a pass.
Reports include every raw elapsed sample and resource configuration. Compilation
and dependency setup precede timing; fresh-process elapsed time includes Wasmtime
initialization and Wasm module compilation, execution and confirmed cleanup.

Each trial enforces one guest execution thread, a 2 GiB linear-memory cap, and
30 billion instruction fuel units. Threads/shared memory and memory64 are disabled.
The guest receives only read-only `/inputs` and writable `/output` capabilities.
WASI provides no networking or process-spawning capability. The Python/JIT runtime
has overhead outside guest linear memory; local CPU speed still affects elapsed
time. This is an execution budget, not an OS CPU quota or fixed hardware emulation.

All source/build tools are installed by uv from the lockfile. Preparation can run
up to 16 explicit argv commands, with bounded logs and a per-command deadline
inside the overall assessment budget. Other languages must target WASIp1; native
execution never substitutes for an unsupported module. Host process ownership is
currently implemented for macOS only.

[renderer_preview.py](renderer_preview.py) can serve the actual renderers with
recorded reports. After pushing and publishing, create a new session in regular
RUSH Lab and ask it to describe the problem, compile a candidate, fix build errors,
and evaluate again. Confirm the card shows actual median timings and the fixed
WASI configuration. Existing sessions retain the version they previously ran.
