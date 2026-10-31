"""Project manifests are explicit and reject paths outside their project."""

from dataclasses import replace

import pytest

from contract import CandidateProject, ProjectFile
from verifier.checks import validate_project


def project():
    return CandidateProject(
        [ProjectFile("main.wat", "(module)")], [], ["./main.wat", "{input_dir}", "{output_path}"]
    )


def test_project_contract_rejects_traversal_and_ambiguous_commands():
    valid = project()
    validate_project(valid, 1024)
    for invalid in (
        replace(valid, files=[ProjectFile("../main.py", "x")]),
        replace(valid, files=[ProjectFile("/main.py", "x")]),
        replace(valid, files=[ProjectFile("main.py", "x"), ProjectFile("main.py", "y")]),
        replace(valid, run=["python3", "main.py", "{input_dir}"]),
        replace(valid, prepare=[["/bin/sh", "-c", "true"]]),
        replace(valid, prepare=[["tool", "../outside"]]),
    ):
        with pytest.raises(ValueError):
            validate_project(invalid, 1024)


def test_prepared_module_is_project_relative(tmp_path):
    from plugin.project import checked_command

    executable = tmp_path / "build" / "main.wasm"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"\0asm")
    value = replace(project(), run=["build/main.wasm", "{input_dir}", "{output_path}"])
    validate_project(value, 1024)
    assert checked_command(value.run, tmp_path) == value.run
    with pytest.raises(ValueError):
        validate_project(replace(value, run=["build/../main.wasm", "{input_dir}", "{output_path}"]), 1024)
    with pytest.raises(ValueError):
        validate_project(replace(value, run=["python3", "{input_dir}", "{output_path}"]), 1024)
