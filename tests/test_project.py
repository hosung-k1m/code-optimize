"""Project manifests are explicit and reject paths outside their project."""

from dataclasses import replace

import pytest

from contract import CandidateProject, ProjectFile
from verifier.checks import validate_project


def project():
    return CandidateProject(
        [ProjectFile("main.py", "print('ok')")], [], ["python3", "main.py", "{input_dir}", "{output_path}"]
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


def test_prepared_environment_executable_is_project_relative(tmp_path):
    import sys

    from plugin.project import checked_command

    executable = tmp_path / ".venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    value = replace(project(), run=[".venv/bin/python", "main.py", "{input_dir}", "{output_path}"])
    validate_project(value, 1024)
    assert checked_command(value.run, tmp_path) == value.run
    with pytest.raises(ValueError):
        validate_project(replace(value, run=[".venv/../python", "{input_dir}", "{output_path}"]), 1024)
