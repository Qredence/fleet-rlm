from __future__ import annotations

from types import SimpleNamespace

from fleet_rlm.runtime.execution.storage_paths import (
    mounted_storage_roots,
    runtime_storage_roots,
)


def test_mounted_storage_roots_preserves_current_daytona_layout() -> None:
    roots = mounted_storage_roots("/home/daytona/memory")

    assert roots.mounted_root == "/home/daytona/memory"
    assert roots.memory_root == "/home/daytona/memory/memory"
    assert roots.artifacts_root == "/home/daytona/memory/artifacts"
    assert roots.buffers_root == "/home/daytona/memory/buffers"
    assert roots.meta_root == "/home/daytona/memory/meta"


def test_mounted_storage_roots_normalizes_legacy_memory_mount() -> None:
    roots = mounted_storage_roots("/data/memory")

    assert roots.mounted_root == "/data"
    assert roots.memory_root == "/data/memory"
    assert roots.artifacts_root == "/data/artifacts"
    assert roots.buffers_root == "/data/buffers"
    assert roots.meta_root == "/data/meta"


def test_runtime_storage_roots_normalizes_legacy_interpreter_mount() -> None:
    roots = runtime_storage_roots(SimpleNamespace(volume_mount_path="/data/memory"))

    assert roots.mounted_root == "/data"
    assert roots.memory_root == "/data/memory"
