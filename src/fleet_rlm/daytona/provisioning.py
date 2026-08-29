"""Strict async Sandbox, Volume, mount, and layout provisioning."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Protocol
from uuid import UUID

from fleet_rlm.daytona.errors import DaytonaAdapterError, map_provider_error
from fleet_rlm.paths import DEFAULT_VOLUME_MOUNT_PATH, VolumePaths, validate_mount_path

# Workspace-scoped subpath validation lives in the provider-neutral owner
# (fleet_rlm.runtime.bindings); Daytona provisioning reuses it directly.
from fleet_rlm.runtime.bindings import (
    require_non_zero_workspace_id,
    require_scoped_volume_subpath,
    workspace_volume_subpath,
)
from fleet_rlm.snapshot_contract import validate_snapshot_name

DEFAULT_SNAPSHOT_NAME = "fleet-rlm-python313-v5"
DEFAULT_VOLUME_NAME = "rlm-volume-dspy"
PYTHON_VERSION = "3.13.13"
BASE_IMAGE = "python:3.13.13-slim-bookworm@sha256:f576b530293e74140ea91d262232648d5c4f45640a95ec447757701bfcacf034"
_DIRECTORY_MODE = "700"
_ZERO_UUID = UUID(int=0)
_SNAPSHOT_REQUIREMENTS = "snapshot-requirements.txt"


class VolumeClient(Protocol):
    async def get(self, name: str, *, create: bool = False) -> Any: ...


class SandboxPlatform(Protocol):
    async def get(self, sandbox_id: str) -> Any | None: ...

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
        Create a sandbox with optional volume, network, labeling, and lifecycle configuration.

        Parameters:
            volume_id (str | None): Identifier of the volume to mount.
            mount_path (str | None): Path at which to mount the volume.
            volume_subpath (str | None): Subpath within the volume to mount.
            labels (dict[str, str] | None): Labels to assign to the sandbox.
            with_volume (bool): Whether to configure a volume for the sandbox.
            ephemeral (bool): Whether to create the sandbox as ephemeral.
            network_block_all (bool): Whether to block all network access.
            network_allow_list (str | None): Network allow-list configuration.
            domain_allow_list (str | None): Domain allow-list configuration.
            auto_stop_interval (int | None): Interval after which the sandbox is stopped automatically.
            auto_delete_interval (int | None): Interval after which the sandbox is deleted automatically.

        Returns:
            Any: The created sandbox.
        """
        ...

    async def delete(self, sandbox_id: Any) -> None:
        """
        Delete the specified sandbox.

        Parameters:
            sandbox_id (Any): Identifier of the sandbox to delete.
        """
        ...

    async def start(self, sandbox_id: str) -> None: ...

    async def stop(self, sandbox_id: str, *, timeout: float = 60, force: bool = False) -> None: ...


@dataclass(frozen=True, slots=True)
class DaytonaSandboxSpec:
    """Immutable image and resource contract for Fleet Daytona Sandboxes."""

    snapshot: str
    python_version: str = PYTHON_VERSION
    base_image: str = BASE_IMAGE
    cpu: int = 2
    memory_gib: int = 4
    disk_gib: int = 8

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot", validate_snapshot_name(self.snapshot))
        if (self.cpu, self.memory_gib, self.disk_gib) != (2, 4, 8):
            raise ValueError("Fleet Daytona snapshot resources must be 2 CPU, 4 GiB memory, and 8 GiB disk")

    @classmethod
    def from_settings(cls, settings: Any) -> DaytonaSandboxSpec:
        value = getattr(settings, "daytona_snapshot", None)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("FLEET_DAYTONA_SNAPSHOT is required")
        return cls(snapshot=value.strip())


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "mount_path", str(self.mount_path))


def sandbox_spec_from_settings(settings: Any) -> DaytonaSandboxSpec:
    return DaytonaSandboxSpec.from_settings(settings)


def volume_config_from_settings(settings: Any) -> VolumeConfig:
    return VolumeConfig.from_settings(settings)


def snapshot_execution_dependencies() -> tuple[str, ...]:
    """Load the exact generated-code packages baked into the Snapshot."""
    content = files("fleet_rlm.daytona").joinpath(_SNAPSHOT_REQUIREMENTS).read_text(encoding="utf-8")
    dependencies = tuple(
        line.strip() for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )
    if not dependencies or any("==" not in dependency or dependency.count("==") != 1 for dependency in dependencies):
        raise RuntimeError("Snapshot dependencies must use exact non-empty == pins")
    return dependencies


_IMPORT_NAME_OVERRIDES: dict[str, str] = {"beautifulsoup4": "bs4"}


def snapshot_dependency_import_names() -> tuple[tuple[str, str, str], ...]:
    """Return dependency distributions, import module names, and pinned versions included in the snapshot.

    Returns:
        tuple[tuple[str, str, str], ...]: Triples containing each distribution name,
        its import module name, and its pinned version.
    """
    triples = []
    for dependency in snapshot_execution_dependencies():
        package, version = dependency.split("==", 1)
        triples.append((package, _IMPORT_NAME_OVERRIDES.get(package, package.replace("-", "_")), version))
    return tuple(triples)


def snapshot_dependency_sha256() -> str:
    """Return the stable digest of the canonical Snapshot dependency contract."""
    canonical = "".join(f"{dependency}\n" for dependency in snapshot_execution_dependencies())
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_snapshot_image(spec: DaytonaSandboxSpec) -> Any:
    """
    Build the Daytona image used for the configured snapshot execution environment.

    Parameters:
        spec (DaytonaSandboxSpec): Snapshot specification containing the base image.

    Returns:
        Any: Configured Daytona image with required system and Python dependencies,
        environment variables, working directory, and user.
    """
    from daytona import Image

    return (
        Image.base(spec.base_image)
        .run_commands(
            "apt-get update && apt-get install -y --no-install-recommends "
            "git ca-certificates && rm -rf /var/lib/apt/lists/*",
            "groupadd --gid 1000 daytona",
            "useradd --uid 1000 --gid daytona --create-home --home-dir /home/daytona --shell /bin/bash daytona",
            "chown -R daytona:daytona /home/daytona",
        )
        .pip_install(list(snapshot_execution_dependencies()))
        .env(
            {
                "PYTHONUNBUFFERED": "1",
                "FLEET_SNAPSHOT_DEPENDENCIES_SHA256": snapshot_dependency_sha256(),
            }
        )
        .workdir("/home/daytona")
        .dockerfile_commands(["USER daytona"])
    )


def verify_sandbox_spec(sandbox: Any, spec: DaytonaSandboxSpec) -> None:
    actual = getattr(sandbox, "snapshot", None)
    if str(actual or "").strip() != spec.snapshot:
        raise DaytonaAdapterError(
            message="sandbox snapshot does not match configured Fleet snapshot",
            cause_type="SandboxSnapshotMismatch",
        )


def recursive_child_volume_subpath(workspace_id: UUID, run_id: UUID, call_index: int) -> str:
    """
    Builds the canonical recursive volume scope for a disposable child RLM.

    Parameters:
        workspace_id (UUID): The workspace identifier.
        run_id (UUID): The child run identifier.
        call_index (int): The positive child call index.

    Returns:
        str: The recursive volume scope path.

    Raises:
        TypeError: If `workspace_id` or `run_id` is not a UUID.
        ValueError: If an identifier is the zero UUID or `call_index` is not positive.
    """
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
    """
    Validate and return a canonical recursive child volume subpath.

    Parameters:
        subpath (str): Candidate path in the form
            ``recursive/<workspace UUID>/<run UUID>/<positive call index>``.
        workspace_id (UUID | None): Optional workspace identifier to require.
        run_id (UUID | None): Optional run identifier to require.
        call_index (int | None): Optional call index to require.

    Returns:
        str: The normalized recursive child volume subpath.

    Raises:
        ValueError: If the path is invalid, non-canonical, or does not match a
            provided identifier.
    """
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
    """
    Validate a persistent workspace or recursive child volume mount subpath.

    Parameters:
        subpath (str): The volume mount subpath to validate.

    Returns:
        str: The validated volume mount subpath.
    """
    if not isinstance(subpath, str) or not subpath.strip():
        return require_scoped_volume_subpath(subpath)
    try:
        return require_scoped_volume_subpath(subpath)
    except ValueError:
        return require_recursive_child_volume_subpath(subpath)


def volume_mount_spec(config: VolumeConfig, volume_id: str, *, workspace_id: UUID) -> dict[str, str]:
    """Build a validated workspace-scoped volume mount specification.

    Parameters:
        config (VolumeConfig): Volume configuration containing the mount path.
        volume_id (str): Identifier of the volume to mount.
        workspace_id (UUID): Workspace whose persistent subpath is mounted.

    Returns:
        dict[str, str]: Volume ID, validated mount path, and workspace subpath.
    """
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
    info = await _file_info(fs, path)
    if info is not None:
        _assert_directory(info)
        return
    if not create:
        raise DaytonaAdapterError(
            message="Workspace Volume mount is unavailable",
            cause_type="VolumeLayoutMissingMount",
        )
    try:
        await fs.create_folder(path, _DIRECTORY_MODE)
    except Exception as exc:
        info = await _file_info(fs, path)
        if info is None:
            raise map_provider_error(exc) from exc
        _assert_directory(info)
        return
    info = await _file_info(fs, path)
    if info is None:
        raise DaytonaAdapterError(
            message="Workspace Volume directory was not created",
            cause_type="VolumeLayoutCreateFailed",
        )
    _assert_directory(info)


async def _ensure_directories(fs: Any, directories: Iterable[str]) -> None:
    for directory in directories:
        await _require_directory(fs, directory, create=True)


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
            return
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
        """Create one sandbox under the strict provisioning policy."""
        try:
            return await self._platform.create(
                volume_id=expected.volume_id,
                mount_path=str(expected.mount_path),
                volume_subpath=require_scoped_volume_subpath(
                    expected.volume_subpath,
                    workspace_id=expected.workspace_id,
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
        await ensure_volume_layout(
            sandbox,
            self._volume_config.paths(),
            session_id=session_id,
            run_id=run_id,
        )


__all__ = [
    "DaytonaSandboxSpec",
    "ExpectedWorkspaceMount",
    "SandboxProvisioner",
    "VolumeConfig",
]
