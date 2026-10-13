"""Concrete Daytona SDK operations and sandbox file access."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    is_sandbox_not_found,
    map_provider_error,
)
from fleet_rlm.daytona.specs import (
    VOLUME_FAILED_STATES,
    VOLUME_READY_RETRY_DELAYS,
    DaytonaEnvironmentProfile,
    DaytonaSandboxSpec,
    require_volume_mount_subpath,
    state_text,
)

if TYPE_CHECKING:
    from daytona import AsyncDaytona

    from fleet_rlm.config.settings import Settings


class LiveDaytonaVolumeClient:
    """Wraps ``client.volume.get(name, create=...)``."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def get(self, name: str, *, create: bool = False) -> Any:
        from daytona.common.errors import DaytonaBadRequestError, DaytonaConflictError, DaytonaInternalServerError

        try:
            volume = await self._client.volume.get(name, create=create)
        except (DaytonaConflictError, DaytonaBadRequestError, DaytonaInternalServerError) as exc:
            if not create:
                raise map_provider_error(exc) from exc
            # The SDK creates only after typed absence. A concurrent creator
            # may win that race, and the provider reports the losing create as
            # 409, 400 "already exists" or 500. Reconcile by one lookup and
            # never repeat creation; without a winner, the create error stands.
            try:
                volume = await self._client.volume.get(name, create=False)
            except Exception:
                raise map_provider_error(exc) from exc
        except Exception as exc:
            raise map_provider_error(exc) from exc
        if not create:
            return volume

        state = _volume_state(volume)
        if state is None or state == "ready":
            return volume
        if state in VOLUME_FAILED_STATES:
            raise DaytonaAdapterError(message="Daytona Volume did not become ready", cause_type="VolumeLifecycleError")
        for delay in VOLUME_READY_RETRY_DELAYS:
            await asyncio.sleep(delay)
            volume = await self._get_existing(name)
            state = _volume_state(volume)
            if state is None or state == "ready":
                return volume
            if state in VOLUME_FAILED_STATES:
                break
        raise DaytonaAdapterError(message="Daytona Volume did not become ready", cause_type="VolumeLifecycleError")

    async def _get_existing(self, name: str) -> Any:
        try:
            return await self._client.volume.get(name, create=False)
        except Exception as exc:
            raise map_provider_error(exc) from exc


def _sdk_timeout(seconds: float) -> float:
    """Map remaining caller time to an SDK timeout; the SDK treats 0 as unlimited."""
    return max(1.0, float(seconds))


def _volume_state(volume: Any) -> str | None:
    state = getattr(volume, "state", None)
    if state is None:
        return None
    return state_text(state)


class LiveDaytonaPlatform:
    """SandboxPlatform over a Daytona SDK client."""

    def __init__(
        self,
        client: Any,
        sandbox_spec: DaytonaSandboxSpec,
        environment_specs: dict[DaytonaEnvironmentProfile, DaytonaSandboxSpec] | None = None,
    ) -> None:
        self._client = client
        self._sandbox_spec = sandbox_spec
        self._sandbox_specs = dict(environment_specs or {})
        self._sandbox_specs.setdefault(sandbox_spec.profile, sandbox_spec)

    def spec_for_profile(self, profile: DaytonaEnvironmentProfile) -> DaytonaSandboxSpec:
        """Return the immutable snapshot/resource contract for ``profile``."""
        try:
            return self._sandbox_specs[profile]
        except KeyError as exc:
            raise ValueError(f"Daytona environment profile is unavailable: {profile.value}") from exc

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
        profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.SESSION,
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
        ttl_minutes: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Create from the profile snapshot; ``timeout`` replaces the SDK's 60-second default."""
        from daytona import CreateSandboxFromSnapshotParams, VolumeMount

        spec = self.spec_for_profile(profile)
        if profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD:
            if volume_id or mount_path or volume_subpath:
                raise ValueError("SemanticChild sandboxes cannot mount a Workspace Volume")
            with_volume = False
            network_block_all = True

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
        effective_labels = dict(labels or {})
        if profile is not DaytonaEnvironmentProfile.SESSION:
            effective_labels.setdefault("fleet.profile", profile.value)
        params = CreateSandboxFromSnapshotParams(
            snapshot=spec.snapshot,
            language="python",
            os_user="daytona",
            labels=effective_labels,
            volumes=volumes,
            ephemeral=ephemeral,
            network_block_all=network_block_all,
            network_allow_list=network_allow_list,
            domain_allow_list=domain_allow_list,
            auto_stop_interval=auto_stop_interval,
            auto_delete_interval=auto_delete_interval,
            ttl_minutes=ttl_minutes,
        )
        try:
            if timeout is None:
                return await self._client.create(params)
            return await self._client.create(params, timeout=_sdk_timeout(timeout))
        except Exception as exc:
            raise map_provider_error(exc) from exc

    async def delete(self, sandbox_id: Any, *, wait: bool = True, timeout: float = 60) -> None:
        """Delete through Daytona's async client, optionally confirming destruction."""
        try:
            target = await self._client.get(sandbox_id) if isinstance(sandbox_id, str) else sandbox_id
        except Exception as exc:
            if is_sandbox_not_found(exc):
                return
            raise map_provider_error(exc) from exc
        try:
            await self._client.delete(target, wait=wait, timeout=timeout)
        except Exception as exc:
            if is_sandbox_not_found(exc):
                return
            raise map_provider_error(exc) from exc

    async def start(self, sandbox_id: str, *, timeout: float | None = None) -> None:
        try:
            sandbox = await self._client.get(sandbox_id)
            if timeout is None:
                await self._client.start(sandbox)
            else:
                await self._client.start(sandbox, timeout=_sdk_timeout(timeout))
        except Exception as exc:
            if is_sandbox_not_found(exc):
                return
            raise map_provider_error(exc) from exc

    async def stop(self, sandbox_id: str, *, timeout: float = 60, force: bool = False) -> None:
        try:
            sandbox = await self._client.get(sandbox_id)
        except Exception as exc:
            if is_sandbox_not_found(exc):
                return
            raise map_provider_error(exc) from exc
        try:
            await sandbox.stop(timeout=timeout, force=force)
        except Exception as exc:
            if is_sandbox_not_found(exc):
                return
            raise map_provider_error(exc) from exc


def daytona_config(settings: Settings) -> Any:
    """Share explicit host configuration between both official SDK clients."""
    from daytona import DaytonaConfig

    kwargs: dict[str, Any] = {"api_url": _DAYTONA_CLOUD_API_URL}
    if settings.daytona_api_key is not None:
        kwargs["api_key"] = settings.daytona_api_key.get_secret_value() or None
    if settings.daytona_org_id:
        kwargs["organization_id"] = settings.daytona_org_id
    return DaytonaConfig(**kwargs)


def build_daytona_client(settings: Settings) -> AsyncDaytona:
    from daytona import AsyncDaytona

    client = AsyncDaytona(daytona_config(settings))
    if settings.daytona_org_id:
        client._api_client.default_headers["X-Daytona-Organization-ID"] = settings.daytona_org_id
    return client


def build_sync_daytona_client(settings: Settings) -> Any:
    from daytona import Daytona

    client = Daytona(daytona_config(settings))
    if settings.daytona_org_id:
        client._api_client.default_headers["X-Daytona-Organization-ID"] = settings.daytona_org_id
    return client


def close_sync_daytona_client(client: Any) -> None:
    """Close pinned SDK resources: 0.220 has no public synchronous close()."""
    # This compatibility seam only owns shutdown; all provider operations use
    # the SDK. Keep it until Daytona publishes a synchronous close method.
    operations = [
        client._subscription_manager.shutdown,
        client._http_client.close,
        client._api_client.rest_client.pool_manager.clear,
        client._toolbox_api_client.rest_client.pool_manager.clear,
    ]
    if client._event_dispatcher is not None:
        operations.append(client._event_dispatcher.disconnect)
    if client._tracer_provider is not None:
        operations.append(client._tracer_provider.shutdown)
    failure: Exception | None = None
    for operation in operations:
        try:
            operation()
        except Exception as exc:
            failure = failure or exc
    if failure is not None:
        raise failure


async def read_file(sandbox: Any, path: str) -> bytes:
    data = await sandbox.fs.download_file(path)
    return data.encode("utf-8") if isinstance(data, str) else bytes(data)


async def write_file(sandbox: Any, path: str, data: bytes) -> None:
    await sandbox.fs.upload_file(data, path)


async def list_files(sandbox: Any, path: str, *, depth: int | None = 1) -> list[Any]:
    return await sandbox.fs.list_files(path, depth=depth)


async def delete_file(sandbox: Any, path: str) -> None:
    await sandbox.fs.delete_file(path)


async def get_file_info(sandbox: Any, path: str) -> Any:
    return await sandbox.fs.get_file_info(path)


async def create_folder(sandbox: Any, path: str, mode: str = "755") -> None:
    await sandbox.fs.create_folder(path, mode=mode)


_DAYTONA_CLOUD_API_URL = "https://app.daytona.io/api"
