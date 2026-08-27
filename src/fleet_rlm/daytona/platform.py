"""Live Daytona SandboxPlatform + VolumeClient adapters (SDK only here)."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fleet_rlm.config import Settings
from fleet_rlm.daytona.errors import DaytonaAdapterError, is_sandbox_not_found, map_provider_error
from fleet_rlm.daytona.provisioning import DaytonaSandboxSpec, require_volume_mount_subpath

_VOLUME_READY_RETRY_DELAYS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
_VOLUME_FAILED_STATES = frozenset({"deleting", "deleted", "error"})
_DAYTONA_CLOUD_API_URL = "https://app.daytona.io/api"
ProviderState = Literal[
    "missing",
    "running",
    "stopped",
    "paused",
    "archived",
    "unrecoverable",
]
_RUNNING_STATES = frozenset({"running", "started", "active"})
_STOPPED_STATES = frozenset({"stopped", "stop"})
_PAUSED_STATES = frozenset({"paused", "pause"})
_ARCHIVED_STATES = frozenset({"archived", "archive"})


def build_daytona_client(settings: Settings) -> Any:
    """Construct the asynchronous Daytona SDK client with the configured endpoint and credentials.

    Parameters:
        settings (Settings): Configuration containing the optional Daytona API key and organization ID.

    Returns:
        Any: The configured asynchronous Daytona client.
    """
    from daytona import AsyncDaytona, DaytonaConfig

    api_key = None
    if settings.daytona_api_key is not None:
        raw = settings.daytona_api_key
        api_key = raw.get_secret_value() if hasattr(raw, "get_secret_value") else str(raw)
        api_key = api_key or None
    # Pass the current SDK field explicitly.  Leaving this unset makes Daytona
    # 0.202.0 evaluate the deprecated ``server_url`` fallback, and would also
    # allow ambient SDK endpoint discovery to bypass Fleet's configuration.
    config_kwargs: dict[str, Any] = {"api_url": _DAYTONA_CLOUD_API_URL}
    if api_key:
        config_kwargs["api_key"] = api_key
    if settings.daytona_org_id:
        config_kwargs["organization_id"] = settings.daytona_org_id
    config = DaytonaConfig(**config_kwargs) if config_kwargs else None
    client = AsyncDaytona(config)
    # The SDK only sets X-Daytona-Organization-ID for JWT auth; also set it for
    # API-key auth so the org routing is respected.
    if settings.daytona_org_id and api_key:
        client._api_client.default_headers["X-Daytona-Organization-ID"] = settings.daytona_org_id
    return client


def normalize_state(raw: Any) -> ProviderState:
    """Normalize provider-specific states at the provider adapter boundary."""
    if raw is None:
        return "missing"
    text = str(getattr(raw, "value", raw)).strip().lower()
    if text in _RUNNING_STATES:
        return "running"
    if text in _STOPPED_STATES:
        return "stopped"
    if text in _PAUSED_STATES:
        return "paused"
    if text in _ARCHIVED_STATES:
        return "archived"
    if text in {"missing", "deleted", ""}:
        return "missing"
    return "unrecoverable"


def sandbox_state(sandbox: Any) -> ProviderState:
    raw = getattr(sandbox, "state", None)
    if raw is None:
        raw = getattr(sandbox, "status", None)
    return normalize_state(raw)


class LiveDaytonaVolumeClient:
    """Wraps ``client.volume.get(name, create=...)``."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def get(self, name: str, *, create: bool = False) -> Any:
        volume = await self._client.volume.get(name, create=create)
        if not create:
            return volume

        state = _volume_state(volume)
        if state is None or state == "ready":
            return volume
        if state in _VOLUME_FAILED_STATES:
            raise DaytonaAdapterError(message="Daytona Volume did not become ready", cause_type="VolumeLifecycleError")
        for delay in _VOLUME_READY_RETRY_DELAYS:
            await asyncio.sleep(delay)
            volume = await self._client.volume.get(name, create=False)
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

    async def get(self, sandbox_id: str) -> Any | None:
        """Return sandbox or ``None`` only for explicit not-found.

        Auth / network / 5xx / timeout raise typed ``ProviderRequestError``.
        """
        try:
            return await self._client.get(sandbox_id)
        except Exception as exc:
            if is_sandbox_not_found(exc):
                return None
            raise map_provider_error(exc) from exc

    async def create(
        self,
        *,
        volume_id: str | None = None,
        mount_path: str | None = None,
        volume_subpath: str | None = None,
        labels: dict[str, str] | None = None,
        with_volume: bool = True,
        ephemeral: bool = False,
        network_block_all: bool = False,
        network_allow_list: str | None = None,
        domain_allow_list: str | None = None,
        auto_stop_interval: int | None = None,
        auto_delete_interval: int | None = None,
    ) -> Any:
        """
        Create a Daytona sandbox from the configured snapshot.

        Parameters:
            volume_id (str | None): Volume identifier required when `with_volume` is true.
            mount_path (str | None): Sandbox mount path required when `with_volume` is true.
            volume_subpath (str | None): Scoped subpath within the volume.
            labels (dict[str, str] | None): Labels to assign to the sandbox.
            with_volume (bool): Whether to attach a volume.
            ephemeral (bool): Whether to create an ephemeral sandbox.
            network_block_all (bool): Whether to block all network access.
            network_allow_list (str | None): Comma-separated network allow-list.
            domain_allow_list (str | None): Comma-separated domain allow-list.
            auto_stop_interval (int | None): Automatic stop interval.
            auto_delete_interval (int | None): Automatic deletion interval.

        Returns:
            Any: The created sandbox.

        Raises:
            ValueError: If `with_volume` is true and `volume_id` or `mount_path` is missing.
        """
        from daytona import CreateSandboxFromSnapshotParams, VolumeMount

        volumes = None
        if with_volume:
            if not volume_id or not mount_path:
                msg = "volume_id and mount_path are required when with_volume=True"
                raise ValueError(msg)
            scoped = require_volume_mount_subpath(volume_subpath or "")
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
            network_block_all=network_block_all,
            network_allow_list=network_allow_list,
            domain_allow_list=domain_allow_list,
            auto_stop_interval=auto_stop_interval,
            auto_delete_interval=auto_delete_interval,
        )
        return await self._client.create(params)

    async def delete(self, sandbox_id: Any) -> None:
        """Delete through Daytona's async client, treating absence as success."""
        try:
            target = await self._client.get(sandbox_id) if isinstance(sandbox_id, str) else sandbox_id
        except Exception as exc:
            if is_sandbox_not_found(exc):
                return
            raise map_provider_error(exc) from exc
        try:
            await self._client.delete(target)
        except Exception as exc:
            if is_sandbox_not_found(exc):
                return
            raise map_provider_error(exc) from exc

    async def start(self, sandbox_id: str) -> None:
        """Start the specified sandbox.

        Parameters:
                sandbox_id (str): The identifier of the sandbox to start.
        """
        sandbox = await self._client.get(sandbox_id)
        await self._client.start(sandbox)

    async def stop(self, sandbox_id: str, *, timeout: float = 60, force: bool = False) -> None:
        """
        Stop a sandbox, optionally deleting it if stopping fails.

        Parameters:
            sandbox_id (str): Identifier of the sandbox to stop.
            timeout (float): Maximum time to wait for the stop operation, in seconds.
            force (bool): Whether to delete the sandbox if stopping fails.

        A missing sandbox is treated as already stopped.
        """
        try:
            sandbox = await self._client.get(sandbox_id)
        except Exception as exc:
            if is_sandbox_not_found(exc):
                return
            raise
        try:
            await self._client.stop(sandbox, timeout=timeout)
        except Exception:
            if force:
                await self._client.delete(sandbox)
            else:
                raise
