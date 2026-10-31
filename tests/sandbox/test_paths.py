from __future__ import annotations

import pytest

from fleet_rlm.workspace import _workspace_relative_path, session_volume_subpath, validate_session_id


@pytest.mark.parametrize("session_id", ["finance-q4", "session_01", "2e8c98b5-5242-4e3b-91b4-2bf1e8c24f0b"])
def test_session_ids_map_to_isolated_volume_subpaths(session_id: str) -> None:
    validate_session_id(session_id)

    assert session_volume_subpath(session_id) == f"sessions/{session_id}"


@pytest.mark.parametrize("session_id", ["../other", "a/b", "/root", "", ".", "..", "bad id"])
def test_session_ids_reject_volume_path_syntax(session_id: str) -> None:
    with pytest.raises(ValueError):
        validate_session_id(session_id)


def test_workspace_paths_are_rooted_and_have_bounded_names() -> None:
    assert _workspace_relative_path("files/quarter-1.csv") == "files/quarter-1.csv"
    for path in ("/etc/passwd", "files/../../secret", "skills\\escape", "files/a\x00b", "files/" + "a" * 1020):
        with pytest.raises(ValueError):
            _workspace_relative_path(path)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("files/a.log", "files/a.log"),
        ("/workspace/files/a.log", "files/a.log"),
        ("skills/x/SKILL.md", "skills/x/SKILL.md"),
        ("/workspace/artifacts/report.json", "artifacts/report.json"),
    ],
)
def test_child_input_paths_accept_the_sandbox_absolute_form(given: str, expected: str) -> None:
    from fleet_rlm.workspace import _child_input_path

    assert _child_input_path(given) == expected


@pytest.mark.parametrize("bad", ["/workspace/memory/x", "/etc/passwd", "/workspace/files/../memory/x", "files/../x"])
def test_child_input_paths_still_reject_escapes_and_other_roots(bad: str) -> None:
    from fleet_rlm.workspace import _child_input_path

    with pytest.raises(ValueError):
        _child_input_path(bad)


def test_generated_remote_scripts_compile_and_report_existing_paths(tmp_path):
    """Remote script builders must emit Python, not a string that merely looks like it.

    The transport base64-encodes and `compile`s these, so a literal backslash-n is a
    SyntaxError that only shows up against a real sandbox. Compile them here instead.
    """
    import json
    import subprocess
    import sys

    from fleet_rlm.sandbox.runtime.fs_ops import (
        existing_workspace_paths_code,
        list_workspace_files_code,
        verify_python_artifact_code,
        workspace_files_metadata_code,
    )

    destinations = ["artifacts/child/present.txt", "artifacts/child/missing.txt"]
    scripts = {
        "existing": existing_workspace_paths_code(destinations),
        "existing_empty": existing_workspace_paths_code([]),
        "list": list_workspace_files_code(),
        "metadata": workspace_files_metadata_code(["files/a.txt"], create_parents=False),
        "verify": verify_python_artifact_code("artifacts/run.py", digest="0" * 64, timeout=5),
    }
    for name, code in scripts.items():
        compile(code, f"<{name}>", "exec")

    # Execute the destination probe against a real directory tree, mapped to /workspace.
    workspace = tmp_path / "workspace"
    (workspace / "artifacts/child").mkdir(parents=True)
    (workspace / "artifacts/child/present.txt").write_text("x")
    probe = scripts["existing"].replace("/workspace", str(workspace))
    completed = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)

    assert json.loads(completed.stdout) == ["artifacts/child/present.txt"]
