"""Concrete Daytona SDK operations and sandbox file access."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    is_sandbox_not_found,
    map_provider_error,
)
from fleet_rlm.daytona.interpreter import (
    DEFAULT_EXECUTION_OUTPUT_CHARS,
    DaytonaCodeInterpreter,
    SyncBridgeDispatcher,
    sandbox_backend,
    sandbox_interpreter_factory,
)
from fleet_rlm.daytona.specs import (
    VOLUME_FAILED_STATES,
    VOLUME_READY_RETRY_DELAYS,
    DaytonaEnvironmentProfile,
    DaytonaSandboxSpec,
    _create_daytona_sandbox,
    _expected_workspace_mount,
    ensure_volume_layout,
    get_or_create_volume_id,
    require_volume_mount_subpath,
    sandbox_spec_from_settings,
    sandbox_state,
    verify_sandbox_spec,
    verify_sandbox_workspace_mount,
    volume_config_from_settings,
)
from fleet_rlm.paths import VolumePaths

if TYPE_CHECKING:
    from daytona import AsyncDaytona

    from fleet_rlm.config.settings import Settings


_VOLUME_FAILED_STATES = VOLUME_FAILED_STATES

_VOLUME_READY_RETRY_DELAYS = VOLUME_READY_RETRY_DELAYS


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
        if state in _VOLUME_FAILED_STATES:
            raise DaytonaAdapterError(message="Daytona Volume did not become ready", cause_type="VolumeLifecycleError")
        for delay in _VOLUME_READY_RETRY_DELAYS:
            await asyncio.sleep(delay)
            volume = await self._get_existing(name)
            state = _volume_state(volume)
            if state is None or state == "ready":
                return volume
            if state in _VOLUME_FAILED_STATES:
                break
        raise DaytonaAdapterError(message="Daytona Volume did not become ready", cause_type="VolumeLifecycleError")

    async def _get_existing(self, name: str) -> Any:
        try:
            return await self._client.volume.get(name, create=False)
        except Exception as exc:
            raise map_provider_error(exc) from exc


def _volume_state(volume: Any) -> str | None:
    state = getattr(volume, "state", None)
    if state is None:
        return None
    return str(getattr(state, "value", state)).lower()


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
    ) -> Any:
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
        )
        try:
            return await self._client.create(params)
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

    async def start(self, sandbox_id: str) -> None:
        try:
            sandbox = await self._client.get(sandbox_id)
            await self._client.start(sandbox)
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


@dataclass(frozen=True, slots=True)
class EphemeralInterpreterLease:
    """Caller-owned ephemeral volume sandbox and interpreter for operator scripts."""

    interpreter: Any
    sandbox: Any
    platform: Any
    session_id: UUID
    run_id: UUID
    workspace_id: UUID
    context_mount_path: str
    volume_paths: VolumePaths


async def _retire_failed_ephemeral_sandbox(
    platform: Any,
    sandbox: Any,
    *,
    interpreter: Any | None = None,
) -> None:
    """Delete one ephemeral sandbox after lease construction fails."""
    if interpreter is not None:
        shutdown = getattr(interpreter, "shutdown", None)
        if callable(shutdown):
            with contextlib.suppress(BaseException):
                await asyncio.to_thread(shutdown)
    await platform.delete(sandbox)


async def acquire_ephemeral_interpreter(
    settings: Any,
    *,
    purpose: str,
    workspace_id: UUID | None = None,
) -> EphemeralInterpreterLease:
    """Acquire one ephemeral volume-backed interpreter for an operator script."""
    client = build_daytona_client(settings)
    spec = sandbox_spec_from_settings(settings)
    platform = LiveDaytonaPlatform(client, spec)
    volume_client = LiveDaytonaVolumeClient(client)
    volume_config = volume_config_from_settings(settings)
    resolved_workspace = workspace_id or uuid4()
    volume_id = await get_or_create_volume_id(volume_client, volume_config)
    expected = _expected_workspace_mount(volume_config, volume_id, resolved_workspace)
    sandbox = await _create_daytona_sandbox(
        platform,
        expected,
        labels={
            "fleet-package": "fleet_rlm",
            "purpose": purpose,
            "workspace_id": str(resolved_workspace),
        },
        ephemeral=True,
    )
    interpreter: Any | None = None
    try:
        if sandbox_state(sandbox) != "running":
            await platform.start(str(sandbox.id))
            refreshed = await platform.get(str(sandbox.id))
            if refreshed is None or sandbox_state(refreshed) != "running":
                raise DaytonaAdapterError(
                    message="sandbox did not reach running state",
                    cause_type="SandboxLifecycleError",
                )
            sandbox = refreshed
        session_id = uuid4()
        run_id = uuid4()
        verify_sandbox_workspace_mount(sandbox, expected)
        verify_sandbox_spec(sandbox, spec)
        await ensure_volume_layout(
            sandbox,
            volume_config.paths(),
            session_id=session_id,
            run_id=run_id,
        )
        loop = asyncio.get_running_loop()
        interpreter = DaytonaCodeInterpreter(
            backend=sandbox_backend(
                sandbox,
                loop=loop,
            )
        )
    except BaseException:
        await _retire_failed_ephemeral_sandbox(platform, sandbox, interpreter=interpreter)
        raise
    volume_paths = volume_config.paths()
    return EphemeralInterpreterLease(
        interpreter=interpreter,
        sandbox=sandbox,
        platform=platform,
        session_id=session_id,
        run_id=run_id,
        workspace_id=resolved_workspace,
        context_mount_path=str(volume_paths.mount_path),
        volume_paths=volume_paths,
    )


def build_daytona_client(settings: Settings) -> AsyncDaytona:
    """Construct the process-owned asynchronous Daytona SDK client."""
    from daytona import AsyncDaytona, DaytonaConfig

    api_key: str | None = None
    if settings.daytona_api_key is not None:
        api_key = settings.daytona_api_key.get_secret_value()
        api_key = api_key or None
    config_kwargs: dict[str, Any] = {"api_url": _DAYTONA_CLOUD_API_URL}
    if api_key:
        config_kwargs["api_key"] = api_key
    if settings.daytona_org_id:
        config_kwargs["organization_id"] = settings.daytona_org_id
    client = AsyncDaytona(DaytonaConfig(**config_kwargs))
    if settings.daytona_org_id and api_key and hasattr(client, "_api_client"):
        client._api_client.default_headers["X-Daytona-Organization-ID"] = settings.daytona_org_id
    return client


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


def _build_interpreter_factory(
    sandbox: Any,
    *,
    loop: asyncio.AbstractEventLoop,
    dispatcher: SyncBridgeDispatcher | None = None,
    execution_output_cap: int = DEFAULT_EXECUTION_OUTPUT_CHARS,
) -> Any:
    return sandbox_interpreter_factory(
        sandbox,
        loop=loop,
        dispatcher=dispatcher,
        execution_output_cap=execution_output_cap,
    )


_DAYTONA_CLOUD_API_URL = "https://app.daytona.io/api"


def remove_run_scratch(sandbox: Any, path: str) -> None:
    """Remove invocation scratch through the worker's synchronous storage bridge."""
    from daytona.common.errors import DaytonaFileNotFoundError

    with contextlib.suppress(DaytonaFileNotFoundError):
        sandbox.fs.delete_file(path, recursive=True)
