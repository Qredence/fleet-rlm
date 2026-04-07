from __future__ import annotations

import pytest

from fleet_rlm.utils.volume_tree import resolve_mounted_volume_path


def test_resolve_mounted_volume_path_defaults_to_daytona_mount_root() -> None:
    assert (
        resolve_mounted_volume_path("memory/example.txt")
        == "/home/daytona/memory/memory/example.txt"
    )


def test_resolve_mounted_volume_path_rejects_paths_outside_daytona_mount_root() -> None:
    with pytest.raises(ValueError, match="mounted volume root '/home/daytona/memory'"):
        resolve_mounted_volume_path("/tmp/example.txt")
