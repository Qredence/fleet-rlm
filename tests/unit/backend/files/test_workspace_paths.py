"""Session Workspace path policy."""

from __future__ import annotations

import pytest


def _normalize(path: str, *, allow_root: bool = False) -> str:
    from fleet_rlm.files.workspace_validation import normalize_workspace_path

    return normalize_workspace_path(path, allow_root=allow_root)


def test_normalizes_safe_relative_paths_and_root() -> None:
    assert _normalize("notes/decision.md") == "notes/decision.md"
    assert _normalize(".", allow_root=True) == "."


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute.txt",
        "../escape.txt",
        "notes/../escape.txt",
        "notes\\escape.txt",
        "notes/\x00escape.txt",
        ".fleet/config",
        "notes/.fleet/config",
        "./notes.txt",
        "notes//decision.md",
    ],
)
def test_rejects_unsafe_file_paths(path: str) -> None:
    with pytest.raises(ValueError):
        _normalize(path)


def test_root_is_only_valid_when_explicitly_allowed() -> None:
    with pytest.raises(ValueError):
        _normalize(".")


def test_enforces_exact_depth_segment_and_total_utf8_bounds() -> None:
    assert _normalize("/".join(["a"] * 8)) == "/".join(["a"] * 8)
    with pytest.raises(ValueError):
        _normalize("/".join(["a"] * 9))

    assert _normalize("a" * 255) == "a" * 255
    with pytest.raises(ValueError):
        _normalize("a" * 256)

    bounded = "/".join(["a" * 127] * 8)
    assert len(bounded.encode("utf-8")) == 1023
    assert _normalize(bounded) == bounded
    assert _normalize(bounded + "a") == bounded + "a"
    with pytest.raises(ValueError):
        _normalize(bounded + "aa")


def test_bounds_use_utf8_bytes_not_code_points() -> None:
    assert _normalize("é" * 127) == "é" * 127
    with pytest.raises(ValueError):
        _normalize("é" * 128)
