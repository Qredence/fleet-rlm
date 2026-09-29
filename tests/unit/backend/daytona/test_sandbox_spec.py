from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from fleet_rlm.daytona.diagnostics import (
    build_snapshot_image,
    environment_manifest,
    snapshot_dependency_sha256,
    snapshot_execution_dependencies,
)
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.runtime import (
    BASE_IMAGE,
    DEFAULT_SNAPSHOT_NAME,
    DaytonaEnvironmentProfile,
    DaytonaSandboxSpec,
    LiveDaytonaPlatform,
    verify_sandbox_spec,
)
from fleet_rlm.sessions.bindings import session_workspace_volume_subpath


def test_spec_requires_an_immutable_versioned_name() -> None:
    for name in ("", "latest", "fleet-rlm-python313", "fleet-rlm-python313-v0"):
        with pytest.raises(ValueError):
            DaytonaSandboxSpec(name)


def test_spec_builds_non_root_pinned_image_with_toolchain_and_declared_dependencies() -> None:
    spec = DaytonaSandboxSpec("fleet-rlm-python313-v1")
    dockerfile = build_snapshot_image(spec).dockerfile()
    digest = snapshot_dependency_sha256()

    assert f"FROM {BASE_IMAGE}" in dockerfile
    assert "groupadd --gid 1000 daytona" in dockerfile
    assert "USER daytona" in dockerfile
    assert "PYTHONUNBUFFERED=1" in dockerfile
    assert f"FLEET_SNAPSHOT_DEPENDENCIES_SHA256={digest}" in dockerfile
    assert "WORKDIR /home/daytona" in dockerfile
    assert snapshot_execution_dependencies() == (
        "mpmath==1.4.1",
        "numpy==2.5.1",
        "pandas==3.0.5",
        "beautifulsoup4==4.15.0",
    )
    install_line = "pip install beautifulsoup4==4.15.0 mpmath==1.4.1 numpy==2.5.1 pandas==3.0.5"
    assert install_line in dockerfile
    assert dockerfile.index(install_line) < dockerfile.index("USER daytona")
    assert "apt-get install -y --no-install-recommends git ca-certificates" in dockerfile
    assert dockerfile.index("apt-get install") < dockerfile.index("USER daytona")
    assert "dspy" not in dockerfile


@pytest.mark.asyncio
async def test_live_platform_builds_session_workspace_sdk_mount_offline() -> None:
    class _Client:
        params: Any | None = None

        async def create(self, params: Any) -> Any:
            self.params = params
            return params

    workspace_id = uuid4()
    session_id = uuid4()
    client = _Client()
    platform = LiveDaytonaPlatform(client, DaytonaSandboxSpec(DEFAULT_SNAPSHOT_NAME))

    params = await platform.create(
        volume_id="offline-test-volume",
        mount_path="/workspace",
        volume_subpath=session_workspace_volume_subpath(workspace_id, session_id),
    )

    assert params is client.params
    mount = params.volumes[0]
    assert mount.volume_id == "offline-test-volume"
    assert mount.mount_path == "/workspace"
    assert mount.subpath == session_workspace_volume_subpath(workspace_id, session_id)


def test_snapshot_provenance_is_exact() -> None:
    spec = DaytonaSandboxSpec("fleet-rlm-python313-v1")
    verify_sandbox_spec(SimpleNamespace(snapshot=spec.snapshot), spec)
    with pytest.raises(DaytonaAdapterError, match="snapshot"):
        verify_sandbox_spec(SimpleNamespace(snapshot="fleet-rlm-python313-v2"), spec)


def test_environment_profiles_keep_capacity_and_data_access_separate() -> None:
    spec = DaytonaSandboxSpec("fleet-rlm-python313-v1")
    workspace_spec = DaytonaSandboxSpec(
        "fleet-rlm-python313-v1",
        profile=DaytonaEnvironmentProfile.WORKSPACE_CHILD,
    )
    session = environment_manifest(spec, DaytonaEnvironmentProfile.SESSION)
    semantic = environment_manifest(spec, DaytonaEnvironmentProfile.SEMANTIC_CHILD)
    workspace = environment_manifest(spec, DaytonaEnvironmentProfile.WORKSPACE_CHILD)

    assert session.image_kind == workspace.image_kind == "session-analysis"
    assert session.volume_allowed and workspace.volume_allowed
    assert not session.warm_pool_eligible and not workspace.warm_pool_eligible
    assert session.resources == workspace.resources == (4, 8, 8)
    assert session.profile is DaytonaEnvironmentProfile.SESSION
    assert workspace.profile is DaytonaEnvironmentProfile.WORKSPACE_CHILD
    assert session.image_identity() == workspace.image_identity()
    assert session.digest == workspace.digest
    assert (
        session.compatible_profiles
        == workspace.compatible_profiles
        == (
            DaytonaEnvironmentProfile.SESSION,
            DaytonaEnvironmentProfile.WORKSPACE_CHILD,
        )
    )
    assert semantic.image_kind == "lean-child"
    assert semantic.dependencies == ()
    semantic_image = build_snapshot_image(
        DaytonaSandboxSpec(
            "fleet-child-test-v1", cpu=2, memory_gib=4, disk_gib=4, profile=DaytonaEnvironmentProfile.SEMANTIC_CHILD
        )
    ).dockerfile()
    assert "pip install" not in semantic_image
    assert "dspy" not in semantic_image
    assert not semantic.volume_allowed and semantic.warm_pool_eligible
    assert semantic.resources == (2, 4, 4)
    assert semantic.digest != session.digest
    assert build_snapshot_image(spec).dockerfile() == build_snapshot_image(workspace_spec).dockerfile()
