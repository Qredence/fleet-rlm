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
