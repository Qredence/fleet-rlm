"""Live Daytona SandboxPlatform + VolumeClient adapters (SDK only here)."""

from __future__ import annotations

from typing import Any

from fleet_rlm_clean.daytona.volumes import require_scoped_volume_subpath


class LiveDaytonaVolumeClient:
    """Wraps ``client.volume.get(name, create=...)``."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, name: str, *, create: bool = False) -> Any:
        return self._client.volume.get(name, create=create)


class LiveDaytonaPlatform:
    """SandboxPlatform over a Daytona SDK client."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, sandbox_id: str) -> Any | None:
        try:
            return self._client.get(sandbox_id)
        except Exception:  # noqa: BLE001 - missing sandbox is a None binding
            return None

    def create(
        self,
        *,
        volume_id: str | None = None,
        mount_path: str | None = None,
        volume_subpath: str | None = None,
        labels: dict[str, str] | None = None,
        with_volume: bool = True,
    ) -> Any:
        from daytona import CreateSandboxFromSnapshotParams, VolumeMount

        volumes = None
        if with_volume:
            if not volume_id or not mount_path:
                msg = "volume_id and mount_path are required when with_volume=True"
                raise ValueError(msg)
            scoped = require_scoped_volume_subpath(volume_subpath or "")
            volumes = [
                VolumeMount(
                    volume_id=volume_id,
                    mount_path=mount_path,
                    subpath=scoped,
                )
            ]
        params = CreateSandboxFromSnapshotParams(
            language="python",
            labels=labels or {},
            volumes=volumes,
            ephemeral=True,
        )
        return self._client.create(params)

    def delete(self, sandbox_id: str) -> None:
        self._client.delete(sandbox_id)

    def start(self, sandbox_id: str) -> None:
        self._client.start(sandbox_id)

    def stop(self, sandbox_id: str) -> None:
        self._client.stop(sandbox_id)
