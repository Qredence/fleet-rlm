"""Public Daytona runtime boundary for reusable roots and disposable children.

DaytonaRuntime manages session-scoped reusable root Sandboxes and ephemeral
disposable child Environments for recursive execution.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterable, Mapping, Sequence
from concurrent.futures import Future, wait
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from threading import Condition, Lock, get_ident
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias, cast
from uuid import UUID, uuid4

from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    ProviderRequestError,
    is_safe_pre_creation_retry,
    is_sandbox_not_found,
    map_provider_error,
    sanitize_failure_text,
)
from fleet_rlm.daytona.interpreter import (
    DEFAULT_EXECUTION_OUTPUT_CHARS,
    DEFAULT_EXECUTION_TIMEOUT_S,
    DaytonaCodeInterpreter,
    SyncBridgeDispatcher,
    sandbox_backend,
)
from fleet_rlm.paths import DEFAULT_VOLUME_MOUNT_PATH, VolumePaths, validate_mount_path
from fleet_rlm.rlm.ownership import OwnedEffect, RunCleanupSupervisor
from fleet_rlm.rlm.recursion import (
    ChildRuntimeAuthorizationError,
    ChildRuntimeCleanupError,
    ChildRuntimeFactory,
    ChildRuntimeNotStartedError,
)
from fleet_rlm.sessions.bindings import (
    BindingGenerationAuthority,
    SandboxBinding,
    require_non_zero_workspace_id,
    require_scoped_volume_subpath,
    require_session_workspace_subpath,
    session_workspace_volume_subpath,
    workspace_volume_subpath,
)
from fleet_rlm.snapshot_contract import validate_snapshot_name

if TYPE_CHECKING:
    from daytona import AsyncDaytona

    from fleet_rlm.config.settings import Settings


PREWARM_RUN_ID = UUID("00000000-0000-4000-8000-000000000000")
DEFAULT_IDLE_STOP_SECONDS = 300.0
_WORKSPACE_IO_DELETE_GRACE_SECONDS = 15.0
_PREWARM_CLAIM_WAIT_SECONDS = 60.0
DEFAULT_CLOSE_RESULT_TIMEOUT_S = 60.0
CHILD_CLEANUP_RESULT_TIMEOUT_S = 60.0
CHILD_DELETE_CONFIRM_TIMEOUT_S = 120.0
CHILD_DELETE_CONFIRM_POLL_S = 1.0
_CHILD_ADMISSION_WAIT_SECONDS = 5.0
_CHILD_CLEANUP_RESULT_TIMEOUT_S = CHILD_CLEANUP_RESULT_TIMEOUT_S
_CHILD_STAGE_MAX_FILES = 256
_CHILD_STAGE_MAX_BYTES = 64 * 1024 * 1024
_CHILD_RESULT_MAX_BYTES = 16 * 1024 * 1024
_CHILD_RESULT_MAX_ENTRIES = 1024
_CLEANUP_EXCEPTIONS = (Exception, asyncio.CancelledError, KeyboardInterrupt, SystemExit)

DEFAULT_SNAPSHOT_NAME = "fleet-rlm-python313-v7"
DEFAULT_CHILD_SNAPSHOT_NAME = "fleet-rlm-python313-child-v2"
DEFAULT_VOLUME_NAME = "rlm-volume-dspy"
PYTHON_VERSION = "3.13.13"
BASE_IMAGE = "python:3.13.13-slim-bookworm@sha256:f576b530293e74140ea91d262232648d5c4f45640a95ec447757701bfcacf034"
SESSION_RESOURCES: tuple[int, int, int] = (4, 8, 8)
SEMANTIC_CHILD_RESOURCES: tuple[int, int, int] = (2, 4, 4)
_DIRECTORY_MODE = "700"
_ZERO_UUID = UUID(int=0)
EXECUTION_MOUNT_PATH = "/workspace"


class DaytonaEnvironmentProfile(StrEnum):
    """The three logical execution environments; capacity is not implied."""

    SESSION = "session"
    SEMANTIC_CHILD = "semantic-child"
    WORKSPACE_CHILD = "workspace-child"


class VolumeClient(Protocol):
    async def get(self, name: str, *, create: bool = False) -> Any: ...


class SandboxPlatform(Protocol):
    async def get(self, sandbox_id: str) -> Any | None: ...

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
    ) -> Any: ...

    async def delete(self, sandbox_id: Any) -> None: ...

    async def start(self, sandbox_id: str) -> None: ...

    async def stop(self, sandbox_id: str, *, timeout: float = 60, force: bool = False) -> None: ...


@dataclass(frozen=True, slots=True)
class DaytonaSandboxSpec:
    """Immutable image and resource contract for Fleet Daytona Sandboxes."""

    snapshot: str
    python_version: str = PYTHON_VERSION
    base_image: str = BASE_IMAGE
    cpu: int = SESSION_RESOURCES[0]
    memory_gib: int = SESSION_RESOURCES[1]
    disk_gib: int = SESSION_RESOURCES[2]
    profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.SESSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot", validate_snapshot_name(self.snapshot))
        profile = self.profile
        if not isinstance(profile, DaytonaEnvironmentProfile):
            try:
                profile = DaytonaEnvironmentProfile(str(profile))
            except ValueError as exc:
                raise ValueError("unknown Daytona environment profile") from exc
            object.__setattr__(self, "profile", profile)
        expected = (
            SEMANTIC_CHILD_RESOURCES if profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD else SESSION_RESOURCES
        )
        if (self.cpu, self.memory_gib, self.disk_gib) != expected:
            raise ValueError(
                "Fleet Daytona snapshot resources must be "
                f"{expected[0]} CPU, {expected[1]} GiB memory, and {expected[2]} GiB disk for {profile.value}"
            )

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.SESSION,
    ) -> DaytonaSandboxSpec:
        field = "daytona_child_snapshot" if profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD else "daytona_snapshot"
        value = getattr(settings, field, None)
        if not isinstance(value, str) or not value.strip():
            env_name = (
                "FLEET_DAYTONA_CHILD_SNAPSHOT"
                if profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD
                else "FLEET_DAYTONA_SNAPSHOT"
            )
            raise ValueError(f"{env_name} is required")
        resources = (
            SEMANTIC_CHILD_RESOURCES if profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD else SESSION_RESOURCES
        )
        return cls(
            snapshot=value.strip(),
            cpu=resources[0],
            memory_gib=resources[1],
            disk_gib=resources[2],
            profile=profile,
        )


@dataclass(frozen=True, slots=True)
class VolumeConfig:
    """Server-owned Volume identity for Workspace-scoped Sandboxes."""

    name: str = DEFAULT_VOLUME_NAME
    mount_path: str = DEFAULT_VOLUME_MOUNT_PATH

    def __post_init__(self) -> None:
        if not self.name or not str(self.name).strip():
            raise ValueError("volume name is required")
        if any(character in self.name for character in ("/", "\\", "\x00", "..")):
            raise ValueError("volume name must not contain path characters")
        validate_mount_path(self.mount_path)

    @classmethod
    def from_settings(cls, settings: Any) -> VolumeConfig:
        name = getattr(settings, "volume_name", None) or DEFAULT_VOLUME_NAME
        mount = getattr(settings, "volume_mount_path", None) or DEFAULT_VOLUME_MOUNT_PATH
        return cls(name=str(name), mount_path=str(mount))

    def paths(self) -> VolumePaths:
        return VolumePaths.from_mount(self.mount_path)


@dataclass(frozen=True, slots=True)
class ExpectedWorkspaceMount:
    volume_id: str
    volume_subpath: str
    mount_path: str
    workspace_id: UUID
    session_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mount_path", str(self.mount_path))


def sandbox_spec_from_settings(
    settings: Any,
    profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.SESSION,
) -> DaytonaSandboxSpec:
    return DaytonaSandboxSpec.from_settings(settings, profile)


def volume_config_from_settings(settings: Any) -> VolumeConfig:
    return VolumeConfig.from_settings(settings)


def execution_timeout_s_from_settings(settings: Any) -> int:
    configured = getattr(settings, "rlm_execution_timeout_s", DEFAULT_EXECUTION_TIMEOUT_S)
    if isinstance(configured, int) and not isinstance(configured, bool) and configured > 0:
        return configured
    return DEFAULT_EXECUTION_TIMEOUT_S


def recursive_child_volume_subpath(workspace_id: UUID, run_id: UUID, call_index: int) -> str:
    workspace = require_non_zero_workspace_id(workspace_id)
    if not isinstance(run_id, UUID):
        raise TypeError("run_id must be a UUID")
    if run_id == _ZERO_UUID:
        raise ValueError("run_id must not be the zero UUID")
    if not isinstance(call_index, int) or isinstance(call_index, bool) or call_index <= 0:
        raise ValueError("call_index must be a positive integer")
    return f"recursive/{workspace}/{run_id}/{call_index}"


def require_recursive_child_volume_subpath(
    subpath: str,
    *,
    workspace_id: UUID | None = None,
    run_id: UUID | None = None,
    call_index: int | None = None,
) -> str:
    if not isinstance(subpath, str) or not subpath.strip():
        raise ValueError("recursive child volume subpath is required")
    normalized = subpath.strip().strip("/")
    parts = normalized.split("/")
    if len(parts) != 4 or parts[0] != "recursive" or ".." in parts:
        raise ValueError("recursive child volume subpath must be recursive/<workspace_id>/<run_id>/<call_index>")
    try:
        parsed_workspace = UUID(parts[1])
        parsed_run = UUID(parts[2])
    except (TypeError, ValueError):
        raise ValueError("recursive child volume subpath must contain UUID ownership") from None
    try:
        parsed_index = int(parts[3])
    except ValueError:
        raise ValueError("recursive child volume subpath call index must be a positive integer") from None
    expected = recursive_child_volume_subpath(parsed_workspace, parsed_run, parsed_index)
    if normalized != expected:
        raise ValueError("recursive child volume subpath is not canonical")
    if workspace_id is not None and parsed_workspace != require_non_zero_workspace_id(workspace_id):
        raise ValueError("recursive child volume subpath does not match workspace_id")
    if run_id is not None and parsed_run != run_id:
        raise ValueError("recursive child volume subpath does not match run_id")
    if call_index is not None and parsed_index != call_index:
        raise ValueError("recursive child volume subpath does not match call_index")
    return normalized


def require_volume_mount_subpath(subpath: str) -> str:
    if not isinstance(subpath, str) or not subpath.strip():
        return require_scoped_volume_subpath(subpath)
    try:
        return require_scoped_volume_subpath(subpath)
    except ValueError:
        pass
    try:
        return require_recursive_child_volume_subpath(subpath)
    except ValueError:
        pass

    normalized = subpath.strip().strip("/")
    parts = normalized.split("/")
    if len(parts) != 5 or parts[0] != "workspaces" or parts[2] != "sessions" or parts[4] != "workspace":
        raise ValueError("VolumeMount subpath is not a supported Fleet namespace") from None
    try:
        workspace_id = UUID(parts[1])
        session_id = UUID(parts[3])
    except ValueError:
        raise ValueError("Session workspace VolumeMount subpath must contain canonical UUIDs") from None
    return require_session_workspace_subpath(normalized, workspace_id=workspace_id, session_id=session_id)


def volume_mount_spec(config: VolumeConfig, volume_id: str, *, workspace_id: UUID) -> dict[str, str]:
    if not volume_id or not str(volume_id).strip():
        raise ValueError("volume_id is required")
    return {
        "volume_id": str(volume_id),
        "mount_path": str(validate_mount_path(config.mount_path)),
        "subpath": workspace_volume_subpath(workspace_id),
    }


async def get_or_create_volume_id(client: VolumeClient, config: VolumeConfig) -> str:
    volume = await client.get(config.name, create=True)
    volume_id = getattr(volume, "id", None)
    if volume_id is None:
        raise RuntimeError("volume client returned an object without id")
    return str(volume_id)


def shared_volume_directories(paths: VolumePaths) -> tuple[str, ...]:
    return tuple(
        str(path)
        for path in (
            paths.artifacts_root(),
            paths.attachments_root(),
            paths.files_root(),
            paths.projects_root(),
            paths.sessions_root(),
        )
    )


def session_volume_directories(paths: VolumePaths, *, session_id: UUID) -> tuple[str, ...]:
    return tuple(
        str(path)
        for path in (
            paths.session_dir(session_id),
            paths.session_workspace_dir(session_id),
            paths.session_runs_dir(session_id),
        )
    )


def run_volume_directories(paths: VolumePaths, *, session_id: UUID, run_id: UUID) -> tuple[str, ...]:
    return tuple(
        str(path)
        for path in (
            paths.run_dir(session_id, run_id),
            paths.run_artifacts_dir(session_id, run_id),
            paths.run_attachments_dir(session_id, run_id),
        )
    )


def required_volume_directories(paths: VolumePaths, *, session_id: UUID, run_id: UUID) -> tuple[str, ...]:
    return (
        *shared_volume_directories(paths),
        *session_volume_directories(paths, session_id=session_id),
        *run_volume_directories(paths, session_id=session_id, run_id=run_id),
    )


def _sandbox_filesystem(sandbox: Any) -> Any:
    fs = getattr(sandbox, "fs", None)
    if fs is None:
        raise DaytonaAdapterError(
            message="Daytona Sandbox filesystem is unavailable",
            cause_type="VolumeLayoutUnavailable",
        )
    return fs


def _is_not_found(exc: BaseException) -> bool:
    if isinstance(exc, FileNotFoundError) or getattr(exc, "status_code", None) == 404:
        return True
    response = getattr(exc, "response", None)
    return response is not None and getattr(response, "status_code", None) == 404


def _assert_directory(info: Any) -> None:
    if not bool(getattr(info, "is_dir", False)):
        raise DaytonaAdapterError(
            message="Workspace Volume layout conflicts with an existing file",
            cause_type="VolumeLayoutConflict",
        )


async def _file_info(fs: Any, path: str) -> Any | None:
    try:
        return await fs.get_file_info(path)
    except Exception as exc:
        if _is_not_found(exc):
            return None
        raise map_provider_error(exc) from exc


async def _require_directory(fs: Any, path: str, *, create: bool) -> None:
    if not create:
        info = await _file_info(fs, path)
        if info is None:
            raise DaytonaAdapterError(
                message="Workspace Volume mount is unavailable",
                cause_type="VolumeLayoutMissingMount",
            )
        _assert_directory(info)
        return

    try:
        await fs.create_folder(path, _DIRECTORY_MODE)
    except Exception as exc:
        info = await _file_info(fs, path)
        if info is None:
            raise map_provider_error(exc) from exc
        _assert_directory(info)
        return


async def _ensure_directories(fs: Any, directories: Iterable[str]) -> None:
    batches: dict[int, list[str]] = {}
    for directory in directories:
        batches.setdefault(str(directory).strip("/").count("/"), []).append(directory)
    for depth in sorted(batches):
        await asyncio.gather(*(_require_directory(fs, d, create=True) for d in batches[depth]))


async def ensure_shared_volume_layout(sandbox: Any, paths: VolumePaths) -> None:
    fs = _sandbox_filesystem(sandbox)
    await _require_directory(fs, str(paths.mount_path), create=False)
    await _ensure_directories(fs, shared_volume_directories(paths))


async def ensure_volume_layout(
    sandbox: Any,
    paths: VolumePaths,
    *,
    session_id: UUID,
    run_id: UUID,
) -> None:
    fs = _sandbox_filesystem(sandbox)
    await _require_directory(fs, str(paths.mount_path), create=False)
    await _ensure_directories(fs, required_volume_directories(paths, session_id=session_id, run_id=run_id))


async def ensure_execution_layout(sandbox: Any, *, run_id: UUID) -> None:
    """Create only the shared Session workspace mount and Run-local scratch."""
    fs = _sandbox_filesystem(sandbox)
    await _require_directory(fs, EXECUTION_MOUNT_PATH, create=False)
    await _ensure_directories(fs, ("/tmp/fleet", f"/tmp/fleet/{run_id}"))


def _mount_field(mount: Any, key: str) -> str | None:
    value = mount.get(key) if isinstance(mount, dict) else getattr(mount, key, None)
    return None if value is None else str(value)


def verify_sandbox_workspace_mount(sandbox: Any, expected: ExpectedWorkspaceMount) -> None:
    labels = getattr(sandbox, "labels", None)
    if isinstance(labels, dict) and labels:
        labeled = str(labels.get("workspace_id") or "").strip()
        if labeled and labeled != str(expected.workspace_id):
            raise DaytonaAdapterError(
                message="sandbox workspace label does not match lease workspace",
                cause_type="WorkspaceMountMismatch",
            )
    mounts = getattr(sandbox, "volumes", None)
    if mounts is None:
        mounts = getattr(sandbox, "mounts", None)
    if not mounts:
        flat = {
            "volume_id": getattr(sandbox, "volume_id", None),
            "mount_path": getattr(sandbox, "mount_path", None),
            "subpath": getattr(sandbox, "volume_subpath", None),
        }
        if all(value is None for value in flat.values()):
            raise DaytonaAdapterError(
                message="sandbox volume mount metadata is unavailable",
                cause_type="WorkspaceMountMetadataMissing",
            )
        mounts = [flat]
    for mount in mounts:
        if (
            _mount_field(mount, "volume_id") == expected.volume_id
            and _mount_field(mount, "mount_path") == str(expected.mount_path)
            and (_mount_field(mount, "subpath") or _mount_field(mount, "volume_subpath")) == expected.volume_subpath
        ):
            return
    raise DaytonaAdapterError(
        message="sandbox volume mount does not match workspace scope",
        cause_type="WorkspaceMountMismatch",
    )


def verify_sandbox_spec(sandbox: Any, spec: DaytonaSandboxSpec) -> None:
    actual = getattr(sandbox, "snapshot", None)
    if str(actual or "").strip() != spec.snapshot:
        raise DaytonaAdapterError(
            message="sandbox snapshot does not match configured Fleet snapshot",
            cause_type="SandboxSnapshotMismatch",
        )


class SandboxProvisioner:
    """One strict async policy boundary for creation, provenance, mount, and layout."""

    def __init__(
        self,
        *,
        platform: SandboxPlatform,
        volume_config: VolumeConfig,
        sandbox_spec: DaytonaSandboxSpec,
    ) -> None:
        self._platform = platform
        self._volume_config = volume_config
        self._sandbox_spec = sandbox_spec

    def expected_mount(self, *, volume_id: str, workspace_id: UUID) -> ExpectedWorkspaceMount:
        mount = volume_mount_spec(self._volume_config, volume_id, workspace_id=workspace_id)
        return ExpectedWorkspaceMount(
            volume_id=mount["volume_id"],
            volume_subpath=mount["subpath"],
            mount_path=mount["mount_path"],
            workspace_id=workspace_id,
        )

    async def create(
        self,
        expected: ExpectedWorkspaceMount,
        *,
        labels: dict[str, str],
        ephemeral: bool,
    ) -> Any:
        try:
            return await self._platform.create(
                volume_id=expected.volume_id,
                mount_path=str(expected.mount_path),
                volume_subpath=(
                    require_session_workspace_subpath(
                        expected.volume_subpath,
                        workspace_id=expected.workspace_id,
                        session_id=expected.session_id,
                    )
                    if expected.session_id is not None
                    else require_scoped_volume_subpath(
                        expected.volume_subpath,
                        workspace_id=expected.workspace_id,
                    )
                ),
                labels=labels,
                ephemeral=ephemeral,
            )
        except Exception as exc:
            raise map_provider_error(exc) from exc

    def verify(self, sandbox: Any, expected: ExpectedWorkspaceMount) -> None:
        verify_sandbox_workspace_mount(sandbox, expected)
        verify_sandbox_spec(sandbox, self._sandbox_spec)

    async def verify_run_layout(
        self,
        sandbox: Any,
        expected: ExpectedWorkspaceMount,
        *,
        session_id: UUID,
        run_id: UUID,
    ) -> None:
        self.verify(sandbox, expected)
        if expected.session_id is not None:
            await ensure_execution_layout(sandbox, run_id=run_id)
        else:
            await ensure_volume_layout(
                sandbox,
                self._volume_config.paths(),
                session_id=session_id,
                run_id=run_id,
            )


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
        from daytona.common.errors import DaytonaConflictError

        try:
            volume = await self._client.volume.get(name, create=create)
        except DaytonaConflictError as exc:
            if not create:
                raise map_provider_error(exc) from exc
            # The SDK creates only after typed absence. A concurrent creator
            # may win that race; reconcile by lookup, never repeat creation.
            volume = await self._get_existing(name)
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
            await self._client.stop(sandbox, timeout=timeout)
        except Exception as exc:
            if force:
                try:
                    await self._client.delete(sandbox)
                except Exception as delete_exc:
                    if is_sandbox_not_found(delete_exc):
                        return
                    raise map_provider_error(delete_exc) from delete_exc
            else:
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
    """Acquire one ephemeral volume-backed interpreter through ``SandboxProvisioner``."""
    client = build_daytona_client(settings)
    spec = sandbox_spec_from_settings(settings)
    platform = LiveDaytonaPlatform(client, spec)
    volume_client = LiveDaytonaVolumeClient(client)
    volume_config = volume_config_from_settings(settings)
    provisioner = SandboxProvisioner(
        platform=platform,
        volume_config=volume_config,
        sandbox_spec=spec,
    )
    resolved_workspace = workspace_id or uuid4()
    volume_id = await get_or_create_volume_id(volume_client, volume_config)
    expected = provisioner.expected_mount(volume_id=volume_id, workspace_id=resolved_workspace)
    sandbox = await provisioner.create(
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
        await provisioner.verify_run_layout(
            sandbox,
            expected,
            session_id=session_id,
            run_id=run_id,
        )
        loop = asyncio.get_running_loop()
        interpreter = DaytonaCodeInterpreter(
            backend=sandbox_backend(
                sandbox,
                loop=loop,
                timeout_s=execution_timeout_s_from_settings(settings),
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


# --- Process-local Daytona Admission ---


class DaytonaAdmissionTimeoutError(RuntimeError):
    """The Turn deadline elapsed before Daytona capacity became available."""


@dataclass(slots=True)
class DaytonaAdmissionPermit:
    """One idempotently releasable slot in Daytona admission."""

    _semaphore: asyncio.BoundedSemaphore
    _loop: asyncio.AbstractEventLoop | None = None
    _execution_semaphore: asyncio.BoundedSemaphore | None = None
    _released: bool = field(default=False, init=False)
    _release_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def release(self) -> None:
        """Release on the semaphore's owning loop, safely from worker threads."""
        with self._release_lock:
            if self._released:
                return
            self._released = True
        loop = self._loop or getattr(self._semaphore, "_loop", None)
        if loop is None or loop.is_closed() or not loop.is_running():
            self._semaphore.release()
            if self._execution_semaphore is not None:
                self._execution_semaphore.release()
            return
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is loop:
            self._semaphore.release()
            if self._execution_semaphore is not None:
                self._execution_semaphore.release()
            return
        try:
            loop.call_soon_threadsafe(self._semaphore.release)
            if self._execution_semaphore is not None:
                loop.call_soon_threadsafe(self._execution_semaphore.release)
        except RuntimeError:
            self._semaphore.release()
            if self._execution_semaphore is not None:
                self._execution_semaphore.release()


class DaytonaAdmission:
    """Bound acquiring plus active Interpreter Leases for one process."""

    def __init__(self, *, max_active_leases: int = 8) -> None:
        if max_active_leases <= 0:
            raise ValueError("max_active_leases must be positive")
        if max_active_leases > 8:
            raise ValueError("max_active_leases must be at most 8")
        self._semaphore = asyncio.BoundedSemaphore(max_active_leases)
        self._execution_semaphore = asyncio.BoundedSemaphore(max(1, max_active_leases - 1))

    async def acquire(self, *, deadline: float, host_io: bool = False) -> DaytonaAdmissionPermit:
        execution_acquired = False
        try:
            async with asyncio.timeout_at(deadline):
                if not host_io:
                    await self._execution_semaphore.acquire()
                    execution_acquired = True
                await self._semaphore.acquire()
        except TimeoutError:
            if execution_acquired:
                self._execution_semaphore.release()
            raise DaytonaAdmissionTimeoutError("Daytona admission unavailable") from None
        except BaseException:
            if execution_acquired:
                self._execution_semaphore.release()
            raise
        return DaytonaAdmissionPermit(
            self._semaphore,
            asyncio.get_running_loop(),
            self._execution_semaphore if execution_acquired else None,
        )


# --- Confirmed Sandbox Deletion Lifecycle ---

DeletionPhase = Literal["requested", "deleting", "absent", "failed"]

_ABSENT_STATES = frozenset({"destroyed", "deleted"})
_DELETING_STATES = frozenset({"destroying", "deleting", "archiving", "stopping"})
_FAILED_STATES = frozenset({"error", "build_failed"})

DEFAULT_CONFIRM_TIMEOUT_S = 60.0
DEFAULT_POLL_INTERVAL_S = 1.0


class DeletionStateProbe(Protocol):
    """Caller-owned one-Sandbox lookup; returns ``None`` on explicit not-found."""

    def __call__(self, sandbox_id: str) -> Awaitable[Any | None]: ...


def _lifecycle_raw_state(target: Any) -> str:
    raw = getattr(target, "state", None)
    if raw is None:
        raw = getattr(target, "status", None)
    return str(getattr(raw, "value", raw) or "").strip().lower()


def classify_deletion_phase(raw_state: Any) -> DeletionPhase:
    """Map one raw provider state string (or enum) onto the public phase model."""
    text = str(getattr(raw_state, "value", raw_state) or "").strip().lower()
    if text in _ABSENT_STATES:
        return "absent"
    if text in _DELETING_STATES:
        return "deleting"
    if text in _FAILED_STATES:
        return "failed"
    return "requested"


@dataclass(frozen=True, slots=True)
class AbsenceConfirmation:
    """Confirmed: the provider reports the Sandbox absent (not-found/destroyed)."""

    sandbox_id: str
    observations: tuple[str, ...]
    duration_s: float
    absent: Literal[True] = True


@dataclass(frozen=True, slots=True)
class AbsenceTimeout:
    """The confirmation budget elapsed without an absent observation."""

    sandbox_id: str
    last_state: str
    observations: tuple[str, ...]
    duration_s: float
    absent: Literal[False] = False


@dataclass(frozen=True, slots=True)
class AbsenceProbeError:
    """A probe call raised, or the provider surfaced a terminal error state."""

    sandbox_id: str
    error: str
    observations: tuple[str, ...]
    duration_s: float
    absent: Literal[False] = False


AbsenceOutcome: TypeAlias = AbsenceConfirmation | AbsenceTimeout | AbsenceProbeError


async def confirm_absence(
    *,
    probe: DeletionStateProbe,
    sandbox_id: str,
    timeout_s: float = DEFAULT_CONFIRM_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> AbsenceOutcome:
    """Poll ``probe`` until the Sandbox is confirmed absent or the budget closes."""
    if sleep is None:
        sleep = asyncio.sleep
    started = clock()
    observations: list[str] = []

    def note(state: str) -> None:
        if not observations or observations[-1] != state:
            observations.append(state)

    while True:
        try:
            target = await probe(sandbox_id)
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            note("probe_error")
            return AbsenceProbeError(
                sandbox_id,
                sanitize_failure_text(exc, max_chars=160),
                tuple(observations),
                clock() - started,
            )
        if target is None:
            note("not_found")
            return AbsenceConfirmation(sandbox_id, tuple(observations), clock() - started)
        state = _lifecycle_raw_state(target) or "unknown"
        note(state)
        phase = classify_deletion_phase(state)
        if phase == "absent":
            return AbsenceConfirmation(sandbox_id, tuple(observations), clock() - started)
        if phase == "failed":
            return AbsenceProbeError(
                sandbox_id,
                f"provider error state: {state}",
                tuple(observations),
                clock() - started,
            )
        if clock() - started >= timeout_s:
            return AbsenceTimeout(sandbox_id, state, tuple(observations), clock() - started)
        await sleep(poll_interval_s)


LeaseKind: TypeAlias = Literal[
    "interactive_turn",
    "background_batch",
    "retained_session",
    "recovery_fence",
    "volume_io",
]


def _cleanup_failed(value: Any) -> bool:
    if value is None:
        return False
    if value is False:
        return True
    if bool(getattr(value, "failed", False)):
        return True
    if getattr(value, "first_error", None) is not None:
        return True
    quarantine = getattr(value, "quarantine", None)
    return bool(getattr(quarantine, "quarantined", False))


async def await_cleanup(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke sync or async cleanup without changing exception identity."""
    result = callback(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


class LeaseState(StrEnum):
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


class ActiveLeaseConflictError(RuntimeError):
    def __init__(self, session_id: UUID, holder_run_id: UUID | None = None) -> None:
        self.session_id = session_id
        self.holder_run_id = holder_run_id
        super().__init__(f"active lease conflict for session {session_id}")


class DaytonaLeaseAcquisitionTimeoutError(RuntimeError):
    pass


class LeaseCleanupError(RuntimeError):
    pass


class ActiveLeaseRegistry:
    """Thread-safe mapping of (workspace_id, session_id) to active run_id."""

    def __init__(self) -> None:
        self._holders: dict[tuple[UUID, UUID], UUID] = {}
        self._lock = Lock()

    @staticmethod
    def _key(session_id: UUID, workspace_id: UUID | None) -> tuple[UUID, UUID]:
        return (workspace_id or UUID(int=0), session_id)

    def acquire(self, session_id: UUID, run_id: UUID, *, workspace_id: UUID | None = None) -> None:
        with self._lock:
            key = self._key(session_id, workspace_id)
            existing = self._holders.get(key)
            if existing is not None and (existing != run_id or run_id == PREWARM_RUN_ID):
                raise ActiveLeaseConflictError(session_id, holder_run_id=existing)
            self._holders[key] = run_id

    def release(self, session_id: UUID, run_id: UUID, *, workspace_id: UUID | None = None) -> None:
        with self._lock:
            key = self._key(session_id, workspace_id)
            if self._holders.get(key) == run_id:
                del self._holders[key]

    def holder(self, session_id: UUID, *, workspace_id: UUID | None = None) -> UUID | None:
        with self._lock:
            if workspace_id is not None:
                return self._holders.get(self._key(session_id, workspace_id))
            matches = [run for (ws, sid), run in self._holders.items() if sid == session_id]
            return matches[0] if len(matches) == 1 else None

    def has_session(self, session_id: UUID) -> bool:
        with self._lock:
            return any(sid == session_id for (_ws, sid) in self._holders)


@dataclass(frozen=True, slots=True)
class LeaseRequest:
    session_id: UUID
    user_id: UUID
    workspace_id: UUID
    run_id: UUID | None = None


@dataclass(slots=True)
class InterpreterLease:
    """Handle for an acquired interpreter and its underlying sandbox."""

    sandbox_id: str
    interpreter_id: str
    volume_id: str
    mount_path: str
    interpreter: Any
    session_id: str | None = None
    user_id: str | None = None
    run_id: str | None = None
    workspace_id: str | None = None
    volume_subpath: str | None = None
    created_sandbox: bool = False
    sandbox: Any | None = None
    requires_sandbox_deletion: bool = False
    binding_generation: int = 1
    _released: bool = False
    _provider_retired: bool = False
    _defer_owner_release: bool = False
    _defer_idle_cleanup: bool = False
    _state: LeaseState = LeaseState.OPEN
    _on_release: Callable[[], None] | None = None
    _release_lock: Lock = field(default_factory=Lock, repr=False)

    @property
    def state(self) -> LeaseState:
        return self._state

    @property
    def closed(self) -> bool:
        return self._state is LeaseState.CLOSED

    @property
    def closing(self) -> bool:
        return self._state is LeaseState.CLOSING

    @property
    def failed(self) -> bool:
        return self._state is LeaseState.FAILED

    def release(self) -> None:
        with self._release_lock:
            if self._released:
                return
            self._state = LeaseState.CLOSING
            try:
                if hasattr(self.interpreter, "shutdown"):
                    try:
                        self.interpreter.shutdown(strict_broker_cleanup=True)
                    except TypeError:
                        self.interpreter.shutdown()
            except BaseException:
                self._state = LeaseState.FAILED
                raise
            self._released = True
            self._state = LeaseState.CLOSED
            if self._on_release is not None and not self._defer_owner_release:
                with contextlib.suppress(BaseException):
                    self._on_release()


class RootSessionLease:
    """Cancellation-safe handle for a session's root sandbox lease."""

    def __init__(
        self,
        key: Any,
        lease: Any,
        release_callback: Callable[[Any], Awaitable[Any] | Any],
        on_closed: Callable[[RootSessionLease], Awaitable[Any] | Any] | None = None,
        *,
        spec: Any | None = None,
        sandbox: Any | None = None,
        interpreter: Any | None = None,
        broker: Any | None = None,
        volume: Any | None = None,
        volume_id: str | None = None,
        mount_path: str | None = None,
        volume_subpath: str | None = None,
    ) -> None:
        self.key = key
        self.spec = spec
        self.lease = lease
        self.release_callback = release_callback
        self.on_closed = on_closed
        self.sandbox = sandbox if sandbox is not None else getattr(lease, "sandbox", None)
        self.interpreter = interpreter if interpreter is not None else getattr(lease, "interpreter", None)
        self.broker = broker
        self.volume = volume if volume is not None else getattr(lease, "volume", None)
        sandbox_id = getattr(lease, "sandbox_id", None) or getattr(self.sandbox, "id", None)
        self.sandbox_id = str(sandbox_id or "")
        vol_id = getattr(lease, "volume_id", None)
        self.volume_id = volume_id or (str(vol_id) if vol_id else None)
        mp = getattr(lease, "mount_path", None)
        self.mount_path = mount_path or (str(mp) if mp else None)
        vsub = getattr(lease, "volume_subpath", None)
        self.volume_subpath = volume_subpath or (str(vsub) if vsub else None)
        self._state = LeaseState.OPEN
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._close_error: BaseException | None = None
        self._notify_on_close = False
        self._close_barrier: Callable[[], Awaitable[Any] | Any] | None = None

    @property
    def state(self) -> LeaseState:
        return self._state

    @property
    def status(self) -> LeaseState:
        return self.state

    @property
    def closed(self) -> bool:
        return self._state is LeaseState.CLOSED

    @property
    def closing(self) -> bool:
        return self._state is LeaseState.CLOSING

    @property
    def failed(self) -> bool:
        return self._state is LeaseState.FAILED

    @property
    def close_error(self) -> BaseException | None:
        return self._close_error

    def set_close_barrier(self, barrier: Callable[[], Awaitable[Any] | Any] | None) -> None:
        self._close_barrier = barrier

    async def try_set_close_barrier(self, barrier: Callable[[], Awaitable[Any] | Any] | None) -> bool:
        async with self._close_lock:
            if self._state is not LeaseState.OPEN:
                return False
            self._close_barrier = barrier
            return True

    async def close(self, *, notify: bool = True, deadline: float | None = None) -> None:
        async with self._close_lock:
            if self._state is LeaseState.CLOSED:
                return
            self._notify_on_close = self._notify_on_close or notify
            task = self._close_task
            if task is None:
                self._state = LeaseState.CLOSING
                task = asyncio.create_task(self._perform_close(), name="fleet-daytona-root-lease-close")
                self._close_task = task
        if deadline is None:
            await asyncio.shield(task)
            return
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("root Session lease close timed out")
        await asyncio.wait_for(asyncio.shield(task), timeout=remaining)

    async def _perform_close(self) -> None:
        current = asyncio.current_task()
        try:
            if self._close_barrier is not None:
                await await_cleanup(self._close_barrier)
            result = await await_cleanup(self.release_callback, self.lease)
            if _cleanup_failed(self.lease) or _cleanup_failed(result):
                raise RuntimeError("root Session cleanup failed")
        except BaseException as exc:
            async with self._close_lock:
                if self._close_task is current:
                    self._state = LeaseState.FAILED
                    self._close_error = exc
                    self._close_task = None
            raise

        async with self._close_lock:
            if self._close_task is not current:
                return
            self._state = LeaseState.CLOSED
            self._close_error = None
            self._close_task = None
            notify = self._notify_on_close
            self._notify_on_close = False
        if notify and self.on_closed is not None:
            with contextlib.suppress(BaseException):
                await await_cleanup(self.on_closed, self)

    async def release(self) -> None:
        await self.close()


@dataclass(frozen=True, slots=True)
class CloseComponentOutcome:
    status: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class InterpreterCloseOutcome:
    status: str
    broker: str = "not_present"
    backend: str = "not_present"
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderCleanupOutcome:
    action: str = "none"
    requested: bool = False
    confirmed_absent: bool = False
    plateau: tuple[str, ...] = ()
    duration_s: float = 0.0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AdmissionOutcome:
    held: bool = False
    released: bool = False
    released_after: str = "not_held"


@dataclass(frozen=True, slots=True)
class QuarantineOutcome:
    quarantined: bool = False
    lane: str = "none"
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxLeaseReceipt:
    kind: LeaseKind
    sandbox_id: str | None
    interpreter: InterpreterCloseOutcome
    provider: ProviderCleanupOutcome
    admission: AdmissionOutcome
    quarantine: QuarantineOutcome
    duration_s: float
    first_error: str | None = None

    @property
    def clean(self) -> bool:
        if self.first_error is not None:
            return False
        if self.quarantine.quarantined:
            return False
        if self.provider.error is not None:
            return False
        return self.interpreter.error is None


class LeasePurgeHook(Protocol):
    def __call__(self, sandbox: Any) -> Awaitable[None]: ...


@dataclass(frozen=True, slots=True)
class SandboxLeasePolicy:
    kind: LeaseKind
    interpreter_shutdown: bool = True
    strict_broker_cleanup: bool = True
    provider_action: Literal["none", "stop", "delete"] = "none"
    stop_force: bool = False
    confirm_absence: bool = False
    confirm_timeout_s: float = 120.0
    confirm_poll_interval_s: float = 0.5
    confirm_fn: Callable[..., Awaitable[AbsenceOutcome]] | None = None
    provider_request_timeout_s: float | None = 30.0
    close_result_timeout_s: float = DEFAULT_CLOSE_RESULT_TIMEOUT_S

    def __post_init__(self) -> None:
        if self.kind == "volume_io":
            object.__setattr__(self, "interpreter_shutdown", False)
            object.__setattr__(self, "provider_action", "delete")
            object.__setattr__(self, "confirm_absence", True)
        elif self.kind == "recovery_fence":
            object.__setattr__(self, "interpreter_shutdown", False)
            object.__setattr__(self, "provider_action", "stop")
            object.__setattr__(self, "stop_force", True)


def _receipt_state(receipt: SandboxLeaseReceipt) -> LeaseState:
    if receipt.first_error is not None or receipt.quarantine.quarantined:
        return LeaseState.FAILED
    return LeaseState.CLOSED


def _sandbox_id_or_none(sandbox: Any) -> str | None:
    value = getattr(sandbox, "id", None)
    return value if isinstance(value, str) and value else None


_DEFERRED_CLOSE_TASKS: set[asyncio.Task[None]] = set()
_PROVIDER_REQUEST_OWNERS: set[tuple[asyncio.Future[Any], SandboxLease]] = set()
_CLOSE_TASK_OWNERS: set[tuple[asyncio.Future[Any], SandboxLease]] = set()
_FAILED_LEASE_OWNERS: dict[int, SandboxLease] = {}
_UNSCHEDULED_CLOSE_OWNERS: dict[
    Future[Any], tuple[asyncio.AbstractEventLoop, Callable[[], Coroutine[Any, Any, Any]]]
] = {}


def _retain_close_task(task: asyncio.Future[Any], lease: SandboxLease) -> None:
    entry = (task, lease)
    _CLOSE_TASK_OWNERS.add(entry)

    def settled(completed: asyncio.Future[Any]) -> None:
        _CLOSE_TASK_OWNERS.discard(entry)
        if not completed.cancelled():
            with contextlib.suppress(BaseException):
                completed.exception()

    task.add_done_callback(settled)


def has_pending_lease_ownership() -> bool:
    """Return True if any lease task, provider request, or quarantined lease is unsettled."""
    return bool(
        any(not task.done() for task in _DEFERRED_CLOSE_TASKS)
        or any(not task.done() for task, _ in _PROVIDER_REQUEST_OWNERS)
        or any(not task.done() for task, _ in _CLOSE_TASK_OWNERS)
        or _FAILED_LEASE_OWNERS
        or _UNSCHEDULED_CLOSE_OWNERS
    )


async def wait_lease_ownership(*, timeout: float | None = None) -> bool:
    tasks = tuple(
        task
        for task in (
            *tuple(task for task in _DEFERRED_CLOSE_TASKS if not task.done()),
            *tuple(task for task, _lease in _PROVIDER_REQUEST_OWNERS if not task.done()),
            *tuple(task for task, _lease in _CLOSE_TASK_OWNERS if not task.done()),
        )
    )
    if not tasks:
        return not has_pending_lease_ownership()
    if timeout is None:
        await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
        return not has_pending_lease_ownership()
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    return not pending and not has_pending_lease_ownership()


class SandboxLease:
    """Owns one Sandbox handle and its confirmed, idempotent close."""

    def __init__(
        self,
        *,
        kind: LeaseKind,
        sandbox: Any | None,
        sandbox_id: str | None = None,
        platform: SandboxPlatform | None = None,
        permit: DaytonaAdmissionPermit | None = None,
        interpreter: Any | None = None,
        purge: LeasePurgeHook | None = None,
        policy: SandboxLeasePolicy | None = None,
    ) -> None:
        self._policy = policy or SandboxLeasePolicy(kind=kind)
        self._sandbox = sandbox
        self._sandbox_id = sandbox_id or _sandbox_id_or_none(sandbox)
        self._platform = platform
        self._permit = permit
        self._interpreter = interpreter
        self._purge = purge
        self._closed = False
        self._state = LeaseState.OPEN
        self._receipt: SandboxLeaseReceipt | None = None
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Future[SandboxLeaseReceipt] | None = None
        self._interpreter_task: asyncio.Task[InterpreterCloseOutcome] | None = None
        self._deferred_close_task: asyncio.Task[None] | None = None
        self._provider_tasks: set[asyncio.Future[Any]] = set()

    @property
    def state(self) -> LeaseState:
        return self._state

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def closing(self) -> bool:
        return self._state is LeaseState.CLOSING

    @property
    def failed(self) -> bool:
        return self._state is LeaseState.FAILED

    @property
    def has_pending_ownership(self) -> bool:
        return bool(
            any(not task.done() for task in self._provider_tasks)
            or (self._deferred_close_task is not None and not self._deferred_close_task.done())
        )

    def _shutdown_interpreter(self) -> InterpreterCloseOutcome:
        interpreter = self._interpreter
        policy = self._policy
        has_broker = (
            bool(getattr(interpreter, "broker", None) or getattr(interpreter, "_http_broker", None))
            if interpreter is not None
            else False
        )
        has_backend = bool(getattr(interpreter, "_backend", None)) if interpreter is not None else False
        if interpreter is None or not policy.interpreter_shutdown:
            return InterpreterCloseOutcome(
                status="not_present" if interpreter is None else "skipped",
                broker="not_present" if not has_broker else "skipped",
                backend="not_present" if not has_backend else "skipped",
            )
        try:
            if hasattr(interpreter, "shutdown"):
                try:
                    interpreter.shutdown(strict_broker_cleanup=policy.strict_broker_cleanup)
                except TypeError:
                    interpreter.shutdown()
        except BaseException as exc:
            error = sanitize_failure_text(exc)
            return InterpreterCloseOutcome(
                status="failed",
                broker="failed" if has_broker else "not_present",
                backend="failed" if has_backend else "not_present",
                error=error,
            )
        return InterpreterCloseOutcome(
            status="clean",
            broker="stopped" if has_broker else "not_present",
            backend="closed" if has_backend else "not_present",
        )

    async def _shutdown_interpreter_owned(self, *, bounded: bool = True) -> InterpreterCloseOutcome:
        task = asyncio.create_task(asyncio.to_thread(self._shutdown_interpreter))
        self._interpreter_task = task
        try:
            if not bounded:
                return await task
            return await asyncio.wait_for(asyncio.shield(task), timeout=max(self._policy.close_result_timeout_s, 1.0))
        except TimeoutError:
            return InterpreterCloseOutcome(
                status="quarantined",
                broker="quarantined" if self._interpreter is not None else "not_present",
                backend="quarantined" if self._interpreter is not None else "not_present",
                error="interpreter shutdown quarantined past close bound",
            )

    def _retain_provider_task(self, task: asyncio.Future[Any]) -> None:
        self._provider_tasks.add(task)
        _PROVIDER_REQUEST_OWNERS.add((task, self))

        def settled(completed: asyncio.Future[Any]) -> None:
            self._provider_tasks.discard(completed)
            _PROVIDER_REQUEST_OWNERS.discard((completed, self))
            if completed.cancelled():
                return
            with contextlib.suppress(BaseException):
                completed.exception()

        task.add_done_callback(settled)

    async def _run_provider_request(
        self,
        request: Awaitable[Any],
        *,
        timeout_s: float | None,
    ) -> str | None:
        task = asyncio.ensure_future(request)
        self._retain_provider_task(task)
        try:
            if timeout_s is None:
                await task
            else:
                await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, timeout_s))
        except TimeoutError:
            # A client timeout does not establish that the provider stopped
            # the request. Keep the task (and its lease) owned until it settles.
            return "provider request TimeoutError"
        except BaseException as exc:
            return sanitize_failure_text(exc)
        return None

    async def _bounded_probe(self, sandbox_id: str) -> Any | None:
        assert self._platform is not None
        probe = getattr(self._platform, "get", None)
        if not callable(probe):
            raise RuntimeError("absence probe unavailable: platform lacks get")
        task = asyncio.ensure_future(probe(sandbox_id))
        self._retain_provider_task(task)
        timeout_s = min(
            max(0.1, self._policy.confirm_poll_interval_s * 2),
            max(0.1, self._policy.confirm_timeout_s),
        )
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
        except TimeoutError:
            raise

    async def _provider_close(self) -> ProviderCleanupOutcome:
        policy = self._policy
        platform = self._platform
        action = policy.provider_action
        if action == "none" or platform is None or self._sandbox_id is None:
            return ProviderCleanupOutcome(action="none", requested=False, confirmed_absent=False)
        started = time.monotonic()
        request_error: str | None = None
        if action == "delete":
            try:
                request = platform.delete(self._sandbox_id)
                request_error = await self._run_provider_request(request, timeout_s=policy.provider_request_timeout_s)
            except BaseException as exc:
                request_error = sanitize_failure_text(exc)
            plateau: tuple[str, ...] = ()
            absent = False
            confirm_error: str | None = None
            probe = getattr(platform, "get", None)
            if policy.confirm_absence and not callable(probe):
                return ProviderCleanupOutcome(
                    action="delete",
                    requested=True,
                    confirmed_absent=False,
                    duration_s=time.monotonic() - started,
                    error=request_error or "absence probe unavailable: platform lacks get",
                )
            if policy.confirm_absence:
                confirm_fn = policy.confirm_fn or confirm_absence
                try:
                    absence: AbsenceOutcome = await confirm_fn(
                        probe=self._bounded_probe,
                        sandbox_id=self._sandbox_id,
                        timeout_s=policy.confirm_timeout_s,
                        poll_interval_s=policy.confirm_poll_interval_s,
                    )
                except BaseException as exc:
                    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                        raise
                    confirm_error = sanitize_failure_text(exc)
                else:
                    plateau = absence.observations
                    absent = isinstance(absence, AbsenceConfirmation)
                    if not absent:
                        confirm_error = f"absence unconfirmed: {absence!r}"[:240]
            return ProviderCleanupOutcome(
                action="delete",
                requested=True,
                confirmed_absent=absent,
                plateau=plateau,
                duration_s=time.monotonic() - started,
                error=request_error or confirm_error,
            )
        stop_error: str | None = None
        try:
            stop_request = platform.stop(self._sandbox_id, timeout=60, force=self._policy.stop_force)
            stop_error = await self._run_provider_request(stop_request, timeout_s=policy.provider_request_timeout_s)
        except BaseException as exc:
            stop_error = sanitize_failure_text(exc)
            if not policy.stop_force or not policy.confirm_absence:
                return ProviderCleanupOutcome(
                    action="stop",
                    requested=True,
                    confirmed_absent=False,
                    duration_s=time.monotonic() - started,
                    error=stop_error,
                )
        if stop_error is not None and policy.stop_force and policy.confirm_absence:
            probe = getattr(platform, "get", None)
            if not callable(probe):
                return ProviderCleanupOutcome(
                    action="stop",
                    requested=True,
                    confirmed_absent=False,
                    duration_s=time.monotonic() - started,
                    error=stop_error or "absence probe unavailable: platform lacks get",
                )
            confirm_fn = policy.confirm_fn or confirm_absence
            try:
                absence = await confirm_fn(
                    probe=self._bounded_probe,
                    sandbox_id=self._sandbox_id,
                    timeout_s=min(policy.confirm_timeout_s, 1.0),
                    poll_interval_s=min(policy.confirm_poll_interval_s, 0.1),
                )
            except BaseException as exc:
                if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                return ProviderCleanupOutcome(
                    action="stop",
                    requested=True,
                    confirmed_absent=False,
                    duration_s=time.monotonic() - started,
                    error=stop_error or sanitize_failure_text(exc),
                )
            absent = isinstance(absence, AbsenceConfirmation)
            return ProviderCleanupOutcome(
                action="stop",
                requested=True,
                confirmed_absent=absent,
                plateau=absence.observations,
                duration_s=time.monotonic() - started,
                error=stop_error or (None if absent else f"absence unconfirmed: {absence!r}"[:240]),
            )
        return ProviderCleanupOutcome(
            action="stop",
            requested=True,
            confirmed_absent=False,
            duration_s=time.monotonic() - started,
            error=stop_error,
        )

    async def _finish_retained_provider_close(self) -> None:
        retry_delay = min(max(self._policy.confirm_poll_interval_s, 0.05), 1.0)
        while True:
            pending = tuple(task for task in self._provider_tasks if not task.done())
            if pending:
                await asyncio.wait(pending, timeout=retry_delay)
                if any(not task.done() for task in self._provider_tasks):
                    continue
            provider = await self._provider_close()
            confirmed = (
                (provider.action == "delete" and provider.requested and provider.confirmed_absent)
                or (provider.action in {"stop", "none"} and provider.error is None)
            ) and not self._provider_tasks
            if confirmed:
                if self._permit is not None:
                    self._permit.release()
                    self._permit = None
                return
            await asyncio.sleep(retry_delay)

    async def _finish_deferred_close(
        self,
        interpreter_task: asyncio.Task[InterpreterCloseOutcome],
    ) -> None:
        try:
            interpreter = await interpreter_task
        except BaseException as exc:
            interpreter = InterpreterCloseOutcome(
                status="failed",
                broker="failed",
                backend="failed",
                error=sanitize_failure_text(exc),
            )
        while interpreter.status in {"failed", "quarantined"}:
            interpreter = await self._shutdown_interpreter_owned(bounded=False)
            if interpreter.status in {"failed", "quarantined"}:
                await asyncio.sleep(min(max(self._policy.confirm_poll_interval_s, 0.05), 1.0))
        if self._purge is not None and self._sandbox is not None:
            with contextlib.suppress(BaseException):
                await self._purge(self._sandbox)
        provider = await self._provider_close()
        retained_provider_pending = (
            self._policy.kind in {"retained_session", "volume_io"}
            and (
                bool(self._provider_tasks)
                or provider.error is not None
                or (
                    self._policy.confirm_absence
                    and provider.action == "delete"
                    and provider.requested
                    and not provider.confirmed_absent
                )
            )
        ) or (self._policy.kind == "recovery_fence" and bool(self._provider_tasks))
        if retained_provider_pending:
            await self._finish_retained_provider_close()
            return
        if self._permit is not None:
            self._permit.release()
            self._permit = None

    def _retain_deferred_close(self, task: asyncio.Task[None]) -> None:
        self._deferred_close_task = task
        _DEFERRED_CLOSE_TASKS.add(task)

        def settled(completed: asyncio.Task[None]) -> None:
            _DEFERRED_CLOSE_TASKS.discard(completed)
            if completed.cancelled():
                _FAILED_LEASE_OWNERS[id(self)] = self
                return
            with contextlib.suppress(BaseException):
                error = completed.exception()
            if error is None:
                _FAILED_LEASE_OWNERS.pop(id(self), None)
            else:
                _FAILED_LEASE_OWNERS[id(self)] = self

        task.add_done_callback(settled)

    async def _close_core(self, *, bounded_interpreter: bool = True) -> SandboxLeaseReceipt:
        started = time.monotonic()
        policy = self._policy
        first_error: str | None = None

        interpreter = await self._shutdown_interpreter_owned(bounded=bounded_interpreter)
        if interpreter.status in {"failed", "quarantined"} and first_error is None:
            first_error = interpreter.error

        if interpreter.status in {"failed", "quarantined"}:
            interpreter_task = self._interpreter_task
            if interpreter_task is None:
                raise RuntimeError("interpreter quarantine has no owned task")
            if not bounded_interpreter:
                interpreter = await self._shutdown_interpreter_owned(bounded=False)
                if interpreter.status in {"failed", "quarantined"}:
                    held = self._permit is not None
                    return SandboxLeaseReceipt(
                        kind=policy.kind,
                        sandbox_id=self._sandbox_id,
                        interpreter=interpreter,
                        provider=ProviderCleanupOutcome(
                            action=policy.provider_action,
                            requested=False,
                            confirmed_absent=False,
                            error="provider cleanup deferred until interpreter shutdown settles",
                        ),
                        admission=AdmissionOutcome(
                            held=held,
                            released=False,
                            released_after="quarantine_failure" if held else "not_held",
                        ),
                        quarantine=QuarantineOutcome(
                            quarantined=True,
                            lane="fallback_thread",
                            error=interpreter.error,
                        ),
                        duration_s=time.monotonic() - started,
                        first_error=interpreter.error or "interpreter shutdown quarantined",
                    )
            else:
                deferred = asyncio.create_task(
                    self._finish_deferred_close(interpreter_task),
                    name="fleet-sandbox-lease-deferred-close",
                )
                self._retain_deferred_close(deferred)
                held = self._permit is not None
                return SandboxLeaseReceipt(
                    kind=policy.kind,
                    sandbox_id=self._sandbox_id,
                    interpreter=interpreter,
                    provider=ProviderCleanupOutcome(
                        action=policy.provider_action,
                        requested=False,
                        confirmed_absent=False,
                        error="provider cleanup deferred until interpreter shutdown settles",
                    ),
                    admission=AdmissionOutcome(
                        held=held,
                        released=False,
                        released_after="quarantine_failure" if held else "not_held",
                    ),
                    quarantine=QuarantineOutcome(
                        quarantined=True,
                        lane="owner_loop",
                        error=interpreter.error,
                    ),
                    duration_s=time.monotonic() - started,
                    first_error=interpreter.error or "interpreter shutdown quarantined",
                )

        if self._purge is not None and self._sandbox is not None:
            try:
                await self._purge(self._sandbox)
            except BaseException as exc:
                if first_error is None:
                    first_error = sanitize_failure_text(exc)

        provider = await self._provider_close()
        if provider.error is not None and first_error is None:
            first_error = provider.error

        retained_provider_pending = (
            policy.kind in {"retained_session", "volume_io"}
            and (
                bool(self._provider_tasks)
                or provider.error is not None
                or (
                    policy.confirm_absence
                    and provider.action == "delete"
                    and provider.requested
                    and not provider.confirmed_absent
                )
            )
        ) or (policy.kind == "recovery_fence" and bool(self._provider_tasks))
        if retained_provider_pending:
            deferred = asyncio.create_task(
                self._finish_retained_provider_close(),
                name="fleet-sandbox-lease-retained-provider-close",
            )
            self._retain_deferred_close(deferred)
            held = self._permit is not None
            return SandboxLeaseReceipt(
                kind=policy.kind,
                sandbox_id=self._sandbox_id,
                interpreter=interpreter,
                provider=provider,
                admission=AdmissionOutcome(
                    held=held,
                    released=False,
                    released_after="quarantine_failure" if held else "not_held",
                ),
                quarantine=QuarantineOutcome(
                    quarantined=True,
                    lane="owner_loop",
                    error=provider.error or "provider request remains owned",
                ),
                duration_s=time.monotonic() - started,
                first_error=first_error or "provider request remains owned",
            )

        quarantined = interpreter.status == "quarantined"
        quarantine_error: str | None = interpreter.error if quarantined else None
        if provider.error is not None:
            quarantined = True
            quarantine_error = quarantine_error or provider.error
        if (
            self._policy.confirm_absence
            and provider.action == "delete"
            and provider.requested
            and not provider.confirmed_absent
        ):
            quarantined = True
            quarantine_error = provider.error or "absence unconfirmed"

        held = self._permit is not None
        if self._permit is not None:
            self._permit.release()
            self._permit = None
        if not held:
            released_after = "not_held"
        elif not quarantined and first_error is None:
            released_after = "confirmed_cleanup"
        else:
            released_after = "quarantine_failure"
        admission = AdmissionOutcome(held=held, released=held, released_after=released_after)

        return SandboxLeaseReceipt(
            kind=policy.kind,
            sandbox_id=self._sandbox_id,
            interpreter=interpreter,
            provider=provider,
            admission=admission,
            quarantine=QuarantineOutcome(
                quarantined=quarantined,
                lane="owner_loop" if quarantined else "none",
                error=quarantine_error,
            ),
            duration_s=time.monotonic() - started,
            first_error=first_error,
        )

    async def _run_fallback_close(self) -> SandboxLeaseReceipt:
        try:
            receipt = await self._close_core(bounded_interpreter=False)
        except BaseException:
            self._close_task = None
            self._state = LeaseState.FAILED
            self._closed = False
            raise
        self._receipt = receipt
        self._close_task = None
        self._state = _receipt_state(receipt)
        self._closed = True
        return receipt

    async def _run_async_close(self) -> SandboxLeaseReceipt:
        current = asyncio.current_task()
        try:
            receipt = await self._close_core()
        except BaseException:
            async with self._close_lock:
                if self._close_task is current:
                    self._close_task = None
                    self._state = LeaseState.FAILED
                    self._closed = False
            raise
        async with self._close_lock:
            if self._close_task is current:
                self._receipt = receipt
                self._close_task = None
                self._state = _receipt_state(receipt)
                self._closed = True
        return receipt

    async def aclose(self, *, deadline: float | None = None) -> SandboxLeaseReceipt:
        task: asyncio.Future[SandboxLeaseReceipt] | None = None
        async with self._close_lock:
            if self._receipt is not None:
                return self._receipt
            task = self._close_task
            if task is None:
                coroutine = self._run_async_close()
                try:
                    task = asyncio.create_task(coroutine, name="fleet-sandbox-lease-close")
                except BaseException:
                    coroutine.close()
                    execution = schedule_owned_close(
                        loop=asyncio.get_running_loop(),
                        build=self._run_fallback_close,
                    )
                    task = asyncio.ensure_future(asyncio.wrap_future(execution.future))
                self._close_task = task
                _retain_close_task(task, self)
                self._state = LeaseState.CLOSING
                if task.done() and self._receipt is None:
                    failed = task.cancelled()
                    if not failed:
                        with contextlib.suppress(BaseException):
                            failed = task.exception() is not None
                    if failed:
                        self._close_task = None
                        self._state = LeaseState.FAILED
                        self._closed = False
        assert task is not None
        if deadline is None:
            return await asyncio.shield(task)
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("Sandbox lease cleanup timed out")
        return await asyncio.wait_for(asyncio.shield(task), timeout=remaining)

    async def wait_ownership(self, *, timeout: float | None = None) -> bool:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
        close_deadline = None
        if timeout is not None:
            close_deadline = asyncio.get_running_loop().time() + timeout
        try:
            await self.aclose(deadline=close_deadline)
        except TimeoutError:
            return False
        tasks = tuple(
            task
            for task in (
                self._close_task,
                self._deferred_close_task,
                *tuple(self._provider_tasks),
            )
            if task is not None and not task.done()
        )
        if not tasks:
            return not self.has_pending_ownership
        if timeout is None:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
        else:
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            if pending:
                return False
        return not self.has_pending_ownership


@dataclass(slots=True)
class OwnedCloseExecution:
    future: Future[Any]
    coroutine: Any | None = None


def schedule_owned_close(
    *,
    loop: asyncio.AbstractEventLoop,
    build: Callable[[], Coroutine[Any, Any, Any]],
) -> OwnedCloseExecution:
    coroutine = build()
    try:
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        return OwnedCloseExecution(future=future, coroutine=coroutine)
    except BaseException as exc:
        if inspect.iscoroutine(coroutine):
            coroutine.close()
        failure: Future[Any] = Future()
        _UNSCHEDULED_CLOSE_OWNERS[failure] = (loop, build)
        failure.set_exception(exc)
        return OwnedCloseExecution(future=failure)


async def _claim_session_lease(
    registry: ActiveLeaseRegistry, session_id: UUID, run_id: UUID, *, workspace_id: UUID, deadline: float
) -> None:
    loop = asyncio.get_running_loop()
    claim_wait_deadline = loop.time() + _PREWARM_CLAIM_WAIT_SECONDS
    while True:
        try:
            registry.acquire(session_id, run_id, workspace_id=workspace_id)
            return
        except ActiveLeaseConflictError as exc:
            if run_id == PREWARM_RUN_ID or exc.holder_run_id != PREWARM_RUN_ID:
                raise
        remaining = min(deadline, claim_wait_deadline) - loop.time()
        if remaining <= 0:
            raise DaytonaLeaseAcquisitionTimeoutError("Daytona lease acquisition timed out") from None
        await asyncio.sleep(min(0.2, remaining))


class ChildRuntimeLeaseState(StrEnum):
    """States observed by callers of a child runtime lease."""

    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


@dataclass(slots=True, eq=False)
class ChildRuntimeLease:
    """One synchronously usable child interpreter and its owned cleanup action."""

    interpreter: Any
    sandbox_id: str
    volume_id: str
    volume_subpath: str
    _close: Callable[[], None] = field(repr=False)
    _data_path: str = field(default="", repr=False, kw_only=True)
    _stage_files: Callable[[Mapping[str, bytes]], None] | None = field(default=None, repr=False, kw_only=True)
    _read_result_files: Callable[[Sequence[str]], Mapping[str, bytes]] | None = field(
        default=None, repr=False, kw_only=True
    )
    _on_closed: Callable[[ChildRuntimeLease], None] | None = field(default=None, repr=False)
    _state: ChildRuntimeLeaseState = field(default=ChildRuntimeLeaseState.OPEN, init=False, repr=False)
    _close_error: BaseException | None = field(default=None, init=False, repr=False)
    _condition: Condition = field(default_factory=Condition, init=False, repr=False)
    _closing_thread_id: int | None = field(default=None, init=False, repr=False)

    @property
    def state(self) -> ChildRuntimeLeaseState:
        with self._condition:
            return self._state

    @property
    def data_path(self) -> str:
        """Absolute child-local directory used by staging and result harvesting."""
        return self._data_path

    def stage_files(self, files: Mapping[str, bytes]) -> None:
        """Copy bounded relative inputs into this child's private data directory."""
        self._require_open()
        if self._stage_files is None:
            raise RuntimeError("child lease does not support private file staging")
        self._stage_files(files)

    def read_result_files(self, paths: Sequence[str]) -> Mapping[str, bytes]:
        """Read bounded relative result files before the child lease is closed."""
        self._require_open()
        if self._read_result_files is None:
            raise RuntimeError("child lease does not support result file harvesting")
        return self._read_result_files(paths)

    def _require_open(self) -> None:
        if self.state is not ChildRuntimeLeaseState.OPEN:
            raise RuntimeError("child lease file operations require an open lease")

    @property
    def close_error(self) -> BaseException | None:
        with self._condition:
            return self._close_error

    def close(self) -> None:
        with self._condition:
            if self._state is ChildRuntimeLeaseState.CLOSED:
                return
            if self._state is ChildRuntimeLeaseState.CLOSING:
                if self._closing_thread_id == get_ident():
                    raise RuntimeError("recursive child lease close is not reentrant")
                while self._state is ChildRuntimeLeaseState.CLOSING:
                    self._condition.wait()
                if self._state is ChildRuntimeLeaseState.CLOSED:
                    return
                if self._state is ChildRuntimeLeaseState.FAILED:
                    error = self._close_error
                    if error is None:
                        raise RuntimeError("recursive child lease close failed")
                    raise error
            if self._state is ChildRuntimeLeaseState.FAILED:
                error = self._close_error
                if error is None:
                    raise RuntimeError("recursive child lease close failed")
                raise error
            self._state = ChildRuntimeLeaseState.CLOSING
            self._closing_thread_id = get_ident()

        try:
            self._close()
        except BaseException as exc:
            with self._condition:
                self._close_error = exc
                self._state = ChildRuntimeLeaseState.FAILED
                self._closing_thread_id = None
                self._condition.notify_all()
            raise
        else:
            with self._condition:
                self._state = ChildRuntimeLeaseState.CLOSED
                self._closing_thread_id = None
                self._condition.notify_all()
            if self._on_closed is not None:
                self._on_closed(self)


class LateCleanupOwner:
    """Keep late provider work owned until its cleanup future settles."""

    def __init__(self, *, loop: asyncio.AbstractEventLoop, wait_timeout_s: float) -> None:
        self._loop = loop
        self._unresolved_leases: dict[int, Any] = {}
        self._lock = Lock()
        self._pending: set[Future[Any]] = set()
        self._error: BaseException | None = None
        self._wait_timeout_s = wait_timeout_s

    def _record_error(self, exc: BaseException) -> None:
        with self._lock:
            if self._error is None:
                self._error = exc

    def _state(self) -> tuple[BaseException | None, bool]:
        with self._lock:
            for future in tuple(self._pending):
                if not future.done():
                    continue
                try:
                    error = future.exception()
                except _CLEANUP_EXCEPTIONS as exc:
                    error = exc
                if error is not None and self._error is None:
                    self._error = error
                self._pending.discard(future)
            return self._error, any(not future.done() for future in self._pending)

    @staticmethod
    def _complete(marker: Future[None], error: BaseException | None = None) -> None:
        if marker.done():
            return
        if error is None:
            marker.set_result(None)
        else:
            marker.set_exception(error)

    def retain(self, future: Future[Any]) -> None:
        with self._lock:
            self._pending.add(future)

        def settled(done: Future[Any]) -> None:
            try:
                error = done.exception()
            except _CLEANUP_EXCEPTIONS as exc:
                self._record_error(exc)
            else:
                if error is not None:
                    self._record_error(error)
            with self._lock:
                self._pending.discard(done)

        future.add_done_callback(settled)

    def adopt_late_acquisition(
        self,
        acquisition: Future[Any],
        close_lease: Callable[[Any], None],
    ) -> None:
        marker: Future[None] = Future()
        self.retain(marker)

        def close_late(done: Future[Any]) -> None:
            try:
                lease = done.result()
            except ChildRuntimeCleanupError as exc:
                self._record_error(exc)
                self._complete(marker)
                return
            except _CLEANUP_EXCEPTIONS:
                self._complete(marker)
                return

            self._unresolved_leases[id(lease)] = lease

            async def close() -> None:
                try:
                    await asyncio.to_thread(close_lease, lease)
                except _CLEANUP_EXCEPTIONS as exc:
                    self._record_error(exc)
                else:
                    self._unresolved_leases.pop(id(lease), None)
                finally:
                    self._complete(marker)

            coroutine = close()
            try:
                if self._loop.is_closed() or not self._loop.is_running():
                    raise RuntimeError("child cleanup application loop is unavailable")
                cleanup = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
                self.retain(cleanup)
            except _CLEANUP_EXCEPTIONS as exc:
                coroutine.close()
                self._record_error(exc)
                self._complete(marker, exc)

        acquisition.add_done_callback(close_late)

    def raise_if_failed(self) -> None:
        error, pending = self._state()
        if error is not None:
            raise ChildRuntimeCleanupError("recursive child cleanup failed") from error
        if pending:
            raise ChildRuntimeCleanupError("recursive child cleanup is still pending")

    def has_unresolved(self) -> bool:
        with self._lock:
            return bool(self._unresolved_leases or any(not future.done() for future in self._pending))

    def wait_owned(self) -> None:
        wait_deadline = time.monotonic() + max(self._wait_timeout_s, 1.0)
        while True:
            with self._lock:
                pending = tuple(future for future in self._pending if not future.done())
            if not pending:
                break
            remaining = max(0.0, wait_deadline - time.monotonic())
            _, still_pending = wait(pending, timeout=remaining)
            if still_pending:
                self._record_error(TimeoutError("recursive child cleanup quarantine timed out"))
                break
        self.raise_if_failed()


async def purge_regular_files(sandbox: Any, mount_path: str) -> None:
    root = PurePosixPath(mount_path)
    entries = await sandbox.fs.list_files(str(root), depth=None)
    files: list[PurePosixPath] = []
    directories: list[PurePosixPath] = []
    for entry in entries:
        path = getattr(entry, "path", None)
        if not isinstance(path, str):
            continue
        candidate = PurePosixPath(path)
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        if not relative.parts:
            continue
        if bool(getattr(entry, "is_dir", False)):
            directories.append(candidate)
        else:
            files.append(candidate)

    for path in files:
        await sandbox.fs.delete_file(str(path))
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        await sandbox.fs.delete_file(str(path), recursive=True)


async def cleanup_after_failed_acquire(
    platform: SandboxPlatform,
    sandbox: Any | None,
    sandbox_id: str | None,
    permit: DaytonaAdmissionPermit,
    *,
    confirm: Callable[..., Awaitable[AbsenceOutcome]] | None = None,
    confirm_timeout_s: float = CHILD_DELETE_CONFIRM_TIMEOUT_S,
    confirm_poll_interval_s: float = CHILD_DELETE_CONFIRM_POLL_S,
) -> None:
    confirmed_absent = sandbox is None

    async def cleanup() -> None:
        nonlocal confirmed_absent
        if sandbox is None:
            return

        delete_error: Exception | None = None
        try:
            await platform.delete(sandbox_id if sandbox_id is not None else sandbox)
        except Exception as exc:
            delete_error = exc

        confirm_fn: Any = confirm or confirm_absence
        if sandbox_id is None:
            raise ChildRuntimeCleanupError("failed-acquire cleanup cannot confirm a sandbox without an id")
        try:
            outcome = await confirm_fn(
                probe=platform.get,
                sandbox_id=sandbox_id,
                timeout_s=confirm_timeout_s,
                poll_interval_s=confirm_poll_interval_s,
            )
        except TypeError:
            outcome = await confirm_fn(
                platform=platform,
                sandbox_id=sandbox_id,
                timeout_s=confirm_timeout_s,
                poll_interval_s=confirm_poll_interval_s,
            )
        is_absent = bool(getattr(outcome, "confirmed_absent", False) or getattr(outcome, "absent", False))
        confirmed_absent = is_absent
        if delete_error is not None:
            raise ChildRuntimeCleanupError(
                f"failed to delete child sandbox {sandbox_id}: {delete_error}"
            ) from delete_error
        if not is_absent:
            raise ChildRuntimeCleanupError(f"absence unconfirmed: failed-acquire child sandbox cleanup: {sandbox_id}")

    cleanup_effect = OwnedEffect.start(cleanup())
    try:
        await cleanup_effect.settle()
    finally:
        if confirmed_absent:
            permit.release()


async def cleanup_child_runtime_async(
    *,
    platform: SandboxPlatform,
    sandbox: Any,
    sandbox_id: str,
    mount_path: str | None,
    permit: DaytonaAdmissionPermit,
    confirm: Callable[..., Awaitable[AbsenceOutcome]] | None = None,
    confirm_timeout_s: float = CHILD_DELETE_CONFIRM_TIMEOUT_S,
    confirm_poll_interval_s: float = CHILD_DELETE_CONFIRM_POLL_S,
    purge: Callable[[Any, str], Awaitable[None]] | None = None,
) -> None:
    purge_fn = purge or purge_regular_files
    confirmed_absent = False
    try:
        if mount_path:
            await purge_fn(sandbox, mount_path)
            if mount_path.startswith("/tmp/fleet/"):
                await sandbox.fs.delete_file(mount_path, recursive=True)
        delete_error: Exception | None = None
        try:
            await platform.delete(sandbox_id)
        except Exception as exc:
            delete_error = exc
        confirm_fn: Any = confirm or confirm_absence
        try:
            outcome = await confirm_fn(
                probe=platform.get,
                sandbox_id=sandbox_id,
                timeout_s=confirm_timeout_s,
                poll_interval_s=confirm_poll_interval_s,
            )
        except TypeError:
            outcome = await confirm_fn(
                platform=platform,
                sandbox_id=sandbox_id,
                timeout_s=confirm_timeout_s,
                poll_interval_s=confirm_poll_interval_s,
            )
        is_absent = bool(getattr(outcome, "confirmed_absent", False) or getattr(outcome, "absent", False))
        confirmed_absent = is_absent
        if delete_error is not None:
            raise ChildRuntimeCleanupError(
                f"failed to delete child sandbox {sandbox_id}: {delete_error}"
            ) from delete_error
        if not is_absent:
            raise ChildRuntimeCleanupError(
                f"absence unconfirmed: recursive child sandbox deletion not confirmed absent: {sandbox_id}"
            )
    except ChildRuntimeCleanupError:
        raise
    except Exception as exc:
        raise ChildRuntimeCleanupError(f"cleanup failed: {exc}") from exc
    finally:
        if confirmed_absent:
            permit.release()


def close_child_runtime_sync(
    *,
    loop: Any,
    platform: SandboxPlatform,
    sandbox: Any,
    sandbox_id: str,
    mount_path: str | None,
    interpreter: Any,
    permit: DaytonaAdmissionPermit,
    retain_pending_cleanup: Callable[[Future[Any]], None] | None = None,
    cleanup_result_timeout_s: float = CHILD_CLEANUP_RESULT_TIMEOUT_S,
    cleanup_child_runtime: Callable[..., Coroutine[Any, Any, None]] | None = None,
    confirm_timeout_s: float = CHILD_DELETE_CONFIRM_TIMEOUT_S,
    confirm_poll_interval_s: float = CHILD_DELETE_CONFIRM_POLL_S,
) -> None:
    cleanup_fn = cleanup_child_runtime or cleanup_child_runtime_async

    async def close_on_owner_loop() -> None:
        shutdown_error: BaseException | None = None
        try:
            shutdown = OwnedEffect.start(asyncio.to_thread(interpreter.shutdown, strict_broker_cleanup=True))
            await shutdown.settle()
        except BaseException as exc:
            shutdown_error = exc
        try:
            await cleanup_fn(
                platform=platform,
                sandbox=sandbox,
                sandbox_id=sandbox_id,
                mount_path=mount_path,
                permit=permit,
                confirm_timeout_s=confirm_timeout_s,
                confirm_poll_interval_s=confirm_poll_interval_s,
            )
        except BaseException as exc:
            raise ChildRuntimeCleanupError("recursive child cleanup failed") from exc
        if shutdown_error is not None:
            raise ChildRuntimeCleanupError("recursive child interpreter shutdown failed") from shutdown_error

    execution = schedule_owned_close(loop=loop, build=close_on_owner_loop)
    if retain_pending_cleanup is not None:
        retain_pending_cleanup(execution.future)
    try:
        execution.future.result(timeout=cleanup_result_timeout_s)
    except _CLEANUP_EXCEPTIONS as exc:
        raise ChildRuntimeCleanupError("recursive child cleanup failed or remains unresolved") from exc


def _close_child_runtime_sync(
    *,
    loop: asyncio.AbstractEventLoop,
    platform: SandboxPlatform,
    sandbox: Any,
    sandbox_id: str,
    mount_path: str | None,
    interpreter: Any,
    permit: Any,
    retain_pending_cleanup: Callable[[Future[Any]], None] | None = None,
) -> None:
    close_child_runtime_sync(
        loop=loop,
        platform=platform,
        sandbox=sandbox,
        sandbox_id=sandbox_id,
        mount_path=mount_path,
        interpreter=interpreter,
        permit=permit,
        retain_pending_cleanup=retain_pending_cleanup,
        cleanup_result_timeout_s=_CHILD_CLEANUP_RESULT_TIMEOUT_S,
    )


def sandbox_id_for(sandbox: Any) -> str:
    value = getattr(sandbox, "id", None)
    if not isinstance(value, str) or not value:
        raise RuntimeError("recursive child sandbox is missing an id")
    return value


def _validate_child_relative_paths(paths: Sequence[str]) -> tuple[str, ...]:
    if isinstance(paths, (str, bytes)):
        raise TypeError("child file paths must be a sequence of relative paths")
    normalized: list[str] = []
    for raw_path in paths:
        if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
            raise ValueError("child file paths must be non-empty relative POSIX paths")
        path = PurePosixPath(raw_path)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in raw_path.split("/")):
            raise ValueError(f"child file path is not safely relative: {raw_path}")
        normalized_path = str(path)
        if normalized_path in normalized:
            raise ValueError("child file paths must be unique")
        normalized.append(normalized_path)
    if len(normalized) > _CHILD_STAGE_MAX_FILES:
        raise ValueError("too many child files")
    return tuple(normalized)


def _validate_child_file_mapping(files: Mapping[str, bytes], *, max_bytes: int) -> dict[str, bytes]:
    if not isinstance(files, Mapping):
        raise TypeError("child files must be a mapping of relative paths to bytes")
    paths = _validate_child_relative_paths(tuple(files))
    if len(paths) > _CHILD_STAGE_MAX_FILES:
        raise ValueError("too many child files")
    validated: dict[str, bytes] = {}
    total = 0
    for path in paths:
        content = files[path]
        if not isinstance(content, bytes):
            raise TypeError("child file contents must be bytes")
        total += len(content)
        if total > max_bytes:
            raise ValueError("child files exceed the configured size limit")
        validated[path] = content
    return validated


def _file_info_value(info: Any, name: str) -> Any:
    value = getattr(info, name, None)
    if value is not None:
        return value
    if isinstance(info, Mapping):
        value = info.get(name)
        if value is not None:
            return value
    additional = getattr(info, "additional_properties", None)
    if isinstance(additional, Mapping):
        return additional.get(name)
    return None


def _file_info_is_symlink(info: Any) -> bool:
    if info is None:
        return False
    if bool(_file_info_value(info, "is_symlink")) or bool(_file_info_value(info, "symlink")):
        return True
    kind = _file_info_value(info, "type")
    if isinstance(kind, str) and kind.lower() in {"symlink", "symbolic_link", "symbolic link"}:
        return True
    mode = _file_info_value(info, "mode")
    if isinstance(mode, str):
        try:
            normalized_mode = int(mode.strip(), 0 if mode.strip().startswith("0o") else 8)
        except ValueError:
            return False
    else:
        normalized_mode = mode
    return isinstance(normalized_mode, int) and normalized_mode & 0o170000 == 0o120000


async def _assert_no_child_symlink(fs: Any, root: str, relative: str = "") -> None:
    """Fail closed unless every existing result-path component is a regular path."""
    get_info = getattr(fs, "get_file_info", None)
    if not callable(get_info):
        raise ValueError("child result filesystem cannot verify symlink safety")
    current = PurePosixPath(root)
    paths = [str(current)]
    if relative:
        for part in PurePosixPath(relative).parts:
            current /= part
            paths.append(str(current))
    for path in paths:
        info = await _maybe_await(get_info(path))
        if _file_info_is_symlink(info):
            raise ValueError("child result path contains an unsafe symlink")


def require_authorized(is_authorized: Callable[[], bool] | None) -> None:
    if is_authorized is not None and not is_authorized():
        raise ChildRuntimeAuthorizationError("Turn is no longer authorized")


def _build_child_runtime_factory(
    *,
    owner: DaytonaRuntime,
    loop: asyncio.AbstractEventLoop,
    dispatcher: SyncBridgeDispatcher | None = None,
    platform: SandboxPlatform,
    admission: DaytonaAdmission,
    volume_id: str | None,
    mount_path: str | None,
    workspace_id: UUID,
    run_id: UUID,
    session_id: UUID | None = None,
    deadline: float,
    execution_timeout_s: int,
    execution_output_cap: int,
    is_authorized: Callable[[], bool] | None = None,
    profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.WORKSPACE_CHILD,
    semantic_child_available: bool = True,
) -> ChildRuntimeFactory:
    late_owner = LateCleanupOwner(loop=loop, wait_timeout_s=_CHILD_CLEANUP_RESULT_TIMEOUT_S)

    def create(
        call_index: int,
        *,
        selected_profile: DaytonaEnvironmentProfile | str | None = None,
    ) -> ChildRuntimeLease:
        chosen_profile = selected_profile if selected_profile is not None else profile
        if not isinstance(chosen_profile, DaytonaEnvironmentProfile):
            chosen_profile = DaytonaEnvironmentProfile(str(chosen_profile))
        if chosen_profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD and not semantic_child_available:
            raise ValueError("SemanticChild requires FLEET_DAYTONA_CHILD_SNAPSHOT")
        acquisition_coroutine = owner._acquire_child_runtime(
            loop=loop,
            dispatcher=dispatcher,
            platform=platform,
            admission=admission,
            volume_id=volume_id,
            mount_path=mount_path,
            profile=chosen_profile,
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=run_id,
            call_index=call_index,
            deadline=deadline,
            execution_timeout_s=execution_timeout_s,
            execution_output_cap=execution_output_cap,
            is_authorized=is_authorized,
            retain_pending_cleanup=late_owner.retain,
            interpreter_factory=DaytonaCodeInterpreter,
            sandbox_backend_factory=sandbox_backend,
            close_child_runtime=_close_child_runtime_sync,
            cleanup_after_failed_acquire=cleanup_after_failed_acquire,
        )
        try:
            acquisition = asyncio.run_coroutine_threadsafe(acquisition_coroutine, loop)
        except BaseException as exc:
            acquisition_coroutine.close()
            raise ChildRuntimeCleanupError("recursive child runtime acquisition failed") from exc
        try:
            return acquisition.result(timeout=max(0.0, deadline - time.monotonic()))
        except DaytonaAdmissionTimeoutError:
            if time.monotonic() < deadline:
                raise ChildRuntimeNotStartedError("child capacity is unavailable") from None
            raise TimeoutError("recursive child runtime acquisition deadline exceeded") from None
        except TimeoutError:
            late_owner.adopt_late_acquisition(acquisition, lambda lease: lease.close())
            raise TimeoutError("recursive child runtime acquisition deadline exceeded") from None

    class Factory:
        def __call__(
            self,
            call_index: int,
            *,
            profile: DaytonaEnvironmentProfile | str | None = None,
        ) -> ChildRuntimeLease:
            return create(call_index, selected_profile=profile)

        def wait_owned(self) -> None:
            late_owner.wait_owned()

        def raise_if_cleanup_failed(self) -> None:
            late_owner.raise_if_failed()

        def has_unresolved(self) -> bool:
            return late_owner.has_unresolved()

    return Factory()


_DAYTONA_CLOUD_API_URL = "https://app.daytona.io/api"


def build_daytona_client(settings: Settings) -> AsyncDaytona:
    """Construct the process-owned asynchronous Daytona SDK client."""
    from daytona import AsyncDaytona, DaytonaConfig

    api_key: str | None = None
    if settings.daytona_api_key is not None:
        raw = settings.daytona_api_key
        api_key = raw.get_secret_value() if hasattr(raw, "get_secret_value") else str(raw)
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


def _sandbox_fs(sandbox: Any) -> Any:
    return getattr(sandbox, "fs", sandbox)


async def read_file(sandbox: Any, path: str) -> bytes:
    data = await _maybe_await(_sandbox_fs(sandbox).download_file(path))
    return data.encode("utf-8") if isinstance(data, str) else bytes(data)


async def write_file(sandbox: Any, path: str, data: bytes) -> None:
    await _maybe_await(_sandbox_fs(sandbox).upload_file(data, path))


async def list_files(sandbox: Any, path: str, *, depth: int | None = 1) -> list[Any]:
    fs = _sandbox_fs(sandbox)
    try:
        entries = await _maybe_await(fs.list_files(path, depth=depth))
    except TypeError:
        entries = await _maybe_await(fs.list_files(path))
    return list(entries or [])


async def delete_file(sandbox: Any, path: str) -> None:
    await _maybe_await(_sandbox_fs(sandbox).delete_file(path))


async def get_file_info(sandbox: Any, path: str) -> Any:
    return await _maybe_await(_sandbox_fs(sandbox).get_file_info(path))


async def create_folder(sandbox: Any, path: str, mode: str = "755") -> None:
    await _maybe_await(_sandbox_fs(sandbox).create_folder(path, mode=mode))


class DaytonaRuntimeState(StrEnum):
    """Lifecycle of the process-scoped runtime facade."""

    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


def _identity_text(value: UUID | str | None, name: str) -> str:
    if value is None:
        raise ValueError(f"{name} is required")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _coerce_uuid(value: UUID | str, name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    text = _identity_text(value, name)
    try:
        return UUID(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid UUID: {text}") from exc


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _lease_fingerprint(lease: Any) -> object | None:
    spec = getattr(lease, "spec", None)
    if spec is not None and hasattr(spec, "context_fingerprint"):
        return spec.context_fingerprint
    return getattr(lease, "context_fingerprint", None)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


@dataclass(frozen=True, slots=True)
class RootSessionSpec:
    """Immutable identity and context selectors for one reusable root."""

    workspace_id: UUID | str
    session_id: UUID | str
    user_id: UUID | str | None = None
    run_id: UUID | str | None = None
    context_fingerprint: object | None = None
    deadline: float | None = None
    force_new: bool = False

    def __post_init__(self) -> None:
        _identity_text(self.workspace_id, "workspace_id")
        _identity_text(self.session_id, "session_id")
        if self.deadline is not None and not isinstance(self.deadline, (int, float)):
            raise TypeError("deadline must be numeric or None")

    @property
    def key(self) -> tuple[str, str]:
        """Return the stable root registry key."""
        return (_identity_text(self.workspace_id, "workspace_id"), _identity_text(self.session_id, "session_id"))

    @property
    def fingerprint(self) -> object | None:
        """Alias for the context selector used for root reuse."""
        return self.context_fingerprint


@dataclass(frozen=True, slots=True)
class ChildEnvironmentSpec:
    """Immutable selectors and bounds for one disposable child."""

    profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.WORKSPACE_CHILD
    workspace_id: UUID | str | None = None
    session_id: UUID | str | None = None
    run_id: UUID | str | None = None
    call_index: int = 0
    volume_id: str | None = None
    mount_path: str | None = None
    volume_subpath: str | None = None
    deadline: float | None = None
    execution_timeout_s: int | None = None
    execution_output_cap: int | None = None
    is_authorized: Callable[[], bool] | None = None

    def __post_init__(self) -> None:
        profile = self.profile
        if not isinstance(profile, DaytonaEnvironmentProfile):
            try:
                profile = DaytonaEnvironmentProfile(str(profile))
            except ValueError as exc:
                raise ValueError("unknown Daytona child environment profile") from exc
            object.__setattr__(self, "profile", profile)
        if profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD and (
            self.volume_id or self.mount_path or self.volume_subpath
        ):
            raise ValueError("SemanticChild cannot carry a Workspace Volume binding")
        if not isinstance(self.call_index, int) or isinstance(self.call_index, bool) or self.call_index < 0:
            raise ValueError("call_index must be a non-negative integer")
        if self.deadline is not None and not isinstance(self.deadline, (int, float)):
            raise TypeError("deadline must be numeric or None")

    @property
    def key(self) -> tuple[str, str] | None:
        if self.workspace_id is None or self.session_id is None:
            return None
        return (_identity_text(self.workspace_id, "workspace_id"), _identity_text(self.session_id, "session_id"))


async def _close_child_lease(lease: Any) -> Any:
    """Close a sync or async child lease."""
    close = getattr(lease, "close", None)
    if not callable(close):
        raise TypeError("child lease does not expose close()")
    if inspect.iscoroutinefunction(close):
        return await close()
    result = await asyncio.to_thread(close)
    if inspect.isawaitable(result):
        return await result
    return result


@contextlib.asynccontextmanager
async def _admitted(capacity: asyncio.Semaphore, deadline: float | None):
    """Hold one bounded capacity slot only around a provider operation."""
    if deadline is None:
        await capacity.acquire()
    else:
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        await asyncio.wait_for(capacity.acquire(), timeout=remaining)
    try:
        yield
    finally:
        capacity.release()


class SessionCleanupState(StrEnum):
    """Durable cleanup position of one runtime-owned session record."""

    ACTIVE = "ACTIVE"
    RELEASING = "RELEASING"
    RETIRED = "RETIRED"
    UNRESOLVED = "UNRESOLVED"


@dataclass(slots=True)
class DaytonaSessionRecord:
    """Runtime-owned resource record for one reusable session Sandbox.

    This is the Phase 2 preservation seam: workspace/session identity,
    Sandbox/Volume handles, binding generation, the active invocation, and
    cleanup state. It carries no prompts, tools, or durable turn state.
    """

    workspace_id: str
    session_id: str
    sandbox_id: str | None = None
    volume_id: str | None = None
    mount_path: str | None = None
    volume_subpath: str | None = None
    binding_generation: int = 0
    active_invocation_id: str | None = None
    cleanup_state: SessionCleanupState = SessionCleanupState.ACTIVE

    @property
    def key(self) -> tuple[str, str]:
        """Return the stable root registry key."""
        return (self.workspace_id, self.session_id)

    def begin_invocation(self, invocation_id: str) -> None:
        """Mark one invocation active; the Sandbox stays owned across it."""
        if self.active_invocation_id is not None:
            raise RuntimeError("session already has an active invocation")
        self.active_invocation_id = invocation_id

    def end_invocation(self, invocation_id: str) -> None:
        """Release the invocation marker without retiring the Sandbox."""
        if self.active_invocation_id != invocation_id:
            raise RuntimeError("invocation marker does not match the active invocation")
        self.active_invocation_id = None


class ChildEnvironment:
    """Async context-managed view over one strictly disposable child lease."""

    def __init__(
        self,
        spec: ChildEnvironmentSpec,
        lease: Any,
        *,
        sandbox: Any | None = None,
        on_closed: Callable[[RootSessionLease], Any] | None = None,
    ) -> None:
        self.spec = spec
        self.lease = lease
        self.sandbox = sandbox if sandbox is not None else getattr(lease, "sandbox", None)
        self.interpreter = getattr(lease, "interpreter", None)
        sandbox_id = getattr(lease, "sandbox_id", None) or getattr(self.sandbox, "id", None)
        self.sandbox_id = str(sandbox_id or "")
        self.volume_id = _optional_text(getattr(lease, "volume_id", None)) or spec.volume_id
        self.volume_subpath = _optional_text(getattr(lease, "volume_subpath", None)) or spec.volume_subpath
        self.mount_path = _optional_text(getattr(lease, "mount_path", None)) or spec.mount_path
        self._owner = RootSessionLease(
            spec.key or ("child", str(spec.call_index)),
            lease,
            _close_child_lease,
            on_closed=on_closed,
            sandbox=self.sandbox,
            interpreter=self.interpreter,
            volume=self.volume_id,
            volume_id=self.volume_id,
            mount_path=self.mount_path,
            volume_subpath=self.volume_subpath,
        )

    @property
    def state(self) -> LeaseState:
        return self._owner.state

    @property
    def status(self) -> LeaseState:
        return self.state

    @property
    def closed(self) -> bool:
        return self._owner.closed

    @property
    def closing(self) -> bool:
        return self._owner.closing

    @property
    def failed(self) -> bool:
        return self._owner.failed

    @property
    def close_error(self) -> BaseException | None:
        return self._owner.close_error

    async def close(self, *, deadline: float | None = None) -> None:
        await self._owner.close(deadline=deadline)

    async def __aenter__(self) -> ChildEnvironment:
        if self.state is not LeaseState.OPEN:
            raise RuntimeError("child environment is no longer open")
        return self

    async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        await self.close(deadline=self.spec.deadline)


class _ChildContext:
    """One-shot context object usable both directly and after ``await``."""

    def __init__(self, runtime: DaytonaRuntime, spec: ChildEnvironmentSpec) -> None:
        self._runtime = runtime
        self._spec = spec
        self._entered = False
        self._environment: ChildEnvironment | None = None

    def __await__(self):
        async def identity() -> _ChildContext:
            return self

        return identity().__await__()

    async def __aenter__(self) -> ChildEnvironment:
        if self._entered:
            raise RuntimeError("child context cannot be entered twice")
        self._entered = True
        self._environment = await self._runtime._acquire_child(self._spec)
        try:
            return await self._environment.__aenter__()
        except BaseException:
            await self._environment.close()
            raise

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._environment is not None:
            await self._environment.__aexit__(exc_type, exc, tb)


logger = logging.getLogger(__name__)


_PREWARM_CLAIM_WAIT_SECONDS = 60.0


async def _claim_session_lease(
    registry: ActiveLeaseRegistry, session_id: UUID, run_id: UUID, *, workspace_id: UUID, deadline: float
) -> None:
    loop = asyncio.get_running_loop()
    claim_wait_deadline = loop.time() + _PREWARM_CLAIM_WAIT_SECONDS
    while True:
        try:
            registry.acquire(session_id, run_id, workspace_id=workspace_id)
            return
        except ActiveLeaseConflictError as exc:
            if run_id == PREWARM_RUN_ID or exc.holder_run_id != PREWARM_RUN_ID:
                raise
        remaining = min(deadline, claim_wait_deadline) - loop.time()
        if remaining <= 0:
            raise DaytonaLeaseAcquisitionTimeoutError("Daytona lease acquisition timed out") from None
        await asyncio.sleep(min(0.2, remaining))


def _retain_provider_task(task: asyncio.Future[Any], owner: set[asyncio.Future[Any]]) -> None:
    owner.add(task)

    def settled(completed: asyncio.Future[Any]) -> None:
        owner.discard(completed)
        if not completed.cancelled():
            with contextlib.suppress(BaseException):
                completed.exception()

    task.add_done_callback(settled)


async def _provider_call(
    awaitable: Awaitable[Any],
    *,
    deadline: float | None,
    operation: str,
    owner: set[asyncio.Future[Any]] | None = None,
) -> Any:
    loop = asyncio.get_running_loop()
    if deadline is not None and deadline <= loop.time():
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise DaytonaLeaseAcquisitionTimeoutError(f"Daytona {operation} timed out") from None
    task = asyncio.ensure_future(awaitable)
    if deadline is None:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if owner is not None and not task.done():
                _retain_provider_task(task, owner)
            raise
    try:
        remaining = deadline - loop.time()
        if remaining <= 0:
            if owner is not None and not task.done():
                _retain_provider_task(task, owner)
            raise _ProviderCallDeadlineError(task, operation)
        return await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
    except TimeoutError:
        if task.done():
            return task.result()
        if owner is not None:
            _retain_provider_task(task, owner)
        raise _ProviderCallDeadlineError(task, operation) from None
    except asyncio.CancelledError:
        if owner is not None and not task.done():
            _retain_provider_task(task, owner)
        raise


async def _settle_provider_task(task: asyncio.Future[Any]) -> Any:
    await OwnedEffect.from_task(task).settle()
    return task.result()


DEFAULT_IDLE_STOP_SECONDS = 300.0


def _sandbox_id(sandbox: Any) -> str:
    sid = getattr(sandbox, "id", None)
    if sid is None:
        raise DaytonaAdapterError(message="sandbox missing id", cause_type="SandboxIdentityError")
    return str(sid)


def _build_interpreter(
    sandbox: Any,
    *,
    loop: asyncio.AbstractEventLoop,
    dispatcher: SyncBridgeDispatcher | None = None,
    execution_output_cap: int = DEFAULT_EXECUTION_OUTPUT_CHARS,
    execution_timeout_s: int = DEFAULT_EXECUTION_TIMEOUT_S,
) -> DaytonaCodeInterpreter:
    if hasattr(sandbox, "code_interpreter"):
        return DaytonaCodeInterpreter(
            backend=sandbox_backend(sandbox, loop=loop, dispatcher=dispatcher, timeout_s=execution_timeout_s),
            execution_output_cap=execution_output_cap,
        )
    existing = getattr(sandbox, "interpreter", None)
    if isinstance(existing, DaytonaCodeInterpreter):
        return existing
    return DaytonaCodeInterpreter(
        backend=getattr(sandbox, "backend", None),
        execution_output_cap=execution_output_cap,
    )


def binding_matches_expected(binding: SandboxBinding, expected: ExpectedWorkspaceMount) -> bool:
    try:
        require_non_zero_workspace_id(binding.workspace_id)
        if expected.session_id is None:
            require_scoped_volume_subpath(binding.volume_subpath, workspace_id=binding.workspace_id)
        else:
            require_session_workspace_subpath(
                binding.volume_subpath,
                workspace_id=binding.workspace_id,
                session_id=expected.session_id,
            )
    except (TypeError, ValueError):
        return False
    return (
        binding.workspace_id == expected.workspace_id
        and binding.volume_id == expected.volume_id
        and binding.volume_subpath == expected.volume_subpath
        and binding.mount_path == expected.mount_path
    )


@dataclass(slots=True)
class _LateOwner:
    request: LeaseRequest
    run_id: UUID
    permit: DaytonaAdmissionPermit | None = None
    acquisition: asyncio.Task[InterpreterLease] | None = None
    lease: InterpreterLease | None = None
    cleanup_task: Any | None = None
    cleanup_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    callback_started: bool = False
    callback_settled: bool = False
    unpublished: bool = False


@dataclass(slots=True)
class _ChildCleanupRecord:
    platform: SandboxPlatform
    sandbox: Any
    sandbox_id: str
    mount_path: str | None
    permit: DaytonaAdmissionPermit


class _ProviderCallDeadlineError(TimeoutError):
    def __init__(self, task: asyncio.Future[Any], operation: str) -> None:
        self.task = task
        self.operation = operation
        super().__init__(f"Daytona {operation} timed out")


@dataclass(slots=True)
class _AcquisitionContext:
    expected: ExpectedWorkspaceMount
    binding: SandboxBinding | None
    persisted_binding: SandboxBinding | None = None


class BindingStoreLike(Protocol):
    async def get(self, session_id: UUID) -> SandboxBinding | None: ...
    async def upsert(self, binding: SandboxBinding) -> SandboxBinding: ...


class DaytonaRuntime:
    """Public provider boundary for reusable root Sessions and child Environments."""

    def __init__(
        self,
        resources: Any | None = None,
        *,
        platform: Any = None,
        volume_client: Any = None,
        volume_config: Any = None,
        bindings: Any = None,
        admission: DaytonaAdmission | None = None,
        sandbox_spec: Any = None,
        cleanup: RunCleanupSupervisor | None = None,
        idle_stop_seconds: float | None = None,
        execution_output_cap: int = DEFAULT_EXECUTION_OUTPUT_CHARS,
        execution_timeout_s: int = DEFAULT_EXECUTION_TIMEOUT_S,
        dispatcher: SyncBridgeDispatcher | None = None,
        root_acquirer: Callable[..., Any] | None = None,
        root_factory: Callable[..., Any] | None = None,
        root_releaser: Callable[..., Any] | None = None,
        child_acquirer: Callable[..., Any] | None = None,
        child_factory: Callable[..., Any] | None = None,
        client: Any = None,
        settings: Any = None,
    ) -> None:
        self._resources = resources
        self._root_acquirer = root_acquirer or root_factory
        self._root_releaser = root_releaser
        self._child_acquirer = child_acquirer or child_factory
        self._roots: dict[tuple[str, str], RootSessionLease] = {}
        self._tainted: set[tuple[str, str]] = set()
        self._children: set[ChildEnvironment] = set()
        self._child_runtime_leases: set[ChildRuntimeLease] = set()
        self._child_cleanup_records: dict[str, _ChildCleanupRecord] = {}
        self._unidentified_child_sandboxes: list[tuple[SandboxPlatform, Any, DaytonaAdmissionPermit]] = []
        self._child_close_tasks: set[asyncio.Task[Any]] = set()
        self._child_factories: set[Any] = set()
        self._workspace_io_leases: set[SandboxLease] = set()
        self._workspace_io_active: set[SandboxLease] = set()
        self._workspace_io_tasks: set[asyncio.Task[Any]] = set()
        self._records: dict[tuple[str, str], DaytonaSessionRecord] = {}
        self._lock = asyncio.Lock()
        self._key_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._invocation_gates: dict[tuple[str, str], asyncio.Lock] = {}
        self._state = DaytonaRuntimeState.OPEN
        # Late provider acquisitions stay tracked until their resulting
        # Sandbox is released, stopped, or deleted and the durable binding
        # is fenced. A timed-out or cancelled create never loses ownership.
        self._late_tasks: set[asyncio.Task[Any]] = set()
        self._late_roots: set[RootSessionLease] = set()
        self._acquisitions: set[asyncio.Task[Any]] = set()
        # Bounded provider-capacity admission owned by the runtime. The
        # semaphore is only held around provider operations, never across a
        # network call for an unrelated session (the registry lock is
        # released before the provider call in acquire_root_session).
        self._capacity = asyncio.Semaphore(8)

        self._platform = platform
        self._volume_client = volume_client
        self._volume_config = volume_config
        self._bindings = bindings
        self._binding_authority = BindingGenerationAuthority()
        self._active_leases = ActiveLeaseRegistry()
        self._admission = admission or DaytonaAdmission()
        self._dispatcher = dispatcher
        self._application_loop: asyncio.AbstractEventLoop | None = None
        self._sandbox_spec = sandbox_spec
        self._cleanup = cleanup or RunCleanupSupervisor()
        self._execution_output_cap = execution_output_cap
        self._execution_timeout_s = execution_timeout_s
        if idle_stop_seconds is not None and idle_stop_seconds <= 0:
            raise ValueError("idle_stop_seconds must be positive")
        self._idle_stop_seconds = idle_stop_seconds
        self._idle_tasks: dict[tuple[UUID, UUID], asyncio.Task[None]] = {}
        self._owned_sandbox_ids: set[str] = set()
        self._owned_sandbox_lock = Lock()
        self._release_tasks: set[asyncio.Task[None]] = set()
        self._handled_release_tasks: set[asyncio.Task[None]] = set()
        self._release_leases: dict[asyncio.Task[None], InterpreterLease] = {}
        self._late_cleanup_tasks: set[Any] = set()
        self._late_owners: dict[int, _LateOwner] = {}
        self._provider_tasks: set[asyncio.Future[Any]] = set()
        self._client = client
        self._settings = settings if settings is not None else getattr(resources, "settings", None)
        self._tracked_sandbox_ids: list[str] = []
        self._client_close_lock = Lock()
        self._client_close_task: asyncio.Task[Any] | None = None
        self._client_closed = False
        self._provisioner = SandboxProvisioner(
            platform=platform, volume_config=volume_config, sandbox_spec=sandbox_spec
        )

        if platform is not None:
            self._root_acquirer = self._root_acquirer or self._acquire_from_resources
            self._root_releaser = self._root_releaser or self._release_from_resources
        if platform is not None or resources is not None:
            self._child_acquirer = self._child_acquirer or self._acquire_child_from_resources

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        environment_specs: dict[DaytonaEnvironmentProfile, DaytonaSandboxSpec],
        sandbox_spec: DaytonaSandboxSpec,
        bindings: BindingStoreLike,
        cleanup: RunCleanupSupervisor,
        max_active_leases: int,
        idle_stop_seconds: float | None,
        execution_output_cap: int,
        execution_timeout_s: int,
        dispatcher: SyncBridgeDispatcher | None,
    ) -> DaytonaRuntime:
        """Construct one process-owned SDK graph within the runtime boundary."""
        client = build_daytona_client(settings)
        platform = LiveDaytonaPlatform(client, sandbox_spec, environment_specs)
        volume_client = LiveDaytonaVolumeClient(client)
        volume_config = volume_config_from_settings(settings)
        return cls(
            platform=platform,
            volume_client=volume_client,
            volume_config=volume_config,
            bindings=bindings,
            admission=DaytonaAdmission(max_active_leases=max_active_leases),
            sandbox_spec=sandbox_spec,
            cleanup=cleanup,
            idle_stop_seconds=idle_stop_seconds,
            execution_output_cap=execution_output_cap,
            execution_timeout_s=execution_timeout_s,
            dispatcher=dispatcher,
            client=client,
            settings=settings,
        )

    @property
    def state(self) -> DaytonaRuntimeState:
        """Return the runtime facade lifecycle state."""
        return self._state

    @property
    def roots(self) -> tuple[RootSessionLease, ...]:
        """Return a view of retained root leases."""
        return tuple(self._roots.values())

    def owns_open_root(self, workspace_id: UUID | str | None, session_id: UUID | str) -> bool:
        """Return True when an OPEN root retains this Session."""
        sid = _identity_text(session_id, "session_id")
        if workspace_id is not None:
            try:
                owner = self._roots.get((_identity_text(workspace_id, "workspace_id"), sid))
            except ValueError:
                owner = None
            if owner is not None and not owner.closed:
                return True
        return any(
            not owner.closed and isinstance(owner.key, tuple) and len(owner.key) > 1 and str(owner.key[1]) == sid
            for owner in tuple(self._roots.values())
        )

    @property
    def children(self) -> tuple[ChildEnvironment, ...]:
        """Return a view of currently owned disposable children."""
        return tuple(self._children)

    async def begin_root_invocation(
        self, workspace_id: UUID, session_id: UUID, invocation_id: UUID, *, deadline: float
    ) -> Callable[[], None]:
        """Serialize a root invocation and retain its bounded identity until release."""
        if self._state is not DaytonaRuntimeState.OPEN:
            raise RuntimeError("Daytona runtime is not accepting invocations")
        key = (str(workspace_id), str(session_id))
        gate = self._invocation_gates.setdefault(key, asyncio.Lock())
        async with asyncio.timeout_at(deadline):
            await gate.acquire()
        if self._state is not DaytonaRuntimeState.OPEN:
            gate.release()
            raise RuntimeError("Daytona runtime is not accepting invocations")
        record = self._records.setdefault(key, DaytonaSessionRecord(*key))
        record.begin_invocation(str(invocation_id))
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            record.end_invocation(str(invocation_id))
            gate.release()

        return release

    async def wait_for_session_idle(self, workspace_id: UUID, session_id: UUID, *, deadline: float) -> None:
        gate = self._invocation_gates.get((str(workspace_id), str(session_id)))
        if gate is not None:
            async with asyncio.timeout_at(deadline):
                async with gate:
                    pass

    async def acquire_root_session(self, spec: RootSessionSpec) -> RootSessionLease:
        """Acquire or reuse the root for ``(workspace_id, session_id)``."""
        if not isinstance(spec, RootSessionSpec):
            raise TypeError("spec must be RootSessionSpec")
        if self._state is not DaytonaRuntimeState.OPEN:
            raise RuntimeError("Daytona runtime is not accepting root Sessions")

        key = spec.key
        async with self._lock:
            key_lock = self._key_locks.get(key)
            if key_lock is None:
                key_lock = asyncio.Lock()
                self._key_locks[key] = key_lock
        # One session's provider wait never blocks registry access for an
        # unrelated session: only this key is serialized, and the global
        # registry lock is held solely for short state checks and installs.
        async with key_lock:
            binding = None
            if self._bindings is not None:
                binding = await _provider_call(
                    self._bindings.get(UUID(key[1])),
                    deadline=spec.deadline,
                    operation="binding lookup",
                    owner=self._provider_tasks,
                )
            async with self._lock:
                if self._state is not DaytonaRuntimeState.OPEN:
                    raise RuntimeError("Daytona runtime is not accepting root Sessions")
                current = self._roots.get(key)
                must_replace = current is not None and (
                    current.state is not LeaseState.OPEN
                    or key in self._tainted
                    or spec.force_new
                    or _lease_fingerprint(current) != spec.context_fingerprint
                    or (
                        binding is not None
                        and (
                            binding.volume_subpath
                            != session_workspace_volume_subpath(
                                _coerce_uuid(spec.workspace_id, "workspace_id"),
                                _coerce_uuid(spec.session_id, "session_id"),
                            )
                            or binding.mount_path != EXECUTION_MOUNT_PATH
                        )
                    )
                )
                if current is None and key in self._tainted:
                    must_replace = True
                binding_changed = (
                    current is not None
                    and binding is not None
                    and (
                        binding.sandbox_id != current.sandbox_id
                        or binding.generation != getattr(current.lease, "binding_generation", None)
                    )
                )
                if current is not None and not must_replace and not binding_changed:
                    return current
                stale = current if must_replace or binding_changed else None

            if stale is not None:
                await stale.close(notify=False, deadline=spec.deadline)
                async with self._lock:
                    if self._roots.get(key) is stale:
                        self._roots.pop(key, None)

            raw = await self._acquire_root_from_provider(spec, force_new=must_replace or spec.force_new)
            owner = self._coerce_root(spec, raw)
            async with self._lock:
                if self._state is DaytonaRuntimeState.OPEN:
                    self._roots[key] = owner
                    self._tainted.discard(key)
                    self._sync_record(key, owner)
                    return owner
                self._late_roots.add(owner)
            await owner.close()
            self._late_roots.discard(owner)
            raise RuntimeError("Daytona runtime closed during root acquisition")

    async def discard_stale_root_session(
        self,
        workspace_id: UUID | str,
        session_id: UUID | str,
        *,
        deadline: float | None = None,
    ) -> None:
        """Close and drop a resident root after external replacement.

        The registry entry remains authoritative until the provider lease has
        closed successfully.  A replacement must not retire the remote
        Sandbox while this retained Root still owns its SessionManager lease.
        """
        key = (_identity_text(workspace_id, "workspace_id"), _identity_text(session_id, "session_id"))
        async with self._lock:
            owner = self._roots.get(key)
            if owner is None:
                return
            self._mark_record(key, SessionCleanupState.RELEASING)
        try:
            await owner.close(notify=False, deadline=deadline)
        except BaseException:
            async with self._lock:
                self._mark_record(key, SessionCleanupState.UNRESOLVED)
            raise
        async with self._lock:
            if self._roots.get(key) is owner:
                self._roots.pop(key, None)
            self._mark_record(key, SessionCleanupState.RETIRED)

    def mark_root_tainted(self, workspace_id: UUID | str, session_id: UUID | str) -> None:
        """Fence a root so the next acquisition rotates its generation."""
        key = (_identity_text(workspace_id, "workspace_id"), _identity_text(session_id, "session_id"))
        self._tainted.add(key)

    def session_record(self, workspace_id: UUID | str, session_id: UUID | str) -> DaytonaSessionRecord | None:
        """Return the runtime-owned resource record for one session, if any."""
        key = (_identity_text(workspace_id, "workspace_id"), _identity_text(session_id, "session_id"))
        return self._records.get(key)

    @property
    def has_pending_ownership(self) -> bool:
        """Whether roots, children, or late acquisitions remain unsettled."""
        return bool(
            self._roots
            or self._children
            or self._child_runtime_leases
            or self._child_cleanup_records
            or self._unidentified_child_sandboxes
            or self._child_factories
            or self._workspace_io_leases
            or self._workspace_io_active
            or any(not task.done() for task in self._workspace_io_tasks)
            or any(not task.done() for task in self._child_close_tasks)
            or self._late_roots
            or self._late_tasks
            or self._acquisitions
            or any(gate.locked() for gate in self._invocation_gates.values())
            or self._owned_sandbox_ids
            or self._late_owners
            or self._late_cleanup_tasks
            or any(not lease._released for lease in self._release_leases.values())
            or any(not task.done() for task in self._provider_tasks)
            or any(not task.done() for task in self._release_tasks)
            or any(not task.done() for task in self._idle_tasks.values())
        )

    def _sync_record(self, key: tuple[str, str], owner: RootSessionLease) -> None:
        """Refresh the resource record from the installed root lease."""
        record = self._records.get(key)
        if record is None:
            record = DaytonaSessionRecord(workspace_id=key[0], session_id=key[1])
            self._records[key] = record
        record.sandbox_id = str(owner.sandbox_id or "") or None
        record.volume_id = owner.volume_id
        record.mount_path = owner.mount_path
        record.volume_subpath = owner.volume_subpath
        generation = getattr(getattr(owner, "lease", None), "binding_generation", None)
        if isinstance(generation, int) and not isinstance(generation, bool):
            record.binding_generation = generation
        record.cleanup_state = SessionCleanupState.ACTIVE

    def _mark_record(self, key: tuple[str, str], state: SessionCleanupState) -> None:
        record = self._records.get(key)
        if record is not None:
            record.cleanup_state = state

    def _retain_late_acquisition(self, spec: RootSessionSpec, acquisition: asyncio.Task[Any]) -> None:
        """Retain the actual provider operation and close its unpublished result."""

        async def settle() -> None:
            try:
                raw = await asyncio.shield(acquisition)
            except BaseException:
                # Provider failure has no returned lease. The provider's
                # acquisition path retains any partially created resource.
                return
            owner = self._coerce_root(spec, raw)
            self._late_roots.add(owner)
            try:
                await owner.close()
            except BaseException:
                # Keep the concrete lease for shutdown retry.
                return
            self._late_roots.discard(owner)

        task = asyncio.create_task(settle(), name="fleet-daytona-late-acquisition-cleanup")
        self._late_tasks.add(task)
        task.add_done_callback(self._late_tasks.discard)

    def open_child(self, spec: ChildEnvironmentSpec) -> _ChildContext:
        """Return a disposable child context."""
        if not isinstance(spec, ChildEnvironmentSpec):
            raise TypeError("spec must be ChildEnvironmentSpec")
        return _ChildContext(self, spec)

    async def close_root_session(
        self,
        workspace_id: UUID | str,
        session_id: UUID | str,
        *,
        deadline: float | None = None,
    ) -> None:
        """Close one retained root."""
        key = (_identity_text(workspace_id, "workspace_id"), _identity_text(session_id, "session_id"))
        async with self._lock:
            owner = self._roots.get(key)
        if owner is None:
            return
        async with self._lock:
            self._mark_record(key, SessionCleanupState.RELEASING)
        try:
            await owner.close(deadline=deadline)
        except BaseException:
            async with self._lock:
                self._mark_record(key, SessionCleanupState.UNRESOLVED)
            raise
        async with self._lock:
            if owner.closed:
                self._mark_record(key, SessionCleanupState.RETIRED)

    async def aclose(self, *, deadline: float | None = None, drain_seconds: float = 30.0) -> bool:
        """Close all retained roots and active children.

        Registry entries remain owned until their close is confirmed.  This is
        important when cancellation or a provider failure interrupts shutdown:
        a later close call must be able to retry the same lease instead of
        losing the only reference to it.
        """
        if drain_seconds < 0:
            raise ValueError("drain_seconds must be non-negative")
        if deadline is None:
            deadline = asyncio.get_running_loop().time() + drain_seconds
        self._state = DaytonaRuntimeState.CLOSING
        errors: list[BaseException] = []

        while self._workspace_io_active and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(min(0.05, max(0.0, deadline - asyncio.get_running_loop().time())))
        if self._workspace_io_active:
            return False

        for lease in tuple(self._workspace_io_leases):
            try:
                await self._close_workspace_io_lease(lease, deadline=deadline)
            except BaseException as exc:
                errors.append(exc)
        io_tasks = tuple(task for task in self._workspace_io_tasks if not task.done())
        if io_tasks:
            await asyncio.wait(io_tasks, timeout=max(0.0, deadline - asyncio.get_running_loop().time()))

        for factory in tuple(self._child_factories):
            task = asyncio.create_task(asyncio.to_thread(factory.wait_owned), name="fleet-daytona-child-drain")
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            _done, pending = await asyncio.wait({task}, timeout=remaining)
            if pending:
                return False
            try:
                task.result()
            except BaseException as exc:
                errors.append(exc)
            else:
                self._child_factories.discard(factory)

        try:
            async with asyncio.timeout_at(deadline):
                for gate in tuple(self._invocation_gates.values()):
                    async with gate:
                        pass
        except TimeoutError:
            return False

        acquisitions = tuple(self._acquisitions)
        if acquisitions:
            timeout = None if deadline is None else max(0.0, deadline - asyncio.get_running_loop().time())
            await asyncio.wait(acquisitions, timeout=timeout)

        async with self._lock:
            children = tuple(self._children)
            child_runtime_leases = tuple(self._child_runtime_leases)
            roots = tuple(self._roots.values())

        for lease in child_runtime_leases:
            if lease.state is ChildRuntimeLeaseState.FAILED:
                continue
            task = asyncio.create_task(asyncio.to_thread(lease.close), name="fleet-daytona-child-lease-close")
            self._retain_child_task(task)
        if self._child_close_tasks:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            completed_child_closes, pending_child_closes = await asyncio.wait(
                tuple(self._child_close_tasks), timeout=remaining
            )
            for task in completed_child_closes:
                with contextlib.suppress(BaseException):
                    task.result()
            if pending_child_closes:
                return False
            self._child_runtime_leases = {
                lease
                for lease in self._child_runtime_leases
                if lease.state is not ChildRuntimeLeaseState.CLOSED and lease.sandbox_id in self._child_cleanup_records
            }

        await self._retry_child_cleanup_records(deadline=deadline)

        for child in children:
            try:
                await child.close(deadline=deadline)
            except BaseException as exc:
                errors.append(exc)
            else:
                if child.closed:
                    async with self._lock:
                        self._children.discard(child)

        for root in roots:
            try:
                await root.close(deadline=deadline)
            except BaseException as exc:
                errors.append(exc)
                async with self._lock:
                    self._mark_record(root.key, SessionCleanupState.UNRESOLVED)
            else:
                if root.closed:
                    async with self._lock:
                        if self._roots.get(root.key) is root:
                            self._roots.pop(root.key, None)
                        self._mark_record(root.key, SessionCleanupState.RETIRED)

        # Drain concrete late acquisitions without cancelling provider work.
        async with self._lock:
            late = tuple(task for task in self._late_tasks if not task.done())
        if late:
            if deadline is None:
                await asyncio.gather(*(asyncio.shield(task) for task in late), return_exceptions=True)
            else:
                remaining = max(0.0, deadline - asyncio.get_running_loop().time())
                if remaining > 0:
                    await asyncio.wait(set(late), timeout=remaining)
            async with self._lock:
                late_pending = [task for task in self._late_tasks if not task.done()]
        else:
            late_pending = []

        for owner in tuple(self._late_roots):
            try:
                await owner.close(deadline=deadline)
            except BaseException as exc:
                errors.append(exc)
            else:
                self._late_roots.discard(owner)

        provider_settled = await self._drain_provider_ownership(
            drain_seconds=max(0.0, deadline - asyncio.get_running_loop().time())
        )
        async with self._lock:
            retained = self.has_pending_ownership or bool(late_pending) or not provider_settled
            if not retained:
                self._key_locks.clear()
                self._invocation_gates.clear()
        self._state = DaytonaRuntimeState.FAILED if errors or retained else DaytonaRuntimeState.CLOSED
        return not errors and not retained

    def _workspace_io_lease(self, sandbox: Any, permit: DaytonaAdmissionPermit) -> SandboxLease:
        lease = SandboxLease(
            kind="volume_io",
            sandbox=sandbox,
            sandbox_id=getattr(sandbox, "id", None),
            platform=self._platform,
            permit=permit,
            policy=SandboxLeasePolicy(
                kind="volume_io",
                interpreter_shutdown=False,
                provider_request_timeout_s=_WORKSPACE_IO_DELETE_GRACE_SECONDS,
                confirm_timeout_s=_WORKSPACE_IO_DELETE_GRACE_SECONDS,
                confirm_poll_interval_s=0.5,
            ),
        )
        self._workspace_io_leases.add(lease)
        return lease

    def _retain_workspace_io_task(self, task: asyncio.Task[Any]) -> None:
        self._workspace_io_tasks.add(task)

        def settled(completed: asyncio.Task[Any]) -> None:
            self._workspace_io_tasks.discard(completed)
            if not completed.cancelled():
                with contextlib.suppress(BaseException):
                    completed.exception()

        task.add_done_callback(settled)

    async def _settle_workspace_io_lease(self, lease: SandboxLease) -> None:
        if await lease.wait_ownership():
            self._workspace_io_leases.discard(lease)

    async def _close_workspace_io_lease(self, lease: SandboxLease, *, deadline: float | None = None) -> None:
        close_task = asyncio.create_task(lease.aclose(deadline=deadline), name="fleet-daytona-workspace-io-close")
        self._retain_workspace_io_task(close_task)
        receipt = await asyncio.shield(close_task)
        if receipt.provider.confirmed_absent and not lease.has_pending_ownership:
            self._workspace_io_leases.discard(lease)
            return
        logger.warning(
            "Workspace I/O Sandbox deletion not confirmed absent within grace period",
            extra={"sandbox_id": lease._sandbox_id, "provider_error": receipt.provider.error},
        )
        task = asyncio.create_task(self._settle_workspace_io_lease(lease), name="fleet-daytona-workspace-io-settle")
        self._retain_workspace_io_task(task)

    @contextlib.asynccontextmanager
    async def open_workspace_sandbox(self, workspace_id: UUID, *, purpose: str) -> AsyncIterator[Any]:
        """Own one temporary mounted Sandbox through confirmed remote cleanup."""
        if self._state is not DaytonaRuntimeState.OPEN:
            raise RuntimeError("Daytona runtime is not accepting Workspace I/O")
        if self._volume_client is None or self._volume_config is None or self._platform is None:
            raise RuntimeError("Daytona Workspace I/O is not configured")
        volume_id = await get_or_create_volume_id(self._volume_client, self._volume_config)
        expected = self._provisioner.expected_mount(volume_id=volume_id, workspace_id=workspace_id)
        permit = await self._admission.acquire(deadline=float("inf"), host_io=True)
        if self._state is not DaytonaRuntimeState.OPEN:
            permit.release()
            raise RuntimeError("Daytona runtime closed during Workspace I/O admission")
        create_task = asyncio.create_task(
            self._provisioner.create(
                expected,
                labels={"fleet-package": "fleet_rlm", "purpose": purpose, "workspace_id": str(workspace_id)},
                ephemeral=True,
            ),
            name="fleet-daytona-workspace-io-create",
        )
        try:
            sandbox = await asyncio.shield(create_task)
        except BaseException:

            async def settle_late_create() -> None:
                try:
                    late_sandbox = await create_task
                except BaseException:
                    permit.release()
                    return
                await self._close_workspace_io_lease(self._workspace_io_lease(late_sandbox, permit))

            task = asyncio.create_task(settle_late_create(), name="fleet-daytona-workspace-io-late-create")
            self._retain_workspace_io_task(task)
            raise
        lease = self._workspace_io_lease(sandbox, permit)
        try:
            await sandbox.refresh_data()
            if sandbox_state(sandbox) != "running":
                raise RuntimeError("Workspace I/O Sandbox did not reach running state")
            self._provisioner.verify(sandbox, expected)
            await ensure_shared_volume_layout(sandbox, self._volume_config.paths())
            self._workspace_io_active.add(lease)
            yield sandbox
        finally:
            self._workspace_io_active.discard(lease)
            try:
                await self._close_workspace_io_lease(lease)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Workspace I/O cleanup remains runtime-owned",
                    extra={"sandbox_id": lease._sandbox_id, "error_type": type(exc).__name__},
                )

    def track_sandbox(self, sandbox_id: str | None) -> None:
        """Retain a concrete Sandbox identity until process disposal confirms absence."""
        if sandbox_id and sandbox_id not in self._tracked_sandbox_ids:
            self._tracked_sandbox_ids.append(sandbox_id)

    async def _cleanup_tracked_sandboxes(self, *, deadline: float) -> bool:
        retained: list[str] = []
        for sandbox_id in tuple(self._tracked_sandbox_ids):
            if self.owns_sandbox(sandbox_id):
                retained.append(sandbox_id)
                continue
            try:
                await _provider_call(
                    self._platform.delete(sandbox_id),
                    deadline=deadline,
                    operation="tracked Sandbox delete",
                    owner=self._provider_tasks,
                )
            except Exception as exc:
                if not is_sandbox_not_found(exc):
                    retained.append(sandbox_id)
                    continue
            try:
                observed = await _provider_call(
                    self._platform.get(sandbox_id),
                    deadline=deadline,
                    operation="tracked Sandbox absence probe",
                    owner=self._provider_tasks,
                )
            except Exception as exc:
                if not is_sandbox_not_found(exc):
                    retained.append(sandbox_id)
            else:
                if observed is not None:
                    retained.append(sandbox_id)
        self._tracked_sandbox_ids = retained
        return not retained

    async def _close_client(self, *, deadline: float) -> bool:
        if self._client is None or self._client_closed:
            return True
        with self._client_close_lock:
            task = self._client_close_task
            if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
                task = asyncio.create_task(self._client.close(), name="fleet-daytona-client-close")
                self._client_close_task = task
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, deadline - asyncio.get_running_loop().time()))
        except TimeoutError:
            return False
        else:
            self._client_closed = True
            return True

    def has_pending_cleanup(self) -> bool:
        return bool(
            self._tracked_sandbox_ids
            or self._workspace_io_leases
            or any(not task.done() for task in self._workspace_io_tasks)
            or any(not task.done() for task in self._provider_tasks)
            or (self._client_close_task is not None and not self._client_close_task.done())
        )

    async def wait_pending_cleanup(self, *, timeout: float | None = None) -> bool:
        tasks = tuple(
            task
            for task in (*self._workspace_io_tasks, *self._provider_tasks, self._client_close_task)
            if task is not None and not task.done()
        )
        if tasks:
            if any(task.get_loop() is not asyncio.get_running_loop() for task in tasks):
                return False
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            if pending:
                return False
        return not self.has_pending_cleanup()

    async def adispose(self, *, drain_seconds: float = 30.0) -> bool:
        """Settle runtime resources before closing the process-owned SDK client."""
        deadline = asyncio.get_running_loop().time() + drain_seconds
        settled = await self.aclose(deadline=deadline)
        tracked = await self._cleanup_tracked_sandboxes(deadline=deadline)
        if self.has_pending_cleanup():
            await self.wait_pending_cleanup(timeout=max(0.0, deadline - asyncio.get_running_loop().time()))
        if not settled or not tracked or self.has_pending_cleanup() or self.has_pending_ownership:
            return False
        return await self._close_client(deadline=deadline)

    async def close(self, *, deadline: float | None = None) -> bool:
        return await self.aclose(deadline=deadline)

    async def _acquire_root_from_provider(self, spec: RootSessionSpec, *, force_new: bool) -> Any:
        if spec.deadline is not None and spec.deadline <= asyncio.get_running_loop().time():
            raise TimeoutError("root Session acquisition timed out")
        acquirer = self._root_acquirer
        if acquirer is None:
            raise RuntimeError("no root acquirer configured")

        async def _call() -> Any:
            # Capacity is admitted only around this provider operation.
            # The registry lock is not held here (acquire_root_session
            # releases it before this call), so one session's network wait
            # never blocks an unrelated session's registry access.
            async with _admitted(self._capacity, spec.deadline):
                try:
                    sig = inspect.signature(acquirer)
                except (TypeError, ValueError):
                    sig = None
                if sig is not None and "force_new" in sig.parameters:
                    res = acquirer(spec, force_new=force_new)
                else:
                    res = acquirer(spec)
                return await _maybe_await(res)

        acquisition = asyncio.create_task(_call(), name="fleet-daytona-root-acquisition")
        self._acquisitions.add(acquisition)
        try:
            if spec.deadline is None:
                return await asyncio.shield(acquisition)
            remaining = max(0.0, spec.deadline - asyncio.get_running_loop().time())
            return await asyncio.wait_for(asyncio.shield(acquisition), timeout=remaining)
        except (TimeoutError, asyncio.CancelledError):
            self._retain_late_acquisition(spec, acquisition)
            raise
        finally:
            self._acquisitions.discard(acquisition)

    def _coerce_root(self, spec: RootSessionSpec, raw: Any) -> RootSessionLease:
        sandbox: Any | None = None
        candidate = raw
        if isinstance(raw, tuple) and len(raw) == 2:
            candidate, sandbox = raw
        if isinstance(candidate, RootSessionLease):
            candidate.spec = spec
            candidate.key = spec.key
            if sandbox is not None:
                candidate.sandbox = sandbox
            return candidate

        releaser = self._root_releaser
        if releaser is None:
            release_method = getattr(candidate, "release", None)
            if not callable(release_method):
                release_method = getattr(candidate, "close", None)
            if not callable(release_method):
                raise TypeError("root acquisition did not return a releasable lease")

            async def releaser(_lease: Any) -> Any:
                return await _maybe_await(release_method())

        return RootSessionLease(
            spec.key,
            candidate,
            releaser,
            spec=spec,
            sandbox=sandbox,
            interpreter=getattr(candidate, "interpreter", None),
            broker=getattr(candidate, "broker", None),
            volume=getattr(candidate, "volume", None),
            volume_id=getattr(candidate, "volume_id", None),
            mount_path=getattr(candidate, "mount_path", None),
            volume_subpath=getattr(candidate, "volume_subpath", None),
        )

    async def _acquire_child(self, spec: ChildEnvironmentSpec) -> ChildEnvironment:
        if self._state is not DaytonaRuntimeState.OPEN:
            raise RuntimeError("Daytona runtime is not accepting child Environments")
        acquirer = self._child_acquirer
        if acquirer is None:
            raise RuntimeError("Daytona child acquisition is unavailable")
        if spec.deadline is not None and spec.deadline <= asyncio.get_running_loop().time():
            raise TimeoutError("child Environment acquisition timed out")

        async def _call() -> Any:
            res = acquirer(spec)
            return await _maybe_await(res)

        if spec.deadline is None:
            raw = await _call()
        else:
            remaining = max(0.0, spec.deadline - asyncio.get_running_loop().time())
            raw = await asyncio.wait_for(_call(), timeout=remaining)

        environment = self._coerce_child(spec, raw)
        async with self._lock:
            environment._owner.on_closed = self._deregister_child
            self._children.add(environment)
            self._sync_child_record(environment)
        return environment

    def _coerce_child(self, spec: ChildEnvironmentSpec, raw: Any) -> ChildEnvironment:
        sandbox: Any | None = None
        candidate = raw
        if isinstance(raw, tuple) and len(raw) == 2:
            candidate, sandbox = raw
        if isinstance(candidate, ChildEnvironment):
            return candidate
        return ChildEnvironment(spec, candidate, sandbox=sandbox)

    def _sync_child_record(self, environment: ChildEnvironment) -> None:
        """Refresh the runtime-owned record for one disposable child lease."""
        spec = environment.spec
        if spec.workspace_id is None or spec.session_id is None:
            return
        key = (
            _identity_text(spec.workspace_id, "workspace_id"),
            _identity_text(spec.session_id, "session_id"),
        )
        record = self._records.get(key)
        if record is None:
            record = DaytonaSessionRecord(workspace_id=key[0], session_id=key[1])
            self._records[key] = record
        # A child record never replaces root Sandbox/Volume ownership; it only
        # tracks the disposable lease's cleanup position. Child reasoning
        # lives in rlm/recursion.py and requests leases here.
        record.cleanup_state = SessionCleanupState.ACTIVE

    async def _deregister_child(self, owner: RootSessionLease) -> None:
        """Forget a child only after its provider cleanup has succeeded."""
        async with self._lock:
            self._children = {child for child in self._children if child._owner is not owner}

    async def _acquire_from_resources(self, spec: RootSessionSpec, *, force_new: bool = False, **_kwargs: Any) -> Any:
        request = LeaseRequest(
            session_id=_coerce_uuid(spec.session_id, "session_id"),
            user_id=_coerce_uuid(spec.user_id or uuid4(), "user_id"),
            workspace_id=_coerce_uuid(spec.workspace_id, "workspace_id"),
            run_id=_coerce_uuid(spec.run_id, "run_id") if spec.run_id is not None else None,
        )
        deadline = spec.deadline if spec.deadline is not None else float("inf")
        lease = await self.acquire(request, deadline=deadline, force_new=force_new)
        sandbox = lease.sandbox
        if sandbox is None:
            raise RuntimeError("acquired Daytona Sandbox is unavailable")
        return lease, sandbox

    async def _release_from_resources(self, lease: Any) -> Any:
        return await self.release(lease)

    def _retain_child_task(self, task: asyncio.Task[Any]) -> None:
        self._child_close_tasks.add(task)

        def settled(completed: asyncio.Task[Any]) -> None:
            self._child_close_tasks.discard(completed)
            if not completed.cancelled():
                with contextlib.suppress(BaseException):
                    completed.exception()

        task.add_done_callback(settled)

    def _forget_child_cleanup_if_settled(self, sandbox_id: str) -> None:
        record = self._child_cleanup_records.get(sandbox_id)
        if record is not None and record.permit._released:
            self._child_cleanup_records.pop(sandbox_id, None)
            self._child_runtime_leases = {
                lease for lease in self._child_runtime_leases if lease.sandbox_id != sandbox_id
            }

    def _adopt_late_child_create(
        self,
        creation: asyncio.Task[Any],
        *,
        platform: SandboxPlatform,
        permit: DaytonaAdmissionPermit,
        mount_path: str | None,
        retain_pending_cleanup: Callable[[Future[Any]], None] | None,
    ) -> None:
        marker: Future[None] = Future()
        if retain_pending_cleanup is not None:
            retain_pending_cleanup(marker)

        async def settle() -> None:
            try:
                sandbox = await asyncio.shield(creation)
            except BaseException:
                permit.release()
                marker.set_result(None)
                return
            try:
                sandbox_id = sandbox_id_for(sandbox)
            except BaseException as exc:
                self._unidentified_child_sandboxes.append((platform, sandbox, permit))
                marker.set_exception(exc)
                return
            self._child_cleanup_records[sandbox_id] = _ChildCleanupRecord(
                platform, sandbox, sandbox_id, mount_path, permit
            )
            try:
                await cleanup_after_failed_acquire(platform, sandbox, sandbox_id, permit)
            except BaseException as exc:
                logger.warning("Late child creation cleanup remains runtime-owned", extra={"sandbox_id": sandbox_id})
                marker.set_exception(exc)
            else:
                marker.set_result(None)
            finally:
                self._forget_child_cleanup_if_settled(sandbox_id)

        task = asyncio.create_task(settle(), name="fleet-daytona-late-child-create")
        self._retain_child_task(task)

    async def _retry_child_cleanup_records(self, *, deadline: float) -> None:
        for sandbox_id, record in tuple(self._child_cleanup_records.items()):
            if record.permit._released:
                self._forget_child_cleanup_if_settled(sandbox_id)
                continue
            task = asyncio.create_task(
                cleanup_child_runtime_async(
                    platform=record.platform,
                    sandbox=record.sandbox,
                    sandbox_id=sandbox_id,
                    mount_path=record.mount_path,
                    permit=record.permit,
                ),
                name="fleet-daytona-child-cleanup-retry",
            )
            self._child_close_tasks.add(task)

            def settled(completed: asyncio.Task[Any], sid: str = sandbox_id) -> None:
                self._child_close_tasks.discard(completed)
                if not completed.cancelled():
                    with contextlib.suppress(BaseException):
                        completed.exception()
                self._forget_child_cleanup_if_settled(sid)

            task.add_done_callback(settled)
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            if remaining:
                await asyncio.wait({task}, timeout=remaining)

    async def _acquire_child_runtime(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        dispatcher: Any = None,
        platform: SandboxPlatform,
        admission: DaytonaAdmission,
        volume_id: str | None,
        mount_path: str | None,
        profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.WORKSPACE_CHILD,
        workspace_id: UUID,
        run_id: UUID,
        session_id: UUID | None = None,
        call_index: int,
        deadline: float,
        execution_timeout_s: int,
        execution_output_cap: int,
        is_authorized: Callable[[], bool] | None = None,
        retain_pending_cleanup: Callable[[Future[Any]], None] | None = None,
        interpreter_factory: Callable[..., Any] = DaytonaCodeInterpreter,
        sandbox_backend_factory: Callable[..., Any] = sandbox_backend,
        close_child_runtime: Callable[..., None] = _close_child_runtime_sync,
        cleanup_after_failed_acquire: Callable[..., Any] = cleanup_after_failed_acquire,
        sandbox_id_for_fn: Callable[[Any], str] | None = None,
        require_authorized_fn: Callable[[Callable[[], bool] | None], None] | None = None,
    ) -> ChildRuntimeLease:
        sandbox_id_resolver = sandbox_id_for_fn or sandbox_id_for
        authorization_check = require_authorized_fn or require_authorized
        authorization_check(is_authorized)
        if not isinstance(profile, DaytonaEnvironmentProfile):
            profile = DaytonaEnvironmentProfile(str(profile))
        semantic = profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD
        if not semantic and (not volume_id or not mount_path or session_id is None):
            raise ValueError("WorkspaceChild requires a Volume binding")
        if not semantic:
            assert session_id is not None
        admission_deadline = min(deadline, time.monotonic() + _CHILD_ADMISSION_WAIT_SECONDS)
        permit = await admission.acquire(deadline=admission_deadline)
        sandbox: Any | None = None
        sandbox_id: str | None = None
        if semantic:
            subpath = ""
        else:
            assert session_id is not None
            subpath = session_workspace_volume_subpath(workspace_id, session_id)
        scratch_path = f"/tmp/fleet/child-data/{run_id}/{call_index}"
        # Keep staged inputs and child outputs in the interpreter's
        # invocation-scoped scratch so model code can resolve the relative
        # paths from FLEET_RUN_SCRATCH.
        child_files_path = scratch_path
        try:
            authorization_check(is_authorized)
            labels = {"fleet.runtime": "recursive-child"}
            if semantic:
                labels["fleet.profile"] = profile.value
            create_kwargs: dict[str, Any] = {
                "profile": profile,
                "volume_id": None if semantic else volume_id,
                "mount_path": None if semantic else EXECUTION_MOUNT_PATH,
                "volume_subpath": None if semantic else subpath,
                "labels": labels,
                "with_volume": not semantic,
                "ephemeral": True,
            }
            if semantic:
                create_kwargs["network_block_all"] = True
            creation = asyncio.create_task(platform.create(**create_kwargs), name="fleet-daytona-child-create")
            try:
                async with asyncio.timeout_at(deadline):
                    sandbox = await asyncio.shield(creation)
            except (TimeoutError, asyncio.CancelledError):
                self._adopt_late_child_create(
                    creation,
                    platform=platform,
                    permit=permit,
                    mount_path=None if semantic else scratch_path,
                    retain_pending_cleanup=retain_pending_cleanup,
                )
                permit = None
                raise
            sandbox_id = sandbox_id_resolver(sandbox)
            child_sandbox_id = sandbox_id
            self._child_cleanup_records[child_sandbox_id] = _ChildCleanupRecord(
                platform, sandbox, child_sandbox_id, None if semantic else scratch_path, permit
            )
            authorization_check(is_authorized)
            if not semantic:
                fs = _sandbox_filesystem(sandbox)
                await _ensure_directories(
                    fs,
                    (
                        "/tmp/fleet",
                        "/tmp/fleet/child-data",
                        str(PurePosixPath(scratch_path).parent),
                        scratch_path,
                    ),
                )
            interpreter = interpreter_factory(
                backend=sandbox_backend_factory(
                    sandbox,
                    loop=loop,
                    dispatcher=dispatcher,
                    timeout_s=execution_timeout_s,
                ),
                execution_output_cap=execution_output_cap,
            )
            interpreter.bind_run_scratch(run_id, call_index=call_index)

            def run_fs(operation: Coroutine[Any, Any, Any]) -> Any:
                try:
                    future = asyncio.run_coroutine_threadsafe(operation, loop)
                except BaseException:
                    operation.close()
                    raise
                return future.result(timeout=max(0.0, deadline - time.monotonic()))

            def stage_child_files(files: Mapping[str, bytes]) -> None:
                validated = _validate_child_file_mapping(files, max_bytes=_CHILD_STAGE_MAX_BYTES)

                async def stage() -> None:
                    fs = _sandbox_filesystem(sandbox)
                    await _ensure_directories(
                        fs,
                        (
                            "/tmp/fleet",
                            "/tmp/fleet/child-data",
                            str(PurePosixPath(child_files_path).parent),
                            child_files_path,
                        ),
                    )
                    for relative, content in validated.items():
                        destination = f"{child_files_path}/{relative}"
                        parent = str(PurePosixPath(destination).parent)
                        await _ensure_directories(fs, (parent,))
                        await write_file(sandbox, destination, content)

                run_fs(stage())

            def read_child_results(paths: Sequence[str]) -> Mapping[str, bytes]:
                validated_paths = _validate_child_relative_paths(paths)

                async def read() -> Mapping[str, bytes]:
                    await _assert_no_child_symlink(_sandbox_filesystem(sandbox), child_files_path)
                    entries = await list_files(sandbox, child_files_path, depth=None)
                    if len(entries) > _CHILD_RESULT_MAX_ENTRIES:
                        raise ValueError("child result directory contains too many entries")
                    by_path: dict[str, Any] = {}
                    for entry in entries:
                        path = getattr(entry, "path", None)
                        if isinstance(path, str):
                            by_path[path.rstrip("/")] = entry
                    output: dict[str, bytes] = {}
                    total = 0
                    for relative in validated_paths:
                        target = f"{child_files_path}/{relative}"
                        entry = by_path.get(target)
                        if entry is None or bool(getattr(entry, "is_dir", False)):
                            raise ValueError(f"child result file is missing or not a file: {relative}")
                        await _assert_no_child_symlink(_sandbox_filesystem(sandbox), child_files_path, relative)
                        if _file_info_is_symlink(entry):
                            raise ValueError("child result path contains an unsafe symlink")
                        declared_size = getattr(entry, "size", None)
                        if isinstance(declared_size, int) and not isinstance(declared_size, bool):
                            if declared_size < 0 or declared_size > _CHILD_RESULT_MAX_BYTES:
                                raise ValueError("child result files exceed the configured size limit")
                            if total + declared_size > _CHILD_RESULT_MAX_BYTES:
                                raise ValueError("child result files exceed the configured size limit")
                        content = await read_file(sandbox, target)
                        total += len(content)
                        if len(content) > _CHILD_STAGE_MAX_BYTES or total > _CHILD_RESULT_MAX_BYTES:
                            raise ValueError("child result files exceed the configured size limit")
                        output[relative] = content
                    return output

                return run_fs(read())

            def close() -> None:
                try:
                    close_child_runtime(
                        loop=loop,
                        platform=platform,
                        sandbox=sandbox,
                        sandbox_id=child_sandbox_id,
                        mount_path=None if semantic else scratch_path,
                        interpreter=interpreter,
                        permit=permit,
                        retain_pending_cleanup=retain_pending_cleanup,
                    )
                finally:
                    loop.call_soon_threadsafe(self._forget_child_cleanup_if_settled, child_sandbox_id)

            return ChildRuntimeLease(
                interpreter,
                child_sandbox_id,
                "" if semantic else (volume_id or ""),
                subpath,
                close,
                _data_path=child_files_path,
                _stage_files=stage_child_files,
                _read_result_files=read_child_results,
            )
        except BaseException:
            if permit is None:
                raise
            if sandbox is not None and sandbox_id is None:
                with contextlib.suppress(BaseException):
                    sandbox_id = sandbox_id_for(sandbox)
                if sandbox_id is None:
                    self._unidentified_child_sandboxes.append((platform, sandbox, permit))
            if sandbox is not None and sandbox_id is not None and sandbox_id not in self._child_cleanup_records:
                self._child_cleanup_records[sandbox_id] = _ChildCleanupRecord(
                    platform, sandbox, sandbox_id, None if semantic else scratch_path, permit
                )
            try:
                cleanup = OwnedEffect.start(cleanup_after_failed_acquire(platform, sandbox, sandbox_id, permit))
                await cleanup.settle()
            except BaseException as cleanup_error:
                raise ChildRuntimeCleanupError("recursive child cleanup failed") from cleanup_error
            finally:
                if sandbox_id is not None:
                    self._forget_child_cleanup_if_settled(sandbox_id)
            raise

    async def _acquire_child_from_resources(self, spec: ChildEnvironmentSpec, **_kwargs: Any) -> Any:
        resources = self._resources
        platform = self._platform or getattr(resources, "platform", None)
        admission = self._admission
        if platform is None or admission is None:
            raise RuntimeError("Daytona child specification is incomplete")
        if spec.workspace_id is None or spec.run_id is None:
            raise RuntimeError("Daytona child specification is incomplete")
        if spec.profile is not DaytonaEnvironmentProfile.SEMANTIC_CHILD and (
            not spec.volume_id or not spec.mount_path or spec.session_id is None
        ):
            raise RuntimeError("WorkspaceChild specification requires a Volume binding")

        loop = asyncio.get_running_loop()
        settings = self._settings
        factory = self.build_child_factory(
            loop=loop,
            dispatcher=self._dispatcher,
            platform=platform,
            admission=admission,
            volume_id=spec.volume_id,
            mount_path=spec.mount_path,
            workspace_id=_coerce_uuid(spec.workspace_id, "workspace_id"),
            session_id=(_coerce_uuid(spec.session_id, "session_id") if spec.session_id is not None else None),
            run_id=_coerce_uuid(spec.run_id, "run_id"),
            deadline=spec.deadline if spec.deadline is not None else float("inf"),
            execution_timeout_s=(
                spec.execution_timeout_s
                if spec.execution_timeout_s is not None
                else execution_timeout_s_from_settings(settings)
            ),
            execution_output_cap=(
                spec.execution_output_cap
                if spec.execution_output_cap is not None
                else getattr(settings, "rlm_max_execution_output_chars", DEFAULT_EXECUTION_OUTPUT_CHARS)
            ),
            is_authorized=spec.is_authorized,
            profile=spec.profile,
        )
        return await asyncio.to_thread(factory, spec.call_index)

    def build_child_factory(self, **options: Any) -> ChildRuntimeFactory:
        """Build a synchronous recursion factory whose leases remain runtime-owned."""
        loop = options.pop("loop", None) or asyncio.get_running_loop()
        self._application_loop = loop
        options.setdefault("platform", self._platform)
        options.setdefault("admission", self._admission)
        options.setdefault("dispatcher", self._dispatcher)
        provider_factory = cast(Any, _build_child_runtime_factory(owner=self, loop=loop, **options))

        class RuntimeChildFactory:
            def __call__(self, call_index: int, *, profile: Any = None) -> Any:
                if self_runtime._state is not DaytonaRuntimeState.OPEN:
                    raise RuntimeError("Daytona runtime is not accepting child leases")
                lease = provider_factory(call_index, profile=profile)
                lease._on_closed = self_runtime._child_lease_closed
                registration = asyncio.run_coroutine_threadsafe(self_runtime._register_child_lease(lease), loop)
                configured_deadline = float(options.get("deadline", float("inf")))
                timeout = max(0.0, configured_deadline - loop.time()) if math.isfinite(configured_deadline) else None
                try:
                    registration.result(timeout=timeout)
                except BaseException:
                    lease.close()
                    raise
                return lease

            def wait_owned(self) -> None:
                try:
                    provider_factory.wait_owned()
                except ChildRuntimeCleanupError:
                    if provider_factory.has_unresolved() or self_runtime._child_cleanup_records:
                        raise
                loop.call_soon_threadsafe(self_runtime._child_factories.discard, self)

            def raise_if_cleanup_failed(self) -> None:
                provider_factory.raise_if_cleanup_failed()

        self_runtime = self
        factory = RuntimeChildFactory()
        self._child_factories.add(factory)
        return factory

    async def _register_child_lease(self, lease: ChildRuntimeLease) -> None:
        async with self._lock:
            if self._state is not DaytonaRuntimeState.OPEN:
                raise RuntimeError("Daytona runtime closed during child acquisition")
            self._child_runtime_leases.add(lease)

    def _child_lease_closed(self, lease: ChildRuntimeLease) -> None:
        loop = self._dispatcher.service_loop() if self._dispatcher is not None else None
        if loop is None:
            loop = self._application_loop
        if loop is not None and loop.is_running() and not loop.is_closed():
            loop.call_soon_threadsafe(self._child_runtime_leases.discard, lease)

    @property
    def active_leases(self) -> ActiveLeaseRegistry:
        return self._active_leases

    def _idle_stop_blocked(self, session_id: UUID, workspace_id: UUID | None) -> bool:
        if self._active_leases.holder(session_id, workspace_id=workspace_id) is not None:
            return True
        if self._active_leases.has_session(session_id):
            return True
        try:
            return self.owns_open_root(workspace_id, session_id)
        except (ValueError, TypeError):
            return True

    def _observe_binding(self, binding: SandboxBinding | None) -> None:
        if binding is None:
            return
        authority = getattr(self, "_binding_authority", None)
        if authority is not None:
            authority.observe(binding)

    def is_binding_current(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        sandbox_id: str,
        generation: int,
    ) -> bool:
        authority = getattr(self, "_binding_authority", None)
        if authority is None:
            return True
        return authority.is_current(
            session_id=session_id,
            workspace_id=workspace_id,
            sandbox_id=sandbox_id,
            generation=generation,
        )

    def revoke_binding(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        sandbox_id: str,
        generation: int,
    ) -> None:
        authority = getattr(self, "_binding_authority", None)
        if authority is not None:
            authority.revoke(
                session_id=session_id,
                workspace_id=workspace_id,
                sandbox_id=sandbox_id,
                generation=generation,
            )

    def _mark_sandbox_owned(self, sandbox_id: str) -> None:
        with self._owned_sandbox_lock:
            self._owned_sandbox_ids.add(sandbox_id)

    def _mark_sandbox_released(self, sandbox_id: str) -> None:
        with self._owned_sandbox_lock:
            self._owned_sandbox_ids.discard(sandbox_id)

    def owns_sandbox(self, sandbox_id: str) -> bool:
        with self._owned_sandbox_lock:
            return sandbox_id in self._owned_sandbox_ids

    async def prewarm_session(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        deadline: float | None = None,
    ) -> bool:
        effective_deadline = deadline if deadline is not None else asyncio.get_running_loop().time() + 120.0
        try:
            lease = await self.acquire(
                LeaseRequest(
                    session_id=session_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    run_id=PREWARM_RUN_ID,
                ),
                deadline=effective_deadline,
            )
        except ActiveLeaseConflictError:
            return False
        await self.release(lease)
        return True

    def schedule_prewarm(
        self,
        session_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
    ) -> asyncio.Task[None]:
        async def run_prewarm() -> None:
            try:
                await self.prewarm_session(session_id, user_id=user_id, workspace_id=workspace_id)
            except asyncio.CancelledError:
                raise
            except BaseException:
                pass

        task = asyncio.create_task(run_prewarm(), name=f"fleet-session-prewarm-{session_id}")
        _retain_provider_task(task, self._provider_tasks)
        return task

    def _expected_mount(self, *, volume_id: str, workspace_id: UUID) -> ExpectedWorkspaceMount:
        return self._provisioner.expected_mount(volume_id=volume_id, workspace_id=workspace_id)

    def _expected_execution_mount(
        self, *, volume_id: str, workspace_id: UUID, session_id: UUID
    ) -> ExpectedWorkspaceMount:
        return ExpectedWorkspaceMount(
            volume_id=str(volume_id),
            volume_subpath=session_workspace_volume_subpath(workspace_id, session_id),
            mount_path=EXECUTION_MOUNT_PATH,
            workspace_id=workspace_id,
            session_id=session_id,
        )

    def _sandbox_retirement_lease(
        self,
        sandbox_id: str,
        *,
        confirm_timeout_s: float = 120.0,
        provider_request_timeout_s: float | None = 30.0,
    ) -> SandboxLease:
        return SandboxLease(
            kind="retained_session",
            sandbox=None,
            sandbox_id=sandbox_id,
            platform=self._platform,
            policy=SandboxLeasePolicy(
                kind="retained_session",
                interpreter_shutdown=False,
                provider_action="delete",
                confirm_timeout_s=confirm_timeout_s,
                provider_request_timeout_s=provider_request_timeout_s,
            ),
        )

    async def acquire(
        self,
        request: LeaseRequest,
        *,
        deadline: float,
        force_new: bool = False,
    ) -> InterpreterLease:
        require_non_zero_workspace_id(request.workspace_id)
        run_id = request.run_id or uuid4()
        session_id = request.session_id
        await self._cancel_idle_stop(session_id, workspace_id=request.workspace_id, deadline=deadline)
        await _claim_session_lease(
            self._active_leases, session_id, run_id, workspace_id=request.workspace_id, deadline=deadline
        )
        claim_held = True
        permit: DaytonaAdmissionPermit | None = None
        try:
            permit = await self._admission.acquire(deadline=deadline)
            acquisition = asyncio.create_task(
                self._acquire_provider(request, run_id=run_id, deadline=deadline, force_new=force_new),
                name="fleet-daytona-provider-acquisition",
            )
            try:
                async with asyncio.timeout_at(deadline):
                    lease = await asyncio.shield(acquisition)
            except TimeoutError:
                self._adopt_late_acquisition(acquisition, permit, request, run_id)
                permit = None
                claim_held = False
                raise DaytonaLeaseAcquisitionTimeoutError("Daytona lease acquisition timed out") from None
            except asyncio.CancelledError:
                self._adopt_late_acquisition(acquisition, permit, request, run_id)
                permit = None
                claim_held = False
                raise

            self._bind_lease_ownership(
                lease,
                permit,
                session_id=session_id,
                workspace_id=request.workspace_id,
                run_id=run_id,
            )
            self._mark_sandbox_owned(lease.sandbox_id)
            return lease
        except BaseException:
            try:
                if permit is not None:
                    permit.release()
            finally:
                if claim_held:
                    self._active_leases.release(session_id, run_id, workspace_id=request.workspace_id)
            raise

    @staticmethod
    async def _settle_provider_acquisition(acquisition: asyncio.Task[InterpreterLease]) -> InterpreterLease:
        return await _settle_provider_task(acquisition)

    async def _settle_late_owner(self, owner: _LateOwner, *, deadline: float | None = None) -> None:
        if owner.unpublished:
            await self._finish_unpublished_lease(owner, deadline=deadline)
            return
        if owner.acquisition is not None and owner.lease is None:
            await self._settle_late_acquisition(owner)
            return
        await self._settle_late_lease(owner)

    async def _settle_late_acquisition(self, owner: _LateOwner) -> None:
        acquisition = owner.acquisition
        assert acquisition is not None
        if not acquisition.done():
            try:
                acquisition_loop = acquisition.get_loop()
            except BaseException:
                acquisition_loop = None
            if acquisition_loop is not asyncio.get_running_loop():
                return
        try:
            try:
                lease = await self._settle_provider_acquisition(acquisition)
            except BaseException:
                try:
                    if owner.permit is not None:
                        owner.permit.release()
                finally:
                    self._active_leases.release(
                        owner.request.session_id,
                        owner.run_id,
                        workspace_id=owner.request.workspace_id,
                    )
                return
            owner.lease = lease
            owner.acquisition = None
            if self._late_owners.get(id(acquisition)) is owner:
                self._late_owners.pop(id(acquisition), None)
            self._late_owners[id(lease)] = owner
            self._mark_sandbox_owned(lease.sandbox_id)
            await self._settle_late_lease(owner)
        finally:
            if acquisition.done() and self._late_owners.get(id(acquisition)) is owner:
                self._late_owners.pop(id(acquisition), None)

    def _track_late_cleanup(self, owner: _LateOwner, task: Any) -> None:
        owner.cleanup_task = task
        self._late_cleanup_tasks.add(task)
        task.add_done_callback(self._settled_late_cleanup)

    def _schedule_late_owner_fallback(self, owner: _LateOwner) -> bool:
        try:
            owner_loop = owner.acquisition.get_loop() if owner.acquisition is not None else None
        except BaseException:
            owner_loop = None
        if owner_loop is None or owner_loop.is_closed() or not owner_loop.is_running():
            # The late-owner registry retains the resource for a later drain;
            # never move a loop-bound acquisition/client to a fabricated loop.
            return False
        try:
            execution = schedule_owned_close(
                loop=owner_loop,
                build=lambda: self._settle_late_owner(owner),
            )
        except BaseException as exc:
            logger.critical("unable to retain late Daytona ownership cleanup", extra={"error_type": type(exc).__name__})
            return False
        self._track_late_cleanup(owner, execution.future)
        return True

    def _schedule_late_owner(self, owner: _LateOwner) -> bool:
        if owner.cleanup_task is not None and not owner.cleanup_task.done():
            return True
        awaitable = self._settle_late_owner(owner)
        try:
            task = self._cleanup.submit(awaitable)
        except BaseException:
            try:
                task = asyncio.create_task(awaitable, name="fleet-daytona-late-ownership-cleanup")
            except BaseException:
                with contextlib.suppress(BaseException):
                    awaitable.close()
                return self._schedule_late_owner_fallback(owner)
        self._track_late_cleanup(owner, task)
        return True

    def _adopt_late_acquisition(
        self,
        acquisition: asyncio.Task[InterpreterLease],
        permit: DaytonaAdmissionPermit,
        request: LeaseRequest,
        run_id: UUID,
    ) -> None:
        owner = _LateOwner(
            request=request,
            run_id=run_id,
            permit=permit,
            acquisition=acquisition,
        )
        self._late_owners[id(acquisition)] = owner
        if self._schedule_late_owner(owner):
            return

        def retry_after_settlement(_completed: asyncio.Future[Any]) -> None:
            if owner.cleanup_task is None:
                self._schedule_late_owner(owner)

        acquisition.add_done_callback(retry_after_settlement)

    async def _settle_late_lease(self, owner: _LateOwner) -> None:
        lease = owner.lease
        assert lease is not None
        assert owner.permit is not None
        try:
            release_task = asyncio.create_task(asyncio.to_thread(lease.release))
            await _settle_provider_task(release_task)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

        quarantine_error: BaseException | None = None
        # A failed interpreter/broker release cannot discard remote ownership.
        # Fence and retire the sandbox anyway; otherwise a transient local
        # shutdown error leaves the admission slot and provider resource live.
        try:
            await self._quarantine(
                lease,
                LeaseRequest(
                    session_id=owner.request.session_id,
                    user_id=owner.request.user_id,
                    workspace_id=UUID(str(lease.workspace_id)) if lease.workspace_id else UUID(int=0),
                    run_id=owner.run_id,
                ),
            )
        except asyncio.CancelledError as exc:
            quarantine_error = exc
        except Exception as exc:
            quarantine_error = exc

        if quarantine_error is not None:
            return

        owner.permit.release()
        self._active_leases.release(
            owner.request.session_id,
            owner.run_id,
            workspace_id=owner.request.workspace_id,
        )
        self._mark_sandbox_released(lease.sandbox_id)
        self._late_owners.pop(id(lease), None)

    async def _retry_late_owners(self, deadline: float) -> bool:
        current_loop = asyncio.get_running_loop()
        tasks: list[asyncio.Future[Any]] = []
        for owner in {id(o): o for o in self._late_owners.values()}.values():
            if owner.unpublished:
                awaitable = self._settle_late_owner(owner, deadline=deadline)
                try:
                    task = asyncio.create_task(awaitable, name="fleet-daytona-unpublished-lease-retry")
                except BaseException:
                    with contextlib.suppress(BaseException):
                        awaitable.close()
                    continue
                self._track_late_cleanup(owner, task)
                tasks.append(task)
                continue
            if owner.acquisition is not None and owner.lease is None:
                if not owner.acquisition.done():
                    try:
                        acquisition_loop = owner.acquisition.get_loop()
                    except BaseException:
                        acquisition_loop = None
                    if acquisition_loop is not current_loop:
                        continue
                task = owner.cleanup_task
                if task is None or task.done():
                    self._schedule_late_owner(owner)
                    task = owner.cleanup_task
                if task is not None:
                    tasks.append(task if isinstance(task, asyncio.Future) else asyncio.wrap_future(task))
                continue
            task = owner.cleanup_task
            if task is None or task.done():
                task = asyncio.create_task(self._settle_late_lease(owner), name="fleet-daytona-late-lease-retry")
                self._track_late_cleanup(owner, task)
            tasks.append(task)
        if not tasks:
            return not self._late_owners
        remaining = max(0.0, deadline - current_loop.time())
        _, pending = await asyncio.wait(tuple(tasks), timeout=remaining)
        return not pending and not self._late_owners

    def _settled_late_cleanup(self, task: Any) -> None:
        self._late_cleanup_tasks.discard(task)
        if task.cancelled():
            return
        with contextlib.suppress(BaseException):
            error = task.exception()
        if error is not None:
            logger.warning("late Daytona ownership cleanup failed", extra={"error_type": type(error).__name__})

    async def _quarantine(
        self, lease: InterpreterLease, request: LeaseRequest, *, deadline: float | None = None
    ) -> None:
        if lease.requires_sandbox_deletion:
            await self._persist_native_binding_state(
                lease,
                request,
                provider_state="fencing",
                deadline=deadline,
            )
            timeout_s = 30.0
            if deadline is not None:
                timeout_s = max(0.1, min(timeout_s, deadline - asyncio.get_running_loop().time()))
            retirement = self._sandbox_retirement_lease(
                lease.sandbox_id, confirm_timeout_s=timeout_s, provider_request_timeout_s=timeout_s
            )
            receipt = await retirement.aclose()
            if not receipt.clean:
                raise RuntimeError("native sandbox deletion was not confirmed")
            await self._persist_native_binding_state(
                lease,
                request,
                provider_state="quarantined",
                deadline=deadline,
            )
            return
        await self._fence_binding(
            SandboxBinding(
                session_id=request.session_id,
                sandbox_id=lease.sandbox_id,
                workspace_id=request.workspace_id,
                volume_id=lease.volume_id,
                volume_subpath=lease.volume_subpath or workspace_volume_subpath(request.workspace_id),
                mount_path=lease.mount_path,
                provider_state="running",
                generation=lease.binding_generation,
            ),
            deadline=deadline,
        )
        if lease.created_sandbox:
            retire = self._sandbox_retirement_lease(lease.sandbox_id)
            receipt_box: dict[str, SandboxLeaseReceipt] = {}

            async def _retire() -> None:
                receipt_box["receipt"] = await retire.aclose()

            deletion = _retire()
            try:
                deletion_task = self._cleanup.submit(deletion)
            except BaseException as scheduler_error:
                try:
                    deletion_task = asyncio.create_task(deletion, name="fleet-daytona-late-sandbox-retirement")
                except BaseException:
                    with contextlib.suppress(BaseException):
                        deletion.close()
                    raise RuntimeError("sandbox retirement ownership unavailable") from scheduler_error
            await deletion_task
            receipt = receipt_box["receipt"]
            if not receipt.clean:
                raise RuntimeError("sandbox retirement was not confirmed")

    async def _persist_native_binding_state(
        self,
        lease: InterpreterLease,
        request: LeaseRequest,
        *,
        provider_state: str,
        deadline: float | None = None,
    ) -> None:
        binding = await self._get_binding_for_workspace(
            request.session_id,
            request.workspace_id,
            deadline=deadline,
        )
        if binding is None or binding.sandbox_id != lease.sandbox_id or binding.generation != lease.binding_generation:
            return
        persisted = await _provider_call(
            self._bindings.upsert(replace(binding, provider_state=provider_state, last_verified_at=datetime.now(UTC))),
            deadline=deadline,
            operation=f"Native Sandbox {provider_state} persistence",
            owner=self._provider_tasks,
        )
        self._observe_binding(persisted)

    async def _get_binding_for_workspace(
        self,
        session_id: UUID,
        workspace_id: UUID,
        *,
        deadline: float | None = None,
    ) -> SandboxBinding | None:
        async def read(awaitable: Awaitable[Any]) -> Any:
            return await _provider_call(
                awaitable,
                deadline=deadline,
                operation="Sandbox binding lookup",
                owner=self._provider_tasks,
            )

        scoped_get = getattr(self._bindings, "get_scoped", None)
        if callable(scoped_get):
            binding = await read(scoped_get(session_id, workspace_id=workspace_id))
            if binding is not None:
                self._observe_binding(binding)
                return binding
            unscoped = await read(self._bindings.get(session_id))
            if unscoped is not None:
                raise DaytonaAdapterError(
                    message="sandbox binding does not match workspace scope",
                    cause_type="WorkspaceMountMismatch",
                )
            return None
        binding = await read(self._bindings.get(session_id))
        if binding is not None and binding.workspace_id != workspace_id:
            raise DaytonaAdapterError(
                message="sandbox binding does not match workspace scope",
                cause_type="WorkspaceMountMismatch",
            )
        self._observe_binding(binding)
        return binding

    async def fence_session(
        self,
        session_id: UUID,
        *,
        workspace_id: UUID | None = None,
        deadline: float | None = None,
    ) -> None:
        if workspace_id is not None:
            binding = await self._get_binding_for_workspace(session_id, workspace_id, deadline=deadline)
        else:
            binding = await _provider_call(
                self._bindings.get(session_id),
                deadline=deadline,
                operation="Sandbox binding lookup",
                owner=self._provider_tasks,
            )
        if binding is None or not binding.sandbox_id:
            return
        await self._fence_binding(binding, deadline=deadline)

    async def _fence_binding(self, binding: SandboxBinding, *, deadline: float | None = None) -> None:
        fenced = await _provider_call(
            self._bindings.upsert(replace(binding, provider_state="fencing", last_verified_at=datetime.now(UTC))),
            deadline=deadline,
            operation="Sandbox fence persistence",
            owner=self._provider_tasks,
        )
        self._observe_binding(fenced)
        if binding.sandbox_id is None:
            return
        timeout_s = 30.0
        if deadline is not None:
            timeout_s = max(0.1, min(timeout_s, deadline - asyncio.get_running_loop().time()))
        fence_lease = SandboxLease(
            kind="recovery_fence",
            sandbox=None,
            sandbox_id=binding.sandbox_id,
            platform=self._platform,
            policy=SandboxLeasePolicy(
                kind="recovery_fence",
                interpreter_shutdown=False,
                provider_action="stop",
                stop_force=True,
                confirm_timeout_s=timeout_s,
                provider_request_timeout_s=timeout_s,
            ),
        )

        async def _fenced_stop() -> None:
            receipt = await fence_lease.aclose()
            if receipt.first_error is not None:
                raise RuntimeError(str(receipt.first_error))

        await _provider_call(
            _fenced_stop(),
            deadline=deadline,
            operation="Sandbox fencing",
            owner=self._provider_tasks,
        )
        quarantined = await _provider_call(
            self._bindings.upsert(replace(binding, provider_state="quarantined", last_verified_at=datetime.now(UTC))),
            deadline=deadline,
            operation="Sandbox quarantine persistence",
            owner=self._provider_tasks,
        )
        self._observe_binding(quarantined)

    def _bind_lease_ownership(
        self,
        lease: InterpreterLease,
        permit: DaytonaAdmissionPermit,
        *,
        session_id: UUID,
        workspace_id: UUID,
        run_id: UUID,
    ) -> None:
        def _clear_active() -> None:
            try:
                permit.release()
            finally:
                self._mark_sandbox_released(lease.sandbox_id)
                self._active_leases.release(session_id, run_id, workspace_id=workspace_id)

        lease._on_release = _clear_active

    async def _acquire_provider(
        self,
        request: LeaseRequest,
        *,
        run_id: UUID,
        deadline: float | None = None,
        force_new: bool = False,
    ) -> InterpreterLease:
        context: _AcquisitionContext | None = None
        sandbox: Any | None = None
        created_sandbox = False
        try:
            context = await self._resolve_acquisition_context(request, deadline=deadline)
            sandbox, created_sandbox = await self._prepare_sandbox(
                request,
                context,
                deadline=deadline,
                force_new=force_new,
            )
            await self._verify_run_layout(
                sandbox, context.expected, request.session_id, run_id, created_sandbox, deadline=deadline
            )
            return await self._persist_binding_and_build_lease(
                request,
                run_id,
                context.expected,
                sandbox,
                created_sandbox,
                deadline=deadline,
                context=context,
            )
        except _ProviderCallDeadlineError as exc:
            with contextlib.suppress(BaseException):
                await _settle_provider_task(exc.task)
            if context is not None and sandbox is not None:
                await self._cleanup_failed_acquisition(
                    request,
                    sandbox,
                    created_sandbox=created_sandbox,
                    deadline=deadline,
                    binding=context.persisted_binding,
                )
            raise DaytonaLeaseAcquisitionTimeoutError(f"Daytona {exc.operation} timed out") from None
        except BaseException:
            if context is not None and sandbox is not None:
                await self._cleanup_failed_acquisition(
                    request,
                    sandbox,
                    created_sandbox=created_sandbox,
                    deadline=deadline,
                    binding=context.persisted_binding,
                )
            raise

    async def _resolve_acquisition_context(
        self, request: LeaseRequest, *, deadline: float | None = None
    ) -> _AcquisitionContext:
        volume_id = await self._resolve_volume_id(deadline=deadline)
        expected = self._expected_execution_mount(
            volume_id=volume_id,
            workspace_id=request.workspace_id,
            session_id=request.session_id,
        )
        binding = await self._get_binding_for_workspace(request.session_id, request.workspace_id, deadline=deadline)
        if binding is not None and binding.provider_state == "fencing":
            raise DaytonaAdapterError(
                message="sandbox execution fence is not confirmed",
                cause_type="SandboxFenceUnconfirmed",
            )
        return _AcquisitionContext(expected, binding)

    async def _prepare_sandbox(
        self,
        request: LeaseRequest,
        context: _AcquisitionContext,
        *,
        deadline: float | None = None,
        force_new: bool = False,
    ) -> tuple[Any, bool]:
        sandbox = await self._reuse_bound_sandbox(request, context, deadline=deadline, force_new=force_new)
        created_sandbox = sandbox is None or force_new
        if sandbox is None:
            sandbox = await self._create_sandbox(
                volume_id=context.expected.volume_id,
                mount_path=context.expected.mount_path,
                volume_subpath=context.expected.volume_subpath,
                request=request,
                deadline=deadline,
            )
        return sandbox, created_sandbox

    async def _reuse_bound_sandbox(
        self,
        request: LeaseRequest,
        context: _AcquisitionContext,
        *,
        deadline: float | None = None,
        force_new: bool = False,
    ) -> Any | None:
        binding = context.binding
        if binding is None or not binding.sandbox_id or binding.provider_state in {"quarantined", "unrecoverable"}:
            return None
        if not binding_matches_expected(binding, context.expected):
            if (
                binding.workspace_id == request.workspace_id
                and binding.volume_subpath == workspace_volume_subpath(request.workspace_id)
                and binding.mount_path == self._volume_config.mount_path
            ):
                return await self._replace_bound_sandbox(binding, request, deadline=deadline)
            raise DaytonaAdapterError(
                message="sandbox binding does not match Session workspace scope",
                cause_type="WorkspaceMountMismatch",
            )
        if force_new:
            return await self._replace_bound_sandbox(binding, request, deadline=deadline)
        sandbox = await self._get_bound_sandbox(binding.sandbox_id, deadline=deadline)
        if sandbox is None:
            return None
        try:
            self._provisioner.verify(sandbox, context.expected)
            sandbox = await self._ensure_running(
                sandbox,
                sandbox_state(sandbox),
                volume_id=context.expected.volume_id,
                mount_path=context.expected.mount_path,
                deadline=deadline,
            )
            self._provisioner.verify(sandbox, context.expected)
            return sandbox
        except ProviderRequestError:
            raise
        except DaytonaAdapterError as exc:
            if exc.cause_type not in {"SandboxUnrecoverable", "SandboxSnapshotMismatch"}:
                raise
            return await self._replace_bound_sandbox(binding, request, deadline=deadline, cause=exc)

    async def _replace_bound_sandbox(
        self,
        binding: SandboxBinding,
        request: LeaseRequest,
        *,
        deadline: float | None = None,
        cause: Exception | None = None,
    ) -> Any:
        replacement = await self.replace(
            replace(binding, provider_state="unrecoverable", last_verified_at=None),
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            deadline=deadline,
        )
        replacement_id = replacement.sandbox_id
        if not replacement_id:
            err = DaytonaAdapterError(
                message="sandbox replacement did not produce a sandbox id",
                cause_type="SandboxReplaceIdentityError",
            )
            raise err from cause if cause else err
        replacement_sandbox = await self._get_bound_sandbox(replacement_id, deadline=deadline)
        if replacement_sandbox is None:
            err = DaytonaAdapterError(
                message="replacement sandbox is not retrievable",
                cause_type="SandboxUnrecoverable",
            )
            raise err from cause if cause else err
        return replacement_sandbox

    async def _get_bound_sandbox(self, sandbox_id: str, *, deadline: float | None = None) -> Any | None:
        try:
            return await _provider_call(
                self._platform.get(sandbox_id),
                deadline=deadline,
                operation="Sandbox lookup",
                owner=self._provider_tasks,
            )
        except ProviderRequestError:
            raise
        except DaytonaAdapterError:
            raise
        except _ProviderCallDeadlineError:
            raise
        except Exception as exc:
            raise map_provider_error(exc) from exc

    async def _verify_run_layout(
        self,
        sandbox: Any,
        expected: ExpectedWorkspaceMount,
        session_id: UUID,
        run_id: UUID,
        created_sandbox: bool,
        deadline: float | None = None,
    ) -> None:
        del created_sandbox
        await _provider_call(
            self._provisioner.verify_run_layout(
                sandbox,
                expected,
                session_id=session_id,
                run_id=run_id,
            ),
            deadline=deadline,
            operation="Sandbox verification",
            owner=self._provider_tasks,
        )

    async def _cleanup_failed_acquisition(
        self,
        request: LeaseRequest,
        sandbox: Any,
        *,
        created_sandbox: bool,
        deadline: float | None = None,
        binding: SandboxBinding | None = None,
    ) -> None:
        sandbox_id = _sandbox_id(sandbox)
        candidate = binding
        durable_read_failed = False
        try:
            durable_binding = await self._get_binding_for_workspace(
                request.session_id,
                request.workspace_id,
                deadline=deadline,
            )
        except Exception:
            durable_read_failed = True
            durable_binding = None
        if not durable_read_failed:
            if (
                durable_binding is None
                or durable_binding.sandbox_id != sandbox_id
                or (
                    candidate is not None
                    and (
                        candidate.sandbox_id != durable_binding.sandbox_id
                        or candidate.generation != durable_binding.generation
                    )
                )
            ):
                candidate = None
            else:
                candidate = durable_binding
        if candidate is not None and candidate.sandbox_id == sandbox_id:
            state = "quarantined" if created_sandbox else "fencing"
            with contextlib.suppress(BaseException):
                fenced = await _provider_call(
                    self._bindings.upsert(replace(candidate, provider_state=state, last_verified_at=None)),
                    deadline=deadline,
                    operation="Failed Sandbox fencing persistence",
                    owner=self._provider_tasks,
                )
                self._observe_binding(fenced)

        interpreter: DaytonaCodeInterpreter | None = None
        with contextlib.suppress(BaseException):
            interpreter = _build_interpreter(
                sandbox,
                loop=asyncio.get_running_loop(),
                dispatcher=self._dispatcher,
                execution_output_cap=self._execution_output_cap,
                execution_timeout_s=self._execution_timeout_s,
            )

        cleanup = SandboxLease(
            kind="retained_session" if created_sandbox else "recovery_fence",
            sandbox=sandbox,
            sandbox_id=sandbox_id,
            platform=self._platform,
            interpreter=interpreter,
            policy=SandboxLeasePolicy(
                kind="retained_session" if created_sandbox else "recovery_fence",
                provider_action="delete" if created_sandbox else "stop",
                stop_force=not created_sandbox,
                confirm_timeout_s=30.0,
                provider_request_timeout_s=30.0,
            ),
        )
        try:
            receipt = await cleanup.aclose(deadline=deadline)
            if not receipt.clean:
                with contextlib.suppress(BaseException):
                    await cleanup.wait_ownership()
        except BaseException:
            with contextlib.suppress(BaseException):
                await cleanup.wait_ownership()

    async def _persist_binding_and_build_lease(
        self,
        request: LeaseRequest,
        run_id: UUID,
        expected: ExpectedWorkspaceMount,
        sandbox: Any,
        created_sandbox: bool,
        deadline: float | None = None,
        context: _AcquisitionContext | None = None,
    ) -> InterpreterLease:
        session_id = request.session_id
        sid = _sandbox_id(sandbox)
        prior_binding = await self._get_binding_for_workspace(session_id, request.workspace_id, deadline=deadline)
        if prior_binding is None:
            binding_generation = 1
        elif (
            prior_binding.sandbox_id == sid
            and prior_binding.provider_state == "running"
            and self.is_binding_current(
                session_id=session_id,
                workspace_id=request.workspace_id,
                sandbox_id=sid,
                generation=prior_binding.generation,
            )
        ):
            binding_generation = prior_binding.generation
        else:
            binding_generation = prior_binding.generation + 1
        candidate = SandboxBinding(
            session_id=session_id,
            sandbox_id=sid,
            workspace_id=request.workspace_id,
            volume_id=expected.volume_id,
            volume_subpath=expected.volume_subpath,
            mount_path=expected.mount_path,
            provider_state="running",
            last_verified_at=datetime.now(UTC),
            generation=binding_generation,
        )
        atomic_replace = getattr(self._bindings, "replace_with_next_generation", None)
        is_replacement = prior_binding is not None and prior_binding.sandbox_id != sid
        persist = (
            atomic_replace(candidate)
            if is_replacement and callable(atomic_replace)
            else self._bindings.upsert(candidate)
        )
        persisted = await _provider_call(
            persist,
            deadline=deadline,
            operation="Sandbox binding persistence",
            owner=self._provider_tasks,
        )
        self._observe_binding(persisted)
        if context is not None:
            context.persisted_binding = persisted
        binding_generation = persisted.generation
        interpreter = _build_interpreter(
            sandbox,
            loop=asyncio.get_running_loop(),
            dispatcher=self._dispatcher,
            execution_output_cap=self._execution_output_cap,
            execution_timeout_s=self._execution_timeout_s,
        )
        return InterpreterLease(
            sandbox_id=sid,
            interpreter_id=f"interp-{sid}-{uuid4().hex[:8]}",
            volume_id=expected.volume_id,
            mount_path=expected.mount_path,
            volume_subpath=expected.volume_subpath,
            interpreter=interpreter,
            sandbox=sandbox,
            session_id=str(session_id),
            user_id=str(request.user_id),
            run_id=str(run_id),
            workspace_id=str(request.workspace_id),
            created_sandbox=created_sandbox,
            binding_generation=binding_generation,
        )

    def _start_release_task(self, lease: InterpreterLease) -> asyncio.Task[None]:
        for task, known in tuple(self._release_leases.items()):
            if known is lease and not task.done():
                return task
        release_task = asyncio.create_task(
            asyncio.to_thread(lease.release),
            name="fleet-daytona-interpreter-release",
        )
        self._release_tasks.add(release_task)
        self._release_leases[release_task] = lease
        release_task.add_done_callback(lambda task: self._settled_release_task(lease, task))
        return release_task

    async def _release_interpreter(self, lease: InterpreterLease) -> None:
        release_task = self._start_release_task(lease)
        await asyncio.shield(release_task)
        self._settled_release_task(lease, release_task)

    async def release(self, lease: InterpreterLease) -> None:
        unpublished = self._late_owners.get(id(lease))
        if unpublished is not None and unpublished.unpublished:
            await self._finish_unpublished_lease(unpublished)
            return
        if lease.requires_sandbox_deletion and not lease._provider_retired:
            if lease.session_id is None or lease.workspace_id is None or lease.run_id is None or lease.user_id is None:
                raise RuntimeError("native sandbox retirement requires complete lease ownership")
            await self.release_and_quarantine(
                lease,
                LeaseRequest(
                    session_id=UUID(lease.session_id),
                    workspace_id=UUID(lease.workspace_id),
                    user_id=UUID(lease.user_id),
                    run_id=UUID(lease.run_id),
                ),
            )
            return
        try:
            await self._release_interpreter(lease)
        except BaseException:
            if lease.session_id is None or lease.workspace_id is None or lease.run_id is None or lease.user_id is None:
                raise
            # A failed broker/interpreter shutdown still owns a live remote
            # resource.  Retain it behind the existing durable fencing and
            # quarantine path instead of merely logging a failed task.  Keep
            # the original failure visible to the active caller; ``aclose``
            # owns the bounded retry and final provider retirement.
            self._retain_unpublished_lease(
                lease,
                LeaseRequest(
                    session_id=UUID(lease.session_id),
                    workspace_id=UUID(lease.workspace_id),
                    user_id=UUID(lease.user_id),
                    run_id=UUID(lease.run_id),
                ),
            )
            raise

    async def _finish_unpublished_lease(
        self,
        owner: _LateOwner,
        *,
        deadline: float | None = None,
    ) -> None:
        async with owner.cleanup_lock:
            lease = owner.lease
            assert lease is not None
            if lease._provider_retired:
                self._late_owners.pop(id(lease), None)
                return
            lease._defer_owner_release = True
            lease._defer_idle_cleanup = True
            release_error: BaseException | None = None
            prior_release_failed = any(
                known is lease and task.done() and not task.cancelled() and task.exception() is not None
                for task, known in self._release_leases.items()
            )
            if not lease._released and not prior_release_failed:
                try:
                    await self._release_interpreter(lease)
                except BaseException as exc:
                    release_error = exc
            # Provider retirement is still required after a broker shutdown
            # failure. It is the containment fallback for a lease whose local
            # release could not be confirmed.
            await self._quarantine(lease, owner.request, deadline=deadline)
            if release_error is not None or prior_release_failed:
                # Provider retirement has contained the failed local release.
                # Mark the lease terminal so future shutdown retries cannot
                # reopen a broker that no longer has a remote owner.
                lease._released = True
                lease._state = LeaseState.CLOSED

            callback = lease._on_release
            if not owner.callback_settled:
                if owner.callback_started:
                    raise RuntimeError("unpublished lease finalization remains unresolved")
                owner.callback_started = True
                try:
                    if callback is not None:
                        callback()
                except BaseException:
                    raise
                owner.callback_settled = True
            lease._provider_retired = True
            lease._defer_owner_release = False
            lease._defer_idle_cleanup = False
            self._late_owners.pop(id(lease), None)
            if release_error is not None:
                logger.info(
                    "Daytona interpreter release was contained by sandbox retirement",
                    extra={"sandbox_id": lease.sandbox_id, "error_type": type(release_error).__name__},
                )

    async def release_and_quarantine(
        self,
        lease: InterpreterLease,
        request: LeaseRequest,
        *,
        deadline: float | None = None,
    ) -> None:
        owner = self._retain_unpublished_lease(lease, request)
        await self._finish_unpublished_lease(owner, deadline=deadline)

    def _retain_unpublished_lease(self, lease: InterpreterLease, request: LeaseRequest) -> _LateOwner:
        """Keep failed interpreter release attached to durable cleanup ownership."""
        owner = self._late_owners.get(id(lease))
        if owner is None or not owner.unpublished:
            owner = _LateOwner(
                request=request,
                run_id=request.run_id or UUID(int=0),
                lease=lease,
                unpublished=True,
            )
            self._late_owners[id(lease)] = owner
        else:
            owner.request = request
        return owner

    async def quarantine(
        self,
        lease: InterpreterLease,
        request: LeaseRequest,
        *,
        deadline: float | None = None,
    ) -> None:
        await self._quarantine(lease, request, deadline=deadline)

    def _settled_release_task(self, lease: InterpreterLease, task: asyncio.Task[None]) -> None:
        if task in self._handled_release_tasks:
            self._handled_release_tasks.discard(task)
            return
        self._handled_release_tasks.add(task)
        self._release_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except BaseException as exc:
            logger.warning(
                "Daytona interpreter release failed",
                extra={"sandbox_id": lease.sandbox_id, "error_type": type(exc).__name__},
            )
            return
        for owned_task, owned_lease in tuple(self._release_leases.items()):
            if owned_lease is lease:
                self._release_leases.pop(owned_task, None)
        if lease._defer_idle_cleanup or lease._provider_retired:
            return
        if self._idle_stop_seconds is None or lease.session_id is None:
            return
        session_id = UUID(lease.session_id)
        workspace_id = UUID(lease.workspace_id) if lease.workspace_id else UUID(int=0)
        idle_key = self._idle_key(session_id, workspace_id)
        self._request_cancel_idle_stop(session_id, workspace_id=workspace_id)
        idle_task = asyncio.create_task(
            self._stop_after_idle(
                session_id=session_id,
                sandbox_id=lease.sandbox_id,
                workspace_id=lease.workspace_id,
                delay=self._idle_stop_seconds,
            ),
            name="fleet-daytona-idle-stop",
        )
        self._idle_tasks[idle_key] = idle_task
        idle_task.add_done_callback(lambda completed, key=idle_key: self._forget_idle_task(key, completed))

    @staticmethod
    def _idle_key(session_id: UUID, workspace_id: UUID | None) -> tuple[UUID, UUID]:
        return (workspace_id or UUID(int=0), session_id)

    def _find_idle_task(
        self,
        session_id: UUID,
        workspace_id: UUID | None,
    ) -> tuple[tuple[UUID, UUID], asyncio.Task[None]] | None:
        if workspace_id is not None:
            key = self._idle_key(session_id, workspace_id)
            task = self._idle_tasks.get(key)
            return (key, task) if task is not None else None
        matches = [(key, task) for key, task in self._idle_tasks.items() if key[1] == session_id]
        return matches[0] if len(matches) == 1 else None

    async def _cancel_idle_stop(
        self,
        session_id: UUID,
        *,
        workspace_id: UUID | None = None,
        deadline: float | None = None,
    ) -> None:
        found = self._find_idle_task(session_id, workspace_id)
        if found is None:
            return
        key, task = found
        task.cancel()
        try:
            if deadline is None:
                await asyncio.shield(task)
            else:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError
                await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
        except TimeoutError:
            raise DaytonaLeaseAcquisitionTimeoutError("Daytona idle-stop cleanup timed out") from None
        except asyncio.CancelledError:
            if task.cancelled():
                self._forget_idle_task(key, task)
                return
            raise

    def _request_cancel_idle_stop(self, session_id: UUID, *, workspace_id: UUID | None = None) -> None:
        found = self._find_idle_task(session_id, workspace_id)
        if found is not None:
            found[1].cancel()

    def _forget_idle_task(self, key: tuple[UUID, UUID], task: asyncio.Task[None]) -> None:
        if self._idle_tasks.get(key) is task:
            self._idle_tasks.pop(key, None)

    async def _stop_after_idle(
        self,
        *,
        session_id: UUID,
        sandbox_id: str,
        workspace_id: str | None,
        delay: float,
    ) -> None:
        await asyncio.sleep(delay)
        workspace_scope = UUID(workspace_id) if workspace_id is not None else None
        if self._idle_stop_blocked(session_id, workspace_scope):
            return
        if workspace_id is not None:
            assert workspace_scope is not None
            binding = await self._get_binding_for_workspace(session_id, workspace_scope)
        else:
            binding = await _provider_call(
                self._bindings.get(session_id),
                deadline=None,
                operation="Idle Sandbox binding lookup",
                owner=self._provider_tasks,
            )
        if binding is None or binding.sandbox_id != sandbox_id or binding.provider_state != "running":
            return
        sandbox = await self._get_bound_sandbox(sandbox_id)
        if sandbox is None or self._idle_stop_blocked(session_id, workspace_scope):
            return

        stop_task = asyncio.create_task(self._platform.stop(sandbox_id))
        _retain_provider_task(stop_task, self._provider_tasks)
        try:
            await asyncio.shield(stop_task)
        except asyncio.CancelledError:
            await asyncio.shield(stop_task)
            raise
        if self._idle_stop_blocked(session_id, workspace_scope):
            return
        if workspace_id is not None:
            assert workspace_scope is not None
            latest = await self._get_binding_for_workspace(session_id, workspace_scope)
        else:
            latest = await _provider_call(
                self._bindings.get(session_id),
                deadline=None,
                operation="Idle Sandbox binding lookup",
                owner=self._provider_tasks,
            )
        if latest is None or latest.sandbox_id != sandbox_id or latest.provider_state != "running":
            return
        self.revoke_binding(
            session_id=session_id,
            workspace_id=workspace_scope or latest.workspace_id,
            sandbox_id=sandbox_id,
            generation=latest.generation,
        )
        update = asyncio.ensure_future(
            self._bindings.upsert(
                replace(
                    latest,
                    provider_state="stopped",
                    last_verified_at=datetime.now(UTC),
                    generation=latest.generation + 1,
                )
            )
        )
        _retain_provider_task(update, self._provider_tasks)
        try:
            persisted = await asyncio.shield(update)
            self._observe_binding(persisted)
        except asyncio.CancelledError:
            await OwnedEffect.from_task(update).settle()
            raise

    async def _drain_provider_ownership(self, *, drain_seconds: float = 30.0) -> bool:
        if drain_seconds < 0:
            raise ValueError("drain_seconds must be non-negative")
        deadline = asyncio.get_running_loop().time() + drain_seconds
        idle = tuple(self._idle_tasks.values())
        for task in idle:
            task.cancel()
        release = tuple(self._release_tasks)
        provider = tuple(self._provider_tasks)
        all_tasks = tuple(dict.fromkeys((*idle, *release, *provider, *self._late_cleanup_tasks)))
        pending: set[asyncio.Future[Any]] = set()
        for task in all_tasks:
            if isinstance(task, asyncio.Future):
                pending.add(task)
            else:
                pending.add(asyncio.wrap_future(task))
        if pending:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            _, pending = await asyncio.wait(pending, timeout=remaining)
        if pending:
            return False

        unpublished_leases = {
            id(owner.lease) for owner in self._late_owners.values() if owner.unpublished and owner.lease is not None
        }
        retry_release = [
            self._start_release_task(lease)
            for lease in tuple(self._release_leases.values())
            if not lease._released and id(lease) not in unpublished_leases
        ]
        if retry_release:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            _, retry_pending = await asyncio.wait(tuple(retry_release), timeout=remaining)
            if retry_pending or any(not lease._released for lease in self._release_leases.values()):
                return False

        return await self._retry_late_owners(deadline)

    def _retain_late_created_sandbox(self, task: asyncio.Future[Any]) -> None:
        async def retire_late() -> None:
            try:
                sandbox = await asyncio.shield(task)
            except BaseException:
                return
            if sandbox is None:
                return
            with contextlib.suppress(BaseException):
                await self._sandbox_retirement_lease(_sandbox_id(sandbox)).aclose()

        coroutine = retire_late()
        try:
            cleanup = asyncio.create_task(coroutine, name="fleet-daytona-late-sandbox-creation-cleanup")
        except BaseException:
            coroutine.close()
            return
        _retain_provider_task(cleanup, self._provider_tasks)

    async def _resolve_volume_id(self, *, deadline: float | None = None) -> str:
        for attempt in range(2):
            try:
                return await _provider_call(
                    get_or_create_volume_id(self._volume_client, self._volume_config),
                    deadline=deadline,
                    operation="Volume resolution",
                    owner=self._provider_tasks,
                )
            except _ProviderCallDeadlineError:
                raise
            except Exception as exc:
                mapped = map_provider_error(exc)
                if attempt == 0 and is_safe_pre_creation_retry(mapped):
                    continue
                if mapped is exc:
                    raise
                raise mapped from exc
        raise AssertionError("unreachable")

    async def replace(
        self,
        binding: SandboxBinding,
        *,
        workspace_id: UUID | None = None,
        user_id: UUID | None = None,
        deadline: float | None = None,
    ) -> SandboxBinding:
        resolved_workspace = workspace_id or binding.workspace_id
        require_non_zero_workspace_id(resolved_workspace)
        if workspace_id is not None and binding.workspace_id != workspace_id:
            raise DaytonaAdapterError(
                message="sandbox binding does not match workspace scope",
                cause_type="WorkspaceMountMismatch",
            )
        if user_id is None or user_id == UUID(int=0):
            raise DaytonaAdapterError(
                message="replace requires a real user_id (zero UUID is forbidden)",
                cause_type="SandboxReplaceIdentityError",
            )
        volume_id = binding.volume_id or await self._resolve_volume_id(deadline=deadline)
        expected = self._expected_execution_mount(
            volume_id=volume_id,
            workspace_id=resolved_workspace,
            session_id=binding.session_id,
        )
        if binding.sandbox_id:
            await self.discard_stale_root_session(resolved_workspace, binding.session_id, deadline=deadline)
            fenced = await _provider_call(
                self._bindings.upsert(replace(binding, provider_state="fencing", last_verified_at=None)),
                deadline=deadline,
                operation="Sandbox replacement fence",
                owner=self._provider_tasks,
            )
            self._observe_binding(fenced)
            retirement = self._sandbox_retirement_lease(binding.sandbox_id)
            retirement_attempted = True
            try:
                receipt = await retirement.aclose(deadline=deadline)
            except TimeoutError as exc:
                raise DaytonaAdapterError(
                    message="sandbox retirement timed out",
                    cause_type="SandboxRetirementTimeout",
                ) from exc
            if not receipt.clean:
                if deadline is None:
                    await retirement.wait_ownership()
                else:
                    try:
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining > 0:
                            await retirement.wait_ownership(timeout=remaining)
                    except TimeoutError:
                        pass
                raise DaytonaAdapterError(
                    message="sandbox retirement was not confirmed",
                    cause_type="SandboxRetirementUnconfirmed",
                )
        else:
            retirement_attempted = False
        request = LeaseRequest(
            session_id=binding.session_id,
            user_id=user_id,
            workspace_id=resolved_workspace,
        )
        sandbox: Any | None = None
        try:
            sandbox = await self._create_sandbox(
                volume_id=expected.volume_id,
                mount_path=expected.mount_path,
                volume_subpath=expected.volume_subpath,
                request=request,
                deadline=deadline,
                settle_on_deadline=False,
            )
            self._provisioner.verify(sandbox, expected)
            new_binding = SandboxBinding(
                session_id=binding.session_id,
                sandbox_id=_sandbox_id(sandbox),
                workspace_id=resolved_workspace,
                volume_id=expected.volume_id,
                volume_subpath=expected.volume_subpath,
                mount_path=expected.mount_path,
                provider_state="running",
                last_verified_at=datetime.now(UTC),
                generation=binding.generation + 1,
            )
            atomic_replace = getattr(self._bindings, "replace_with_next_generation", None)
            persist = atomic_replace(new_binding) if callable(atomic_replace) else self._bindings.upsert(new_binding)
            persisted = await persist
            self._observe_binding(persisted)
            return persisted
        except BaseException:
            if sandbox is not None:
                with contextlib.suppress(BaseException):
                    await self._sandbox_retirement_lease(_sandbox_id(sandbox)).aclose()
            if retirement_attempted:
                with contextlib.suppress(BaseException):
                    await self._bindings.upsert(replace(binding, provider_state="quarantined", last_verified_at=None))
            raise

    async def _ensure_running(
        self,
        sandbox: Any,
        state: str,
        *,
        volume_id: str,
        mount_path: str,
        deadline: float | None = None,
    ) -> Any:
        del volume_id, mount_path
        if state == "running":
            return sandbox
        if state in {"stopped", "paused", "archived"}:
            try:
                await _provider_call(
                    self._platform.start(_sandbox_id(sandbox)),
                    deadline=deadline,
                    operation="Sandbox start",
                    owner=self._provider_tasks,
                )
                refreshed = await _provider_call(
                    self._platform.get(_sandbox_id(sandbox)),
                    deadline=deadline,
                    operation="Sandbox lookup",
                    owner=self._provider_tasks,
                )
                return refreshed or sandbox
            except _ProviderCallDeadlineError:
                raise
            except Exception as exc:
                raise map_provider_error(exc) from exc
        raise DaytonaAdapterError(
            message=f"sandbox unusable in state {state}",
            cause_type="SandboxUnrecoverable",
        )

    async def _create_sandbox(
        self,
        *,
        volume_id: str,
        mount_path: str,
        volume_subpath: str,
        request: LeaseRequest,
        deadline: float | None = None,
        settle_on_deadline: bool = True,
    ) -> Any:
        expected = ExpectedWorkspaceMount(
            volume_id=volume_id,
            volume_subpath=volume_subpath,
            mount_path=mount_path,
            workspace_id=request.workspace_id,
            session_id=request.session_id if mount_path == EXECUTION_MOUNT_PATH else None,
        )
        try:
            return await _provider_call(
                self._provisioner.create(
                    expected,
                    labels={
                        "session_id": str(request.session_id),
                        "user_id": str(request.user_id),
                        "workspace_id": str(request.workspace_id),
                        "fleet_package": "fleet_rlm",
                        "volume_subpath": expected.volume_subpath,
                    },
                    ephemeral=False,
                ),
                deadline=deadline,
                operation="Sandbox creation",
                owner=self._provider_tasks,
            )
        except _ProviderCallDeadlineError as exc:
            if settle_on_deadline:
                return await _settle_provider_task(exc.task)
            self._retain_late_created_sandbox(exc.task)
            raise DaytonaLeaseAcquisitionTimeoutError(f"Daytona {exc.operation} timed out") from None
