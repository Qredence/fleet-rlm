"""Volume name and mount configuration (no SDK clients at import time).

Live ``get_or_create`` is intentionally thin and injectable so unit tests never
open network connections. SessionManager (impl-08) owns full lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from fleet_rlm_clean.daytona.paths import (
    DEFAULT_VOLUME_MOUNT_PATH,
    VolumePaths,
    validate_mount_path,
)

DEFAULT_VOLUME_NAME = "rlm-volume-dspy"


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


def volume_mount_spec(config: VolumeConfig, volume_id: str) -> dict[str, str]:
    """Dict shape suitable for constructing a Daytona VolumeMount later.

    Keys align with SDK ``VolumeMount(volume_id=..., mount_path=...)``.
    Subpath isolation (per workspace) can be added when SessionManager lands.
    """
    validate_mount_path(config.mount_path)
    return {
        "volume_id": volume_id,
        "mount_path": config.mount_path,
    }
