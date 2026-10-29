"""Run with the rebuilt desktop's Python; no model or server credentials are needed."""

import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml
from nanoagent.plugin import sandbox
from nanoagent.plugin.install import Installs
from nanoagent.plugin.package import inspect_package, pack
from nanoagent.plugin.python import plugin_python
from nanoagent.plugin.tasks import run_task
from nanoagent.plugin.verifier import verify_output
from slimconfig import Config, load_config


@dataclass
class ReleaseConfig(Config):
    output_directory: str
    repetitions: int
    outer_timeout_seconds: float
    calibrate: bool


ROOT = Path(__file__).parents[1]
FAST = {
    "group-counts": "from pathlib import Path\nfrom collections import Counter\ndef solve(i,o):\n values=map(int,(Path(i)/'data.txt').read_text().split())\n Path(o).write_text(repr(sorted(Counter(values).items())),encoding='utf-8')\n",
    "set-intersection": "from pathlib import Path\ndef solve(i,o):\n p=Path(i)\n a=set(map(int,(p/'left.txt').read_text().split()))\n b=set(map(int,(p/'right.txt').read_text().split()))\n Path(o).write_text(repr(sorted(a&b)),encoding='utf-8')\n",
}


async def main():
    cfg = load_config(ReleaseConfig, [str(Path(__file__).with_name("release.yaml"))])
    if cfg.repetitions <= 0:
        raise ValueError("repetitions must be positive")
    evidence = (ROOT / cfg.output_directory).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    runtime = plugin_python(sys.executable)
    sandbox.configure("seatbelt", runtime)
    data = pack(ROOT)
    entry = inspect_package(data)
    (evidence / "code-optimize.tar.gz").write_bytes(data)
    installed = Installs(evidence / "installs", evidence / "uv-cache", 30, runtime).install(entry["sha256"], data)
    manifest = installed.manifest
    rows = []
    (evidence / "identity.json").write_text(
        json.dumps(
            {
                "python": sys.executable,
                "version": sys.version,
                "source": str(__import__("nanoagent").__file__),
                "package_sha256": entry["sha256"],
                "package_bytes": len(data),
                "installed": str(manifest.root),
            },
            indent=2,
        )
    )

    async def invoke(name, task, given, *, stage=None):
        workspace = evidence / "runs" / name
        workspace.mkdir(parents=True)
        if stage is not None:
            stage(workspace)
        start = time.perf_counter()
        outcome = await run_task(
            manifest,
            manifest.task(task),
            given,
            workspace,
            make_agent=None,
            make_decider=None,
            timeout=cfg.outer_timeout_seconds,
            on_event=lambda *args: None,
            stage=lambda path: None,
        )
        row = {
            "name": name,
            "elapsed_seconds": time.perf_counter() - start,
            "valid": outcome.verdict.valid,
            "verdict": outcome.verdict.message,
            "workspace": str(workspace),
        }
        if task == "evaluate-code":
            row["reason"] = outcome.output["summary"]["reason"]
            row["speedup"] = outcome.output["summary"]["suite_speedup"]
            row["correct"] = all(item["outputs_match"] for item in outcome.output["summary"]["cases"])
        (workspace / "returned-output.json").write_text(json.dumps(outcome.output, indent=2))
        rows.append(row)
        (evidence / "results.json").write_text(json.dumps(rows, indent=2))
        print(json.dumps(row), flush=True)
        return outcome, workspace

    for problem, fast_source in FAST.items():
        reference = {"origin": "bundled", "directory": problem}
        described, _ = await invoke(f"describe-{problem}", "describe-problem", {"problem": reference})
        assert described.verdict.valid
        baseline = described.output["baseline_source"]
        wrong, _ = await invoke(
            f"wrong-{problem}",
            "evaluate-code",
            {
                "problem": reference,
                "source": "from pathlib import Path\ndef solve(i,o): Path(o).write_bytes(b'wrong\\n')",
            },
        )
        assert wrong.output["summary"]["reason"] == "wrong_output"
        crash, _ = await invoke(
            f"crash-{problem}",
            "evaluate-code",
            {"problem": reference, "source": "def solve(i,o): raise RuntimeError('expected release fixture')"},
        )
        assert crash.output["summary"]["reason"] == "execution_failure"
        if cfg.calibrate:
            slow = baseline.replace("def solve(", "def original_solve(") + (
                "\nimport time\ndef solve(i,o):\n original_solve(i,o)\n time.sleep(0.05)\n"
            )
            for variant, source in (("identical", baseline), ("faster", fast_source), ("slower", slow)):
                for attempt in range(cfg.repetitions):
                    outcome, _ = await invoke(
                        f"{problem}-{variant}-{attempt}", "evaluate-code", {"problem": reference, "source": source}
                    )
                    assert all(case["outputs_match"] for case in outcome.output["summary"]["cases"])
        faster, workspace = await invoke(
            f"revised-{problem}", "evaluate-code", {"problem": reference, "source": fast_source}
        )
        given = {"problem": reference, "source": fast_source}
        assert verify_output(manifest, "evaluate_code", given, faster.output, workspace).valid
        modified = json.loads(json.dumps(faster.output))
        modified["candidate"]["sha256"] = "0" * 64
        assert not verify_output(manifest, "evaluate_code", given, modified, workspace).valid

    def stage(workspace):
        shutil.copytree(ROOT / "src/plugin/problems/group-counts", workspace / "assignment")

    output, _ = await invoke(
        "workspace-bundle",
        "evaluate-code",
        {"problem": {"origin": "workspace", "directory": "assignment"}, "source": FAST["group-counts"]},
        stage=stage,
    )
    assert all(case["outputs_match"] for case in output.output["summary"]["cases"])
    for name, source in (
        ("hanging", "import time\ndef solve(i,o): time.sleep(20)"),
        ("log-flood", "import os\ndef solve(i,o):\n while True: os.write(1,b'x'*65536); os.write(2,b'y'*65536)"),
    ):
        outcome, _ = await invoke(
            name, "evaluate-code", {"problem": {"origin": "bundled", "directory": "group-counts"}, "source": source}
        )
        assert outcome.output["summary"]["reason"] == "execution_failure"

    def staged_group(workspace, *, boundary=False):
        stage(workspace)
        problem = workspace / "assignment"
        for path in problem.glob("fixtures/*/data.txt"):
            path.write_text("2 1 2 3 1 2")
        if boundary:
            path = problem / "baseline.py"
            baseline = path.read_text().replace("def solve(", "def original_solve(")
            path.write_text(baseline + "\nimport time\ndef solve(i,o):\n original_solve(i,o)\n time.sleep(0.16)\n")

    if cfg.calibrate:
        for name, boundary in (("tiny", False), ("boundary", True)):
            source = FAST["group-counts"]
            if boundary:
                source = (
                    source.replace("def solve(", "def original_solve(")
                    + "\nimport time\ndef solve(i,o):\n original_solve(i,o)\n time.sleep(0.10)\n"
                )
            for attempt in range(cfg.repetitions):
                await invoke(
                    f"{name}-{attempt}",
                    "evaluate-code",
                    {"problem": {"origin": "workspace", "directory": "assignment"}, "source": source},
                    stage=lambda workspace, boundary=boundary: staged_group(workspace, boundary=boundary),
                )
        await asyncio.gather(
            *(
                invoke(
                    f"concurrent-{index}",
                    "evaluate-code",
                    {"problem": {"origin": "bundled", "directory": "group-counts"}, "source": FAST["group-counts"]},
                )
                for index in range(2)
            )
        )

    cancel_workspace = evidence / "runs/mcp-cancellation"
    cancel_workspace.mkdir(parents=True)
    cancel_source = "import os,time\nfrom pathlib import Path\ndef solve(i,o):\n Path('worker.pid').write_text(str(os.getpid()))\n while True:\n  with Path('heartbeat').open('ab') as f: f.write(b'x'); f.flush()\n  time.sleep(0.02)"
    task = asyncio.create_task(
        run_task(
            manifest,
            manifest.task("evaluate-code"),
            {"problem": {"origin": "bundled", "directory": "group-counts"}, "source": cancel_source},
            cancel_workspace,
            make_agent=None,
            make_decider=None,
            timeout=cfg.outer_timeout_seconds,
            on_event=lambda *args: None,
            stage=lambda path: None,
        )
    )
    try:
        deadline = asyncio.get_running_loop().time() + 30
        pidfile = None
        while not pidfile:
            matches = list(cancel_workspace.glob("assessments/*/trials/*/*/candidate/worker.pid"))
            if matches:
                pidfile = matches[0]
                break
            if task.done() or asyncio.get_running_loop().time() > deadline:
                raise RuntimeError("MCP cancellation candidate did not start")
            await asyncio.sleep(0.02)
        worker_pid = int(pidfile.read_text())
        task.cancel()
        try:
            await asyncio.wait_for(task, 5)
        except asyncio.CancelledError:
            pass
        heartbeat = pidfile.with_name("heartbeat")
        initial_size = heartbeat.stat().st_size
        await asyncio.sleep(0.3)
        assert heartbeat.stat().st_size == initial_size
        try:
            os.kill(worker_pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("Worker survived actual MCP cancellation")
        row = {
            "name": "mcp-cancellation",
            "valid": True,
            "worker_pid": worker_pid,
            "heartbeat_bytes": initial_size,
            "workspace": str(cancel_workspace),
        }
        rows.append(row)
        (evidence / "results.json").write_text(json.dumps(rows, indent=2))
        print(json.dumps(row), flush=True)
    finally:
        if not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, 5)
            except asyncio.CancelledError:
                pass

    def stress_bundle(workspace):
        stage(workspace)
        problem = workspace / "assignment"
        path = problem / "problem.yaml"
        config = yaml.safe_load(path.read_text())
        config["cases"] = config["cases"][:1]
        shutil.rmtree(problem / "fixtures/skewed")
        config["execution"]["per_trial_timeout_seconds"] = 1.0
        config["execution"]["total_timeout_seconds"] = 120.0
        config["execution"]["max_output_bytes"] = 614400
        config["execution"]["max_log_bytes"] = 1024
        config["measurement"]["warmup_pairs"] = 0
        config["measurement"]["measured_pairs"] = 50
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        (problem / "baseline.py").write_text(
            "from pathlib import Path\ndef solve(i,o): Path(o).write_bytes(b'x'*614400)"
        )

    large, _ = await invoke(
        "maximum-schedule-evidence",
        "evaluate-code",
        {
            "problem": {"origin": "workspace", "directory": "assignment"},
            "source": "from pathlib import Path\ndef solve(i,o): Path(o).write_bytes(b'x'*614400)",
        },
        stage=stress_bundle,
    )
    assert len(large.output["trials"]) == 100
    assert sum(trial["result"]["size"] for trial in large.output["trials"]) == 61440000
    print(f"Completed {len(rows)} installed task calls; package {hashlib.sha256(data).hexdigest()}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
