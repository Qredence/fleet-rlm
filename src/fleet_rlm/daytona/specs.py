"""Daytona specifications, volume models, and environment profiles.

Defines immutable contracts for sandbox configurations, volume mount topologies,
and lifecycle states across Fleet RLM's Daytona execution environments.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID

from fleet_rlm.daytona.errors import DaytonaAdapterError, map_provider_error
from fleet_rlm.daytona.interpreter import (
    DEFAULT_EXECUTION_TIMEOUT_S,
)
from fleet_rlm.paths import (
    DEFAULT_VOLUME_MOUNT_PATH,
    SESSION_WORKSPACE_MOUNT_PATH,
    VolumePaths,
    validate_mount_path,
)
from fleet_rlm.sessions.bindings import (
    require_non_zero_workspace_id,
    require_scoped_volume_subpath,
    require_session_workspace_subpath,
    workspace_volume_subpath,
)
from fleet_rlm.snapshot_contract import validate_snapshot_name

PREWARM_RUN_ID = UUID("00000000-0000-4000-8000-000000000000")
DEFAULT_IDLE_STOP_SECONDS = 300.0
DEFAULT_SNAPSHOT_NAME = "fleet-rlm-python313-v7"
DEFAULT_CHILD_SNAPSHOT_NAME = "fleet-rlm-python313-child-v2"
DEFAULT_VOLUME_NAME = "fleet-volume"
PYTHON_VERSION = "3.13.13"
BASE_IMAGE = "python:3.13.13-slim-bookworm@sha256:f576b530293e74140ea91d262232648d5c4f45640a95ec447757701bfcacf034"
SESSION_RESOURCES: tuple[int, int, int] = (4, 8, 8)
SEMANTIC_CHILD_RESOURCES: tuple[int, int, int] = (2, 4, 4)
DIRECTORY_MODE = "700"
ZERO_UUID = UUID(int=0)

ProviderState = Literal[
    "missing",
    "running",
    "stopped",
    "paused",
    "archived",
    "unrecoverable",
]
RUNNING_STATES = frozenset({"running", "started", "active"})
STOPPED_STATES = frozenset({"stopped", "stop"})
PAUSED_STATES = frozenset({"paused", "pause"})
ARCHIVED_STATES = frozenset({"archived", "archive"})


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
    if run_id == ZERO_UUID:
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
    from daytona.common.errors import DaytonaConflictError

    try:
        volume = await client.get(config.name, create=True)
    except DaytonaConflictError:
        volume = await client.get(config.name, create=False)
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


def sandbox_filesystem(sandbox: Any) -> Any:
    fs = getattr(sandbox, "fs", None)
    if fs is None:
        raise DaytonaAdapterError(
            message="Daytona Sandbox filesystem is unavailable",
            cause_type="VolumeLayoutUnavailable",
        )
    return fs


def is_not_found(exc: BaseException) -> bool:
    if isinstance(exc, FileNotFoundError) or getattr(exc, "status_code", None) == 404:
        return True
    response = getattr(exc, "response", None)
    return response is not None and getattr(response, "status_code", None) == 404


def assert_directory(info: Any) -> None:
    is_directory = info.get("is_dir", False) if isinstance(info, Mapping) else getattr(info, "is_dir", False)
    if not bool(is_directory):
        raise DaytonaAdapterError(
            message="Workspace Volume layout conflicts with an existing file",
            cause_type="VolumeLayoutConflict",
        )


async def file_info(fs: Any, path: str) -> Any | None:
    try:
        return await fs.get_file_info(path)
    except Exception as exc:
        if is_not_found(exc):
            return None
        raise map_provider_error(exc) from exc


async def require_directory(fs: Any, path: str, *, create: bool) -> None:
    if not create:
        info = await file_info(fs, path)
        if info is None:
            raise DaytonaAdapterError(
                message="Workspace Volume mount is unavailable",
                cause_type="VolumeLayoutMissingMount",
            )
        assert_directory(info)
        return

    try:
        await fs.create_folder(path, DIRECTORY_MODE)
    except Exception as exc:
        info = await file_info(fs, path)
        if info is None:
            raise map_provider_error(exc) from exc
        assert_directory(info)
        return


async def ensure_directories(fs: Any, directories: Iterable[str]) -> None:
    batches: dict[int, list[str]] = {}
    for directory in directories:
        batches.setdefault(str(directory).strip("/").count("/"), []).append(directory)
    for depth in sorted(batches):
        await asyncio.gather(*(require_directory(fs, d, create=True) for d in batches[depth]))


async def ensure_shared_volume_layout(sandbox: Any, paths: VolumePaths) -> None:
    fs = sandbox_filesystem(sandbox)
    await require_directory(fs, str(paths.mount_path), create=False)
    await ensure_directories(fs, shared_volume_directories(paths))


async def ensure_volume_layout(
    sandbox: Any,
    paths: VolumePaths,
    *,
    session_id: UUID,
    run_id: UUID,
) -> None:
    fs = sandbox_filesystem(sandbox)
    await require_directory(fs, str(paths.mount_path), create=False)
    await ensure_directories(fs, required_volume_directories(paths, session_id=session_id, run_id=run_id))


async def ensure_execution_layout(sandbox: Any, *, run_id: UUID) -> None:
    """Create only the shared Session workspace mount and Run-local scratch."""
    fs = sandbox_filesystem(sandbox)
    await require_directory(fs, SESSION_WORKSPACE_MOUNT_PATH, create=False)
    await ensure_directories(fs, ("/tmp/fleet", f"/tmp/fleet/{run_id}"))


async def verify_execution_mount(sandbox: Any) -> None:
    """Check the mount from the Python process used for RLM execution."""
    process = getattr(sandbox, "process", None)
    if process is None or not callable(getattr(process, "exec", None)):
        raise DaytonaAdapterError(
            message="Sandbox process cannot verify the Workspace mount",
            cause_type="InterpreterConfigurationError",
        )
    check = await process.exec(
        f"python -c 'import os; os.chdir(\"{SESSION_WORKSPACE_MOUNT_PATH}\")'",
        timeout=10,
    )
    if getattr(check, "exit_code", None) != 0:
        raise DaytonaAdapterError(
            message="Workspace Volume mount is unavailable to Python execution",
            cause_type="ExecutionMountNotVisible",
        )


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


def normalize_state(raw: Any) -> ProviderState:
    """Normalize provider-specific states at the provider adapter boundary."""
    if raw is None:
        return "missing"
    text = str(getattr(raw, "value", raw)).strip().lower()
    if text in RUNNING_STATES:
        return "running"
    if text in STOPPED_STATES:
        return "stopped"
    if text in PAUSED_STATES:
        return "paused"
    if text in ARCHIVED_STATES:
        return "archived"
    if text in {"missing", "deleted", ""}:
        return "missing"
    return "unrecoverable"


def sandbox_state(sandbox: Any) -> ProviderState:
    raw = getattr(sandbox, "state", None)
    if raw is None:
        raw = getattr(sandbox, "status", None)
    return normalize_state(raw)


VOLUME_READY_RETRY_DELAYS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
VOLUME_FAILED_STATES = frozenset({"deleting", "deleted", "error"})


def expected_workspace_mount(
    volume_config: VolumeConfig,
    volume_id: str,
    workspace_id: UUID,
) -> ExpectedWorkspaceMount:
    mount = volume_mount_spec(volume_config, volume_id, workspace_id=workspace_id)
    return ExpectedWorkspaceMount(
        volume_id=mount["volume_id"],
        volume_subpath=mount["subpath"],
        mount_path=mount["mount_path"],
        workspace_id=workspace_id,
    )


_expected_workspace_mount = expected_workspace_mount


async def create_daytona_sandbox(
    platform: SandboxPlatform,
    expected: ExpectedWorkspaceMount,
    *,
    labels: dict[str, str],
    ephemeral: bool,
) -> Any:
    """Create a sandbox from its already validated Volume binding."""
    try:
        return await platform.create(
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


_create_daytona_sandbox = create_daytona_sandbox
