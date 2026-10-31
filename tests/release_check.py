"""Run actual installed MCP tasks using the rebuilt desktop engine and sandbox."""

import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

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
    outer_timeout_seconds: float
    dependency: str


ROOT = Path(__file__).parents[1]


def cpp_project(source, *, files=None, prepare=None):
    return {
        "files": [{"path": "main.cpp", "content": source}, *(files or [])],
        "prepare": [*(prepare or []), ["python3", "-m", "ziglang", "c++", "-target", "wasm32-wasi",
                    "-fno-exceptions", "-O2", "-std=c++17", "main.cpp", "-o", "main.wasm"]],
        "run": ["./main.wasm", "{input_dir}", "{output_path}"],
    }


def wat_project(source):
    return {
        "files": [{"path": "main.wat", "content": source}],
        "prepare": [["python3", "-c", 'from pathlib import Path; from wasmtime import wat2wasm; Path("main.wasm").write_bytes(wat2wasm(Path("main.wat").read_text()))']],
        "run": ["./main.wasm", "{input_dir}", "{output_path}"],
    }

async def main():
    cfg = load_config(ReleaseConfig, [str(Path(__file__).with_name("release.yaml"))])
    evidence = (ROOT / cfg.output_directory).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    engine = Path(sys.executable).parents[3]
    if not all((engine / "bin" / name).is_file() for name in ("node", "uv")):
        raise RuntimeError("Run release_check.py with the installed RUSH Lab engine's Python")
    # Match lab.mjs systemEnv: use the app's tools, not the invoking terminal's.
    os.environ["PATH"] = os.pathsep.join([str(engine / "bin"), str(engine / "git" / "bin"), "/usr/bin", "/bin"])
    runtime = plugin_python(sys.executable)
    sandbox.configure("seatbelt", runtime)
    data = pack(ROOT)
    entry = inspect_package(data)
    (evidence / "code-optimize.tar.gz").write_bytes(data)
    installed = Installs(evidence / "installs", evidence / "uv-cache", 60, runtime).install(entry["sha256"], data)
    manifest = installed.manifest
    rows = []
    (evidence / "identity.json").write_text(json.dumps({
        "python": sys.executable, "version": sys.version,
        "nanoagent": str(__import__("nanoagent").__file__),
        "package_sha256": entry["sha256"], "package_bytes": len(data),
    }, indent=2))

    async def invoke(name, task, given, stage=None):
        workspace = evidence / "runs" / name
        workspace.mkdir(parents=True)
        if stage:
            stage(workspace)
        outcome = await run_task(
            manifest, manifest.task(task), given, workspace,
            make_agent=None, make_decider=None, timeout=cfg.outer_timeout_seconds,
            on_event=lambda *args: None, stage=lambda path: None,
        )
        row = {"name": name, "valid": outcome.verdict.valid, "verdict": outcome.verdict.message}
        if task == "evaluate-code":
            row.update(reason=outcome.output["summary"]["reason"],
                       speedup=outcome.output["summary"]["suite_speedup"],
                       correct=all(case["outputs_match"] for case in outcome.output["summary"]["cases"]))
        (workspace / "returned-output.json").write_text(json.dumps(outcome.output, indent=2))
        rows.append(row)
        (evidence / "results.json").write_text(json.dumps(rows, indent=2))
        print(json.dumps(row), flush=True)
        return outcome, workspace

    listing, _ = await invoke("list", "list-problems", {})
    assert listing.verdict.valid
    reference = {"origin": "bundled", "directory": "repeated-shortest-paths"}
    assert reference in [item["reference"] for item in listing.output["problems"]]
    for problem in ("repeated-shortest-paths", "group-counts", "set-intersection"):
        description, _ = await invoke(f"describe-{problem}", "describe-problem",
                                      {"problem": {"origin": "bundled", "directory": problem}})
        assert description.verdict.valid and description.output["problem_config"]["baseline"].endswith(".cpp")
        assert "2147483648" in description.output["prompt"] and "30000000000" in description.output["prompt"]

    source = (ROOT / "tests/candidates/graph.cpp").read_text()
    bad = cpp_project('#include <nonexistent_wasi_header.h>\n' + source)
    try:
        await invoke("compile-error", "evaluate-code", {"problem": reference, "project": bad})
    except RuntimeError as error:
        assert "project preparation failed" in str(error), str(error)
        (evidence / "runs/compile-error/error.json").write_text(json.dumps({"error": str(error)}))
    else:
        raise AssertionError("invalid C++ unexpectedly compiled")

    for name, candidate, reason in (
        ("wrong", cpp_project('#include <fstream>\nint main(int,char**v){std::ofstream(v[2])<<"wrong\\n";}'), "wrong_output"),
        ("crash", wat_project('(module (func (export "_start") unreachable))'), "execution_failure"),
        ("cpp", cpp_project(source), "pass"),
    ):
        given = {"problem": reference, "project": candidate}
        outcome, workspace = await invoke(name, "evaluate-code", given)
        assert outcome.output["summary"]["reason"] == reason, outcome.output["summary"]
        assert verify_output(manifest, "evaluate_code", given, outcome.output, workspace).valid
        altered = json.loads(json.dumps(outcome.output))
        altered["candidate"]["sha256"] = "0" * 64
        assert not verify_output(manifest, "evaluate_code", given, altered, workspace).valid

    pyproject = '[project]\nname="graph-candidate"\nversion="0.0.0"\nrequires-python=">=3.12"\ndependencies=[' + json.dumps(cfg.dependency) + ']\n'
    with TemporaryDirectory(prefix="code-optimize-lock-") as temporary:
        dependency_root = Path(temporary)
        (dependency_root / "pyproject.toml").write_text(pyproject)
        process = await asyncio.create_subprocess_exec("uv", "lock", "--project", temporary, "--python", sys.executable)
        if await process.wait() != 0:
            raise RuntimeError("candidate dependency locking failed")
        locked = (dependency_root / "uv.lock").read_text()
    generator = 'from pathlib import Path; import six; assert six.PY3; Path("dependency.h").write_text("#define DEPENDENCY_READY 1\\n")'
    dependent = cpp_project('#include "dependency.h"\nstatic_assert(DEPENDENCY_READY == 1);\n' + source,
                            files=[{"path": "pyproject.toml", "content": pyproject},
                                   {"path": "uv.lock", "content": locked},
                                   {"path": "generate.py", "content": generator}],
                            prepare=[["uv", "sync", "--frozen", "--python", "python3"],
                                     [".venv/bin/python", "generate.py"]])
    outcome, _ = await invoke("build-dependency", "evaluate-code", {"problem": reference, "project": dependent})
    assert outcome.verdict.valid, outcome.output["summary"]

    def stage(workspace):
        shutil.copytree(ROOT / "src/plugin/problems/repeated-shortest-paths", workspace / "assignment")

    listing, _ = await invoke("workspace-list", "list-problems", {}, stage)
    workspace_reference = {"origin": "workspace", "directory": "assignment"}
    assert workspace_reference in [item["reference"] for item in listing.output["problems"]]
    result, _ = await invoke("workspace-describe", "describe-problem", {"problem": workspace_reference}, stage)
    assert result.verdict.valid
    print(f"Release checks passed: {evidence}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
