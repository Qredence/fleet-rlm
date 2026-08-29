from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.provisioning import (
    BASE_IMAGE,
    DEFAULT_SNAPSHOT_NAME,
    PYTHON_VERSION,
    DaytonaSandboxSpec,
    build_snapshot_image,
    sandbox_spec_from_settings,
    snapshot_dependency_import_names,
    snapshot_dependency_sha256,
    snapshot_execution_dependencies,
    verify_sandbox_spec,
)


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
    assert "dspy" not in dockerfile.lower()


def test_default_snapshot_envelope_stays_fixed() -> None:
    spec = DaytonaSandboxSpec(DEFAULT_SNAPSHOT_NAME)

    assert spec.snapshot == "fleet-rlm-python313-v5"
    assert spec.python_version == PYTHON_VERSION == "3.13.13"
    assert (spec.cpu, spec.memory_gib, spec.disk_gib) == (2, 4, 8)


def test_dependency_import_names_map_distribution_to_module() -> None:
    assert snapshot_dependency_import_names() == (
        ("mpmath", "mpmath", "1.4.1"),
        ("numpy", "numpy", "2.5.1"),
        ("pandas", "pandas", "3.0.5"),
        ("beautifulsoup4", "bs4", "4.15.0"),
    )


def test_settings_require_snapshot_only_when_converted_to_daytona_spec() -> None:
    with pytest.raises(ValueError, match="FLEET_DAYTONA_SNAPSHOT"):
        sandbox_spec_from_settings(Settings())
    assert sandbox_spec_from_settings(Settings(daytona_snapshot="fleet-test-v1")).snapshot == "fleet-test-v1"
    with pytest.raises(ValueError, match="FLEET_DAYTONA_SNAPSHOT"):
        Settings(daytona_snapshot="latest")


def test_snapshot_provenance_is_exact() -> None:
    spec = DaytonaSandboxSpec("fleet-rlm-python313-v1")
    verify_sandbox_spec(SimpleNamespace(snapshot=spec.snapshot), spec)
    with pytest.raises(DaytonaAdapterError, match="snapshot"):
        verify_sandbox_spec(SimpleNamespace(snapshot="fleet-rlm-python313-v2"), spec)
