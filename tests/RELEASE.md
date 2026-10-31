# Code Optimize release checks

Version 0.2.0 uses C++17 baselines and typed, multilingual candidate projects.
Deterministic code checks every result, artifact and acceptance threshold.

From the plugin root:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv lock --check
```

Build the regular **RUSH Lab** desktop app from the NanoAgent checkout with
`npm --prefix helpinghands run desktop`, install/open that build, and run
[release_check.py](release_check.py) with its installed engine's Python from the
plugin root. Use the engine path shown in the app's startup log under
`~/Library/Application Support/lab.mochi.lab/app/<stamp>/mochi-lab/nanoagent/.venv/bin/python`.
Local validation for this project uses RUSH Lab, as requested by its owner.

The settings in [release.yaml](release.yaml) select a new evidence directory
outside the checkout, the outer deadline and a pinned dependency. Preserve each
previous evidence folder before running again. The harness packs and inspects
the plugin, installs through NanoAgent's normal installer, enables macOS Seatbelt,
and invokes real MCP tasks and the restricted verifier. It checks:

- Discovery and descriptions of all three bundled problems, plus a workspace bundle.
- Wrong and crashing submissions with deterministic rejection and intact reports.
- Python and JavaScript graph candidates against the compiled C++ baseline.
- A separate Python virtual environment, installing a pinned package using uv
  through the sandbox's declared package proxy.
- Exact output bytes, paired timing policy, and rejection of altered evidence.

The returned reports include source and fixture snapshots, compiler version and
build argv/log artifacts, runtime tool paths, raw timing samples and policy
outcomes. A passing correctness check alone does not imply a speedup. Preserve
failed timing outcomes; do not rerun merely to obtain a pass.

[renderer_preview.py](renderer_preview.py) serves the actual result renderers
using the recorded installed evidence. Its YAML selects evidence and kit paths.
After publishing, create a new session in RUSH Lab and check that its intro shows
the graph challenge, starting prompt and current configuration. Ask the agent
to list problems, describe `bundled/repeated-shortest-paths`, and evaluate a
candidate. Existing sessions remain evidence of the version they ran.

The supported interface is `project_v1` / `executable_directory_v1`. A candidate
has UTF-8 files, explicit preparation argv arrays and a run argv with exactly one
`{input_dir}` and `{output_path}` argument. Preparation has at most 16 commands,
30 seconds per command within the assessment's total deadline, and bounded logs.
Python dependencies use uv and project manifests; other languages require an
available toolchain and explicit commands. Missing tools, unavailable package
hosts, invalid projects and preparation failures raise clear errors.

Each trial receives a fresh copy of the prepared project and fixtures. Timing
includes process guard startup and confirmed cleanup, excluding preparation and
copying. macOS process-group ownership and EOF lifetime supervision remain in
force for build commands and trials. Execution, cancellation and descendant
ownership tests exercise deadlines and cleanup. Windows and Linux execution
remain explicitly unsupported.

Configuration bounds remain 120 seconds per assessment, 256 trials, 1 MiB
submitted project, 4 MiB accepted result per trial, 1 MiB retained combined log
per command/trial, 2,000 bundle files, 32 MiB bundle snapshots and 64 MiB total
declared report evidence. The schedule requires 20% timeout overhead. The graph
bundle currently uses 2-second trials, 120 seconds total, one warmup and five
measured pairs per case, speedup ≥1.10, slowdown ≤1.05 and relative MAD ≤0.10.

Publishing preserves manifest status; no background build changes it. The new
version must be pushed and published before the marketplace serves its session
description or API. Local package validation does not update production.
