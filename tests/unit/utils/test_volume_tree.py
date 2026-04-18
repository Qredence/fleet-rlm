from __future__ import annotations

import pytest

from fleet_rlm.utils.volume_tree import (
    resolve_mounted_volume_path,
    resolve_realpath_within_root,
)


def test_resolve_realpath_within_root_reports_clean_invalid_path_error() -> None:
    resolved, error = resolve_realpath_within_root(
        "../etc/passwd",
        root="/tmp/fleet-root",
        empty_error="missing",
        invalid_error_prefix="invalid path: ",
    )

    assert resolved is None
    assert error == "invalid path: ../etc/passwd"


def test_resolve_mounted_volume_path_defaults_to_daytona_mount_root() -> None:
    assert resolve_mounted_volume_path("artifacts/run.json") == (
        "/home/daytona/memory/artifacts/run.json"
    )


def test_resolve_mounted_volume_path_supports_legacy_roots_when_explicit() -> None:
    assert (
        resolve_mounted_volume_path(
            "notes.txt",
            default_root="/data/memory",
            allowed_root="/data",
        )
        == "/data/memory/notes.txt"
    )


def test_resolve_mounted_volume_path_defaults_to_daytona_mount_root_with_memory_prefix() -> (
    None
):
    assert (
        resolve_mounted_volume_path("memory/example.txt")
        == "/home/daytona/memory/memory/example.txt"
    )


def test_resolve_mounted_volume_path_rejects_paths_outside_daytona_mount_root() -> None:
    with pytest.raises(ValueError, match="mounted volume root '/home/daytona/memory'"):
        resolve_mounted_volume_path("/tmp/example.txt")
