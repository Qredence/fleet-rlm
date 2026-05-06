from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from fleet_rlm.integrations.daytona.runtime import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    DaytonaSandboxRuntime,
)
from fleet_rlm.integrations.daytona.sandbox_spec import (
    DEFAULT_SANDBOX_LABELS,
    build_sandbox_spec,
    default_sandbox_name,
    merge_sandbox_labels,
)


def _make_runtime() -> DaytonaSandboxRuntime:
    return DaytonaSandboxRuntime(config=SimpleNamespace(api_key="key", api_url="https://api.daytona.test", target=None))


def test_default_sandbox_name_uses_utc_timestamp() -> None:
    name = default_sandbox_name(now=dt.datetime(2026, 5, 3, 12, 34, 56, tzinfo=dt.timezone.utc))

    assert name == "fleet-rlm-20260503-123456"


def test_merge_sandbox_labels_overlays_defaults() -> None:
    defaults = {"managed-by": "fleet-rlm", "env": "default"}

    merged = merge_sandbox_labels(
        default_labels=defaults,
        labels={"env": "custom", "team": "agent"},
    )

    assert merged == {
        "managed-by": "fleet-rlm",
        "env": "custom",
        "team": "agent",
    }
    assert defaults == {"managed-by": "fleet-rlm", "env": "default"}


def test_build_sandbox_spec_applies_defaults() -> None:
    spec = build_sandbox_spec(default_labels=DEFAULT_SANDBOX_LABELS, volume_name="tenant-a")

    assert spec.name is not None
    assert spec.name.startswith("fleet-rlm-")
    assert spec.language == "python"
    assert spec.snapshot == "fleet-rlm-base"
    assert spec.volume_name == "tenant-a"
    assert spec.volume_mount_path == str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
    assert spec.labels == {"managed-by": "fleet-rlm"}
    assert spec.auto_stop_interval == 30
    assert spec.auto_archive_interval == 60
    assert spec.auto_delete_interval is None


def test_build_sandbox_spec_skips_default_snapshot_when_image_present() -> None:
    image = object()

    spec = build_sandbox_spec(default_labels=DEFAULT_SANDBOX_LABELS, image=image)

    assert spec.image is image
    assert spec.snapshot is None


def test_runtime_build_sandbox_spec_preserves_overrides() -> None:
    runtime = _make_runtime()

    spec = runtime.build_sandbox_spec(
        name="custom-sandbox",
        volume_name="tenant-a",
        volume_subpath="jobs/42",
        snapshot="snapshot-123",
        env_vars={"A": "1"},
        labels={"team": "agent"},
        cpu=2,
        memory=4,
        disk=8,
        auto_stop_interval=15,
        auto_archive_interval=45,
        auto_delete_interval=120,
        network_block_all=True,
        network_allow_list="10.0.0.0/8",
    )

    assert spec.name == "custom-sandbox"
    assert spec.snapshot == "snapshot-123"
    assert spec.volume_name == "tenant-a"
    assert spec.volume_subpath == "jobs/42"
    assert spec.volume_mount_path == str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
    assert spec.env_vars == {"A": "1"}
    assert spec.labels == {"managed-by": "fleet-rlm", "team": "agent"}
    assert spec.cpu == 2
    assert spec.memory == 4
    assert spec.disk == 8
    assert spec.auto_stop_interval == 15
    assert spec.auto_archive_interval == 45
    assert spec.auto_delete_interval == 120
    assert spec.network_block_all is True
    assert spec.network_allow_list == "10.0.0.0/8"
