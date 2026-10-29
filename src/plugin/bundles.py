"""Explicit-origin problem bundle loading, validation and immutable snapshots."""

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from contract import ProblemConfig, ProblemReference
from plugin.config import load_problem_config


@dataclass(frozen=True)
class ProblemBundle:
    reference: ProblemReference
    config: ProblemConfig
    configuration: bytes
    prompt: bytes
    baseline: bytes
    inputs: dict[str, dict[str, bytes]]


def _relative(value: str) -> PurePosixPath:
    if not value or "\\" in value:
        raise ValueError(f"invalid bundle path: {value!r}")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError(f"invalid bundle path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError(f"invalid bundle path: {value!r}")
    return path


def _read_regular(root: Path, relative: str, maximum_size: int) -> bytes:
    rel = _relative(relative)
    cursor = root
    for part in rel.parts:
        cursor = cursor / part
        info = cursor.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"bundle path contains symlink: {relative}")
    info = cursor.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"bundle path is not a regular file: {relative}")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(cursor, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > maximum_size:
            raise ValueError(f"bundle file changed or exceeded its size limit: {relative}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(maximum_size + 1)
        after = os.fstat(descriptor)
        changed = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if len(data) > maximum_size or len(data) != opened.st_size or changed:
            raise ValueError(f"bundle file changed while being snapshotted: {relative}")
        return data
    finally:
        os.close(descriptor)


def _inventory(root: Path) -> dict[str, bytes]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"problem bundle is not a real directory: {root}")
    entries: list[tuple[str, Path, int]] = []
    total_size = 0
    for base, dirs, names in os.walk(root, followlinks=False):
        base_path = Path(base)
        for name in list(dirs):
            path = base_path / name
            if path.is_symlink() or not path.is_dir():
                raise ValueError(f"bundle contains symlink or special directory: {path.relative_to(root)}")
        for name in names:
            path = base_path / name
            rel = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ValueError(f"bundle contains symlink or special file: {rel}")
            entries.append((rel, path, info.st_size))
            total_size += info.st_size
            if len(entries) > 2000:
                raise ValueError("bundle exceeds the 2,000-file limit")
            if total_size > 32 * 1024 * 1024:
                raise ValueError("bundle exceeds the 32 MiB unpacked file limit")
    files: dict[str, bytes] = {}
    for rel, path, expected_size in entries:
        data = _read_regular(root, rel, expected_size)
        if len(data) != expected_size:
            raise ValueError(f"bundle file changed while being snapshotted: {rel}")
        files[rel] = data
    return files


def load_problem(reference: ProblemReference, workspace: Path) -> ProblemBundle:
    """Load from exactly the selected origin and snapshot every declared input byte."""
    if reference.origin == "workspace":
        root_base = workspace
    elif reference.origin == "bundled":
        root_base = Path(__file__).parent / "problems"
    else:
        raise ValueError(f"unsupported problem origin: {reference.origin}")
    rel = _relative(reference.directory)
    root = root_base.joinpath(*rel.parts)
    # Ensure the selected directory and every component beneath its origin are ordinary.
    cursor = root_base
    if cursor.is_symlink() or not cursor.is_dir():
        raise ValueError(f"problem origin is not a real directory: {reference.origin}")
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink() or not cursor.is_dir():
            raise ValueError(f"problem directory does not exist at selected origin: {reference.origin}/{reference.directory}")
    if not root.is_dir():
        raise ValueError(f"problem directory does not exist at selected origin: {reference.origin}/{reference.directory}")
    inventory = _inventory(root)
    try:
        config_bytes = inventory["problem.yaml"]
    except KeyError:
        raise ValueError("bundle is missing problem.yaml") from None
    if len(config_bytes) > 1024 * 1024:
        raise ValueError("problem.yaml must be at most 1 MiB")
    # Parse the exact config bytes already snapshotted, avoiding a read/parse race.
    with tempfile.TemporaryDirectory(prefix="code-optimize-config-", dir=workspace) as temporary:
        config_path = Path(temporary) / "problem.yaml"
        config_path.write_bytes(config_bytes)
        config = load_problem_config(config_path)
    _relative(config.prompt)
    _relative(config.baseline)
    if config.prompt == "problem.yaml" or config.baseline == "problem.yaml" or config.prompt == config.baseline:
        raise ValueError("prompt, baseline, and problem.yaml must be distinct bundle files")
    fixture_dirs = [_relative(case.input_dir).as_posix() for case in config.cases]
    declared_files = {"problem.yaml", config.prompt, config.baseline}
    for fixture_dir in fixture_dirs:
        if any(path == fixture_dir or path.startswith(fixture_dir + "/") or fixture_dir.startswith(path + "/") for path in declared_files):
            raise ValueError(f"fixture directory conflicts with a declared file: {fixture_dir}")
    for index, fixture_dir in enumerate(fixture_dirs):
        if any(
            other == fixture_dir or other.startswith(fixture_dir + "/") or fixture_dir.startswith(other + "/")
            for other in fixture_dirs[index + 1 :]
        ):
            raise ValueError("case fixture directories must not overlap")
    try:
        prompt = inventory[config.prompt]
        baseline = inventory[config.baseline]
    except KeyError as error:
        raise ValueError(f"bundle is missing declared file: {error.args[0]}") from None
    if len(prompt) > 1024 * 1024:
        raise ValueError("prompt must be at most 1 MiB")
    if len(baseline) > config.execution.max_source_bytes:
        raise ValueError("baseline source exceeds max_source_bytes")
    fixture_snapshots: dict[str, dict[str, bytes]] = {}
    used = {"problem.yaml", config.prompt, config.baseline}
    for case in config.cases:
        fixture_root = _relative(case.input_dir)
        case_files: dict[str, bytes] = {}
        prefix = fixture_root.as_posix() + "/"
        for path, content in inventory.items():
            if path.startswith(prefix):
                case_files[path[len(prefix):]] = content
                used.add(path)
        if not case_files:
            raise ValueError(f"case {case.id} has no fixture files")
        fixture_snapshots[case.id] = case_files
    unknown = set(inventory) - used
    if unknown:
        raise ValueError(f"bundle contains undeclared files: {', '.join(sorted(unknown))}")
    trial_count = 2 * len(config.cases) * (config.measurement.warmup_pairs + config.measurement.measured_pairs)
    evidence_bound = (
        sum(map(len, inventory.values()))
        + config.execution.max_source_bytes
        + trial_count * (config.execution.max_output_bytes + config.execution.max_log_bytes)
        + 1024 * 1024
    )
    if evidence_bound > 64 * 1024 * 1024:
        raise ValueError("snapshot and maximum assessment evidence exceed the 64 MiB report budget")
    return ProblemBundle(reference, config, config_bytes, prompt, baseline, fixture_snapshots)
