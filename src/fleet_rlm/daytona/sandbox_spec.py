"""Immutable Daytona Snapshot contract for Fleet-created Sandboxes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.snapshot_contract import validate_snapshot_name

DEFAULT_SNAPSHOT_NAME = "fleet-rlm-python313-v2"
PYTHON_VERSION = "3.13.13"
BASE_IMAGE = "python:3.13.13-slim-bookworm@sha256:f576b530293e74140ea91d262232648d5c4f45640a95ec447757701bfcacf034"


@dataclass(frozen=True, slots=True)
class DaytonaSandboxSpec:
    """The immutable image and resource contract for Fleet Daytona Sandboxes."""

    snapshot: str
    python_version: str = PYTHON_VERSION
    base_image: str = BASE_IMAGE
    cpu: int = 1
    memory_gib: int = 1
    disk_gib: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot", validate_snapshot_name(self.snapshot))
        if (self.cpu, self.memory_gib, self.disk_gib) != (1, 1, 3):
            raise ValueError("Fleet Daytona snapshot resources must be 1 CPU, 1 GiB memory, and 3 GiB disk")


def sandbox_spec_from_settings(settings: Any) -> DaytonaSandboxSpec:
    """Resolve the required server-owned snapshot setting without exposing secrets."""
    value = getattr(settings, "daytona_snapshot", None)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("FLEET_DAYTONA_SNAPSHOT is required")
    return DaytonaSandboxSpec(snapshot=value.strip())


def build_snapshot_image(spec: DaytonaSandboxSpec) -> Any:
    """Build the minimal non-root image definition without package installation."""
    from daytona import Image

    return (
        Image.base(spec.base_image)
        .run_commands(
            "groupadd --gid 1000 daytona",
            "useradd --uid 1000 --gid daytona --create-home --home-dir /home/daytona --shell /bin/bash daytona",
            "chown -R daytona:daytona /home/daytona",
        )
        .env({"PYTHONUNBUFFERED": "1"})
        .workdir("/home/daytona")
        .dockerfile_commands(["USER daytona"])
    )


def verify_sandbox_spec(sandbox: Any, spec: DaytonaSandboxSpec) -> None:
    """Require provider-reported snapshot provenance before Fleet reuses a Sandbox."""
    actual = getattr(sandbox, "snapshot", None)
    if str(actual or "").strip() != spec.snapshot:
        raise DaytonaAdapterError(
            message="sandbox snapshot does not match configured Fleet snapshot",
            cause_type="SandboxSnapshotMismatch",
        )


__all__ = [
    "BASE_IMAGE",
    "DEFAULT_SNAPSHOT_NAME",
    "PYTHON_VERSION",
    "DaytonaSandboxSpec",
    "build_snapshot_image",
    "sandbox_spec_from_settings",
    "validate_snapshot_name",
    "verify_sandbox_spec",
]
