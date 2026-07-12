"""Volume name and mount configuration (no SDK clients at import time).

Live ``get_or_create`` is intentionally thin and injectable so unit tests never
open network connections. SessionManager owns full lifecycle and Workspace
Volume Scope isolation via VolumeMount ``subpath``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from fleet_rlm_clean.daytona.paths import (
    DEFAULT_VOLUME_MOUNT_PATH,
    VolumePaths,
    validate_mount_path,
)

DEFAULT_VOLUME_NAME = "rlm-volume-dspy"
_ZERO_UUID = UUID(int=0)


class VolumeClient(Protocol):
    """Minimal Daytona volume surface used by the clean package."""

    def get(self, name: str, *, create: bool = False) -> Any: ...


@dataclass(frozen=True, slots=True)
class VolumeConfig:
    """Server-owned volume identity for workspace sandboxes."""

    name: str = DEFAULT_VOLUME_NAME
    mount_path: str = DEFAULT_VOLUME_MOUNT_PATH

    def __post_init__(self) -> None:
        if not self.name or not str(self.name).strip():
            msg = "volume name is required"
            raise ValueError(msg)
        if any(c in self.name for c in ("/", "\\", "\x00", "..")):
            msg = "volume name must not contain path characters"
            raise ValueError(msg)
        validate_mount_path(self.mount_path)

    def paths(self) -> VolumePaths:
        return VolumePaths.from_mount(self.mount_path)


def volume_config_from_settings(settings: Any) -> VolumeConfig:
    name = getattr(settings, "volume_name", None) or DEFAULT_VOLUME_NAME
    mount = getattr(settings, "volume_mount_path", None) or DEFAULT_VOLUME_MOUNT_PATH
    return VolumeConfig(name=str(name), mount_path=str(mount))


def get_or_create_volume_id(client: VolumeClient, config: VolumeConfig) -> str:
    """Resolve a volume id via Daytona ``volume.get(name, create=True)``.

    Returns the provider id string. Does not mount; mounting is SessionManager's job.
    """
    volume = client.get(config.name, create=True)
    volume_id = getattr(volume, "id", None)
    if volume_id is None:
        msg = "volume client returned an object without id"
        raise RuntimeError(msg)
    return str(volume_id)


def require_non_zero_workspace_id(workspace_id: UUID) -> UUID:
    """Reject the zero UUID — bindings must never create/replace with it."""
    if not isinstance(workspace_id, UUID):
        msg = "workspace_id must be a UUID"
        raise TypeError(msg)
    if workspace_id == _ZERO_UUID:
        msg = "workspace_id must not be the zero UUID"
        raise ValueError(msg)
    return workspace_id


def workspace_volume_subpath(workspace_id: UUID) -> str:
    """Canonical Workspace Volume Scope subpath on the shared server Volume."""
    wid = require_non_zero_workspace_id(workspace_id)
    return f"workspaces/{wid}"


def require_scoped_volume_subpath(subpath: str, *, workspace_id: UUID | None = None) -> str:
    """Reject unscoped / empty mounts; optionally check workspace match."""
    if not isinstance(subpath, str) or not subpath.strip():
        msg = "VolumeMount without workspace subpath is rejected"
        raise ValueError(msg)
    normalized = subpath.strip().strip("/")
    if ".." in normalized.split("/") or normalized.startswith("workspaces/../"):
        msg = "volume subpath must not contain path traversal"
        raise ValueError(msg)
    if not normalized.startswith("workspaces/"):
        msg = "volume subpath must be under workspaces/<workspace_id>"
        raise ValueError(msg)
    rest = normalized.removeprefix("workspaces/")
    if not rest or "/" in rest:
        msg = "volume subpath must be exactly workspaces/<workspace_id>"
        raise ValueError(msg)
    if workspace_id is not None:
        expected = workspace_volume_subpath(workspace_id)
        if normalized != expected:
            msg = "volume subpath does not match workspace_id"
            raise ValueError(msg)
    return normalized


def volume_mount_spec(
    config: VolumeConfig,
    volume_id: str,
    *,
    workspace_id: UUID,
) -> dict[str, str]:
    """Dict shape suitable for constructing a Daytona VolumeMount.

    Keys align with SDK ``VolumeMount(volume_id=..., mount_path=..., subpath=...)``.
    Full shared Volume mounts without workspace subpath are rejected.
    """
    if not volume_id or not str(volume_id).strip():
        msg = "volume_id is required"
        raise ValueError(msg)
    validate_mount_path(config.mount_path)
    subpath = workspace_volume_subpath(workspace_id)
    return {
        "volume_id": str(volume_id),
        "mount_path": config.mount_path,
        "subpath": subpath,
    }
