"""Live Daytona SandboxPlatform + VolumeClient adapters (SDK only here)."""

from __future__ import annotations

import time
from typing import Any

from fleet_rlm.daytona.errors import DaytonaAdapterError, is_sandbox_not_found, map_provider_error
from fleet_rlm.daytona.sandbox_spec import DaytonaSandboxSpec
from fleet_rlm.daytona.volumes import require_scoped_volume_subpath

_VOLUME_READY_RETRY_DELAYS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
_VOLUME_FAILED_STATES = frozenset({"deleting", "deleted", "error"})


class LiveDaytonaVolumeClient:
    """Wraps ``client.volume.get(name, create=...)``."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, name: str, *, create: bool = False) -> Any:
        volume = self._client.volume.get(name, create=create)
        if not create:
            return volume

        state = _volume_state(volume)
        if state is None or state == "ready":
            return volume
        if state in _VOLUME_FAILED_STATES:
            raise DaytonaAdapterError(message="Daytona Volume did not become ready", cause_type="VolumeLifecycleError")
        for delay in _VOLUME_READY_RETRY_DELAYS:
            time.sleep(delay)
            volume = self._client.volume.get(name, create=False)
            state = _volume_state(volume)
            if state is None or state == "ready":
                return volume
            if state in _VOLUME_FAILED_STATES:
                break
        raise DaytonaAdapterError(message="Daytona Volume did not become ready", cause_type="VolumeLifecycleError")


def _volume_state(volume: Any) -> str | None:
    state = getattr(volume, "state", None)
    if state is None:
        return None
    return str(getattr(state, "value", state)).lower()


class LiveDaytonaPlatform:
    """SandboxPlatform over a Daytona SDK client."""

    def __init__(self, client: Any, sandbox_spec: DaytonaSandboxSpec) -> None:
        self._client = client
        self._sandbox_spec = sandbox_spec

    def get(self, sandbox_id: str) -> Any | None:
        """Return sandbox or ``None`` only for explicit not-found.

        Auth / network / 5xx / timeout raise typed ``ProviderRequestError``.
        """
        try:
            return self._client.get(sandbox_id)
        except Exception as exc:  # noqa: BLE001 - classify provider outcomes
            if is_sandbox_not_found(exc):
                return None
            raise map_provider_error(exc) from exc

    def create(
        self,
        *,
        volume_id: str | None = None,
        mount_path: str | None = None,
        volume_subpath: str | None = None,
        labels: dict[str, str] | None = None,
        with_volume: bool = True,
        ephemeral: bool = False,
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
            snapshot=self._sandbox_spec.snapshot,
            language="python",
            os_user="daytona",
            labels=labels or {},
            volumes=volumes,
            ephemeral=ephemeral,
        )
        return self._client.create(params)

    def delete(self, sandbox_id: Any) -> None:
        """Delete through Daytona 0.192, which requires a Sandbox object."""
        target = self._client.get(sandbox_id) if isinstance(sandbox_id, str) else sandbox_id
        self._client.delete(target)

    def start(self, sandbox_id: str) -> None:
        self._client.start(sandbox_id)

    def stop(self, sandbox_id: str, *, timeout: float = 60, force: bool = False) -> None:
        sandbox = self._client.get(sandbox_id)
        sandbox.stop(timeout=timeout, force=force)
