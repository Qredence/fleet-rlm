"""impl-07: Volume mount defaults and safe path layout (no live Daytona)."""

from __future__ import annotations

from pathlib import PurePosixPath
from uuid import uuid4

import pytest

from fleet_rlm_clean.daytona.paths import (
    DEFAULT_VOLUME_MOUNT_PATH,
    UnsafePathError,
    VolumePaths,
    resolve_under_root,
    validate_mount_path,
    validate_path_id,
)
from fleet_rlm_clean.daytona.volumes import (
    DEFAULT_VOLUME_NAME,
    VolumeConfig,
    get_or_create_volume_id,
    volume_config_from_settings,
    volume_mount_spec,
)


def test_default_mount_matches_design() -> None:
    assert DEFAULT_VOLUME_MOUNT_PATH == "/home/daytona/fleet"
    root = VolumePaths.from_mount()
    assert root.root == PurePosixPath("/home/daytona/fleet")
    assert root.skills_root() == PurePosixPath("/home/daytona/fleet/skills")
    assert root.artifacts_root() == PurePosixPath("/home/daytona/fleet/artifacts")
    assert root.attachments_root() == PurePosixPath("/home/daytona/fleet/attachments")
    assert root.memory_root() == PurePosixPath("/home/daytona/fleet/memory")
    assert root.sessions_root() == PurePosixPath("/home/daytona/fleet/sessions")


def test_validate_mount_path_rejects_unsafe() -> None:
    with pytest.raises(UnsafePathError):
        validate_mount_path("/")
    with pytest.raises(UnsafePathError):
        validate_mount_path("relative/path")
    with pytest.raises(UnsafePathError):
        validate_mount_path("/home/daytona/../etc")
    with pytest.raises(UnsafePathError):
        validate_mount_path("/etc/fleet")
    with pytest.raises(UnsafePathError):
        validate_mount_path("/home//daytona/fleet")
    with pytest.raises(UnsafePathError):
        validate_mount_path("")


def test_validate_path_id_rejects_traversal_and_separators() -> None:
    with pytest.raises(UnsafePathError):
        validate_path_id("../etc")
    with pytest.raises(UnsafePathError):
        validate_path_id("a/b")
    with pytest.raises(UnsafePathError):
        validate_path_id("not-a-uuid")
    with pytest.raises(UnsafePathError):
        validate_path_id("")
    with pytest.raises(UnsafePathError):
        validate_path_id("abc\x00def")
    sid = uuid4()
    assert validate_path_id(sid) == str(sid)
    assert validate_path_id(str(sid)) == str(sid)


def test_run_staging_roots_are_unique_per_run() -> None:
    paths = VolumePaths.from_mount()
    session_id = uuid4()
    run_a = uuid4()
    run_b = uuid4()
    a = paths.run_staging_dir(session_id, run_a)
    b = paths.run_staging_dir(session_id, run_b)
    assert a != b
    assert a.parent == paths.run_dir(session_id, run_a)
    assert str(a).startswith(str(paths.session_dir(session_id)))
    assert a.name == "staging"


def test_resolve_under_root_rejects_escape() -> None:
    root = PurePosixPath("/home/daytona/fleet")
    with pytest.raises(UnsafePathError):
        resolve_under_root(root, "..")
    with pytest.raises(UnsafePathError):
        resolve_under_root(root, "sessions", "../x")
    ok = resolve_under_root(root, "sessions", str(uuid4()))
    assert str(ok).startswith("/home/daytona/fleet/sessions/")


def test_volume_config_and_mount_spec() -> None:
    cfg = VolumeConfig()
    assert cfg.name == DEFAULT_VOLUME_NAME
    assert cfg.mount_path == DEFAULT_VOLUME_MOUNT_PATH
    assert cfg.paths().root == PurePosixPath(DEFAULT_VOLUME_MOUNT_PATH)
    spec = volume_mount_spec(cfg, "vol-123")
    assert spec == {"volume_id": "vol-123", "mount_path": DEFAULT_VOLUME_MOUNT_PATH}

    with pytest.raises(ValueError):
        VolumeConfig(name="../evil")
    with pytest.raises(UnsafePathError):
        VolumeConfig(mount_path="/etc")


def test_get_or_create_volume_id_uses_injected_client() -> None:
    class _Vol:
        id = "vid-1"

    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool]] = []

        def get(self, name: str, *, create: bool = False) -> _Vol:
            self.calls.append((name, create))
            return _Vol()

    client = _Client()
    vid = get_or_create_volume_id(client, VolumeConfig(name="my-vol"))
    assert vid == "vid-1"
    assert client.calls == [("my-vol", True)]


def test_settings_volume_fields() -> None:
    from fleet_rlm_clean.config import Settings

    settings = Settings()
    assert settings.volume_name == DEFAULT_VOLUME_NAME
    assert settings.volume_mount_path == DEFAULT_VOLUME_MOUNT_PATH
    cfg = volume_config_from_settings(settings)
    assert cfg.name == settings.volume_name
