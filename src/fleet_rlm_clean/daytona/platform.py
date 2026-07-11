"""Live Daytona SandboxPlatform + VolumeClient adapters (SDK only here)."""

from __future__ import annotations

from typing import Any


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
        volume_id: str,
        mount_path: str,
        labels: dict[str, str] | None = None,
    ) -> Any:
        from daytona import CreateSandboxFromSnapshotParams, VolumeMount

        params = CreateSandboxFromSnapshotParams(
            language="python",
            labels=labels or {},
            volumes=[VolumeMount(volume_id=volume_id, mount_path=mount_path)],
        )
        return self._client.create(params)

    def delete(self, sandbox_id: str) -> None:
        self._client.delete(sandbox_id)

    def start(self, sandbox_id: str) -> None:
        self._client.start(sandbox_id)

    def stop(self, sandbox_id: str) -> None:
        self._client.stop(sandbox_id)
