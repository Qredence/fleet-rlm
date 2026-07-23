from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.config import Settings
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.sandbox_spec import (
    BASE_IMAGE,
    DEFAULT_SNAPSHOT_NAME,
    PYTHON_VERSION,
    DaytonaSandboxSpec,
    build_snapshot_image,
    sandbox_spec_from_settings,
    verify_sandbox_spec,
)


def test_spec_requires_an_immutable_versioned_name() -> None:
    for name in ("", "latest", "fleet-rlm-python313", "fleet-rlm-python313-v0"):
        with pytest.raises(ValueError):
            DaytonaSandboxSpec(name)


def test_spec_builds_minimal_non_root_pinned_image() -> None:
    spec = DaytonaSandboxSpec("fleet-rlm-python313-v1")
    dockerfile = build_snapshot_image(spec).dockerfile()

    assert f"FROM {BASE_IMAGE}" in dockerfile
    assert "groupadd --gid 1000 daytona" in dockerfile
    assert "USER daytona" in dockerfile
    assert "PYTHONUNBUFFERED=1" in dockerfile
    assert "WORKDIR /home/daytona" in dockerfile
    assert "pip install" not in dockerfile
    assert "apt-get" not in dockerfile


def test_default_snapshot_envelope_stays_minimal_and_fixed() -> None:
    spec = DaytonaSandboxSpec(DEFAULT_SNAPSHOT_NAME)

    assert spec.snapshot == "fleet-rlm-python313-v2"
    assert spec.python_version == PYTHON_VERSION == "3.13.13"
    assert (spec.cpu, spec.memory_gib, spec.disk_gib) == (1, 1, 3)


def test_settings_require_snapshot_only_when_converted_to_daytona_spec() -> None:
    with pytest.raises(ValueError, match="FLEET_DAYTONA_SNAPSHOT"):
        sandbox_spec_from_settings(Settings(_env_file=None))
    assert (
        sandbox_spec_from_settings(Settings(_env_file=None, daytona_snapshot="fleet-test-v1")).snapshot
        == "fleet-test-v1"
    )
    with pytest.raises(ValueError, match="FLEET_DAYTONA_SNAPSHOT"):
        Settings(_env_file=None, daytona_snapshot="latest")


def test_snapshot_provenance_is_exact() -> None:
    spec = DaytonaSandboxSpec("fleet-rlm-python313-v1")
    verify_sandbox_spec(SimpleNamespace(snapshot=spec.snapshot), spec)
    with pytest.raises(DaytonaAdapterError, match="snapshot"):
        verify_sandbox_spec(SimpleNamespace(snapshot="fleet-rlm-python313-v2"), spec)
