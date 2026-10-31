# Code Optimize release checks

`code-optimize` is a ready `nanoagent.plugin/v0001` plugin for macOS. The deterministic verifier determines assessment outcomes. A human or model does not adjudicate candidate correctness.

Run unit/integration tests and lint from the plugin root:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

[release_check.py](release_check.py) is a deterministic installed-package harness. Run it from the plugin root with the installed RUSH Lab engine's Python:

```bash
/path/to/RUSH-Lab/engine/nanoagent/.venv/bin/python tests/release_check.py
```

Use the actual installed engine path on this computer. It packs and inspects the plugin, installs locked dependencies with NanoAgent's normal installer, enables macOS Seatbelt, and invokes actual tool-runner tasks and the restricted verifier. Its settings are in [release.yaml](release.yaml); its output directory must be new and outside the source checkout so uv does not discover that checkout as an ancestor project. Preserve previous evidence before another run.

The harness checks both bundled problems, exact matching output, wrong/crashing candidates, describe/evaluate/revise feedback, workspace bundle staging, artifact hashes, repeated verification, guarded algorithm controls, timeout/log limits, cancellation, and a large report. Correct output and speed measurements are separate assertions. Timing controls record full assessment outcomes; no unsuccessful assessment reruns to obtain a pass. An optimized submission can fail the stability gate.

[renderer_preview.py](renderer_preview.py) serves actual renderers and kit assets with recorded installed evidence; [renderer_preview.yaml](renderer_preview.yaml) selects those local paths. It supports visual inspection of the same returned report without invoking a model.

The plugin supports only `plugin_python`, `solve_directory_v1`, exact bytes, and wall time. Supported configuration bounds: at least 1 second per guarded trial, at most 120 seconds per assessment, 256 trials, 1 MiB source, 4 MiB accepted output, and 1 MiB retained combined trial logs. The schedule must fit with 20% overhead. A bundle has at most 2,000 files and 32 MiB of snapshotted bytes. The total maximum declared evidence, snapshot, source allowance and 1 MiB report reserve must fit 64 MiB. Unsupported values fail; these are plugin limits, not new host quotas.

On macOS the worker forks a solve process while retaining a trusted process-group anchor. Cleanup sends SIGKILL while the anchor still owns the group, then requires its kqueue exit event, direct-child reap and closed log streams within the original trial deadline. Independent PID and heartbeat tests cover descendants that close logs and ignore SIGTERM. Failed preflight diagnostics remain marked incomplete; sandbox-denied process enumeration is never interpreted as an empty group.

The installed-package harness checks execution directly without server logs.
Check the member workflow in the production RUSH Lab app after publishing:

- Install through the authenticated member path and exercise describe/evaluate/revise/artifact access with the loaded plugin version visible.
- Exercise the real desktop Stop control and relay disconnect, independently confirming worker and managed descendant cleanup.
- Measure the actual deployed outer deadline at the maximum supported schedule/evidence size, including installation/preflight, cleanup, repeated verification and serialization margin.
- Calibrate substantive, startup-dominated, boundary and concurrent controls with the final timing boundary; retain unsuccessful outcomes.
- Implement and test Windows process ownership and its sandbox before advertising Windows support. Linux is also explicitly unsupported in v1.

Publishing preserves the manifest status; it does not schedule a build.
`in-development` deliberately prevents the plugin from opening in RUSH Lab.
Use a new version with `status: ready` when publishing a plugin to try in the app.
