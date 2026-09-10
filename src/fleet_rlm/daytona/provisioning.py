"""Strict async Sandbox, Volume, mount, and layout provisioning."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
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

DEFAULT_SNAPSHOT_NAME = "fleet-rlm-python313-v7"
DEFAULT_CHILD_SNAPSHOT_NAME = "fleet-rlm-python313-child-v2"
DEFAULT_VOLUME_NAME = "rlm-volume-dspy"
PYTHON_VERSION = "3.13.13"
BASE_IMAGE = "python:3.13.13-slim-bookworm@sha256:f576b530293e74140ea91d262232648d5c4f45640a95ec447757701bfcacf034"
SESSION_RESOURCES: tuple[int, int, int] = (4, 8, 8)
SEMANTIC_CHILD_RESOURCES: tuple[int, int, int] = (2, 4, 4)
_DIRECTORY_MODE = "700"
_ZERO_UUID = UUID(int=0)
_SNAPSHOT_REQUIREMENTS = "snapshot-requirements.txt"


class DaytonaEnvironmentProfile(StrEnum):
    """The three logical execution environments; capacity is not implied."""

    SESSION = "session"
    SEMANTIC_CHILD = "semantic-child"
    WORKSPACE_CHILD = "workspace-child"


class MissingImportOutcome(StrEnum):
    """Bounded outcomes for optional image import observations."""

    MISSING = "missing"
    IMPORT_ERROR = "import-error"
    VERSION_MISMATCH = "version-mismatch"


@dataclass(frozen=True, slots=True)
class MissingImportObservation:
    """Content-free evidence that one profile import check failed."""

    module: str
    profile: DaytonaEnvironmentProfile
    outcome: MissingImportOutcome

    def as_dict(self) -> dict[str, str]:
        return {"module": self.module, "profile": self.profile.value, "outcome": self.outcome.value}


_IMPORT_NAME = re.compile(r"^[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*$", re.IGNORECASE)


def normalize_missing_import_observation(
    module: str,
    profile: DaytonaEnvironmentProfile | str,
    outcome: MissingImportOutcome | str = MissingImportOutcome.MISSING,
) -> MissingImportObservation:
    """Normalize a bounded module/profile/outcome observation.

    Invalid or overlong provider-derived names are rejected rather than
    retained, keeping receipts deterministic and free of exception content.
    """
    normalized_module = module.strip().lower()
    if len(normalized_module) > 128 or not _IMPORT_NAME.fullmatch(normalized_module):
        raise ValueError("missing-import module must be a normalized import name")
    try:
        normalized_profile = (
            profile if isinstance(profile, DaytonaEnvironmentProfile) else DaytonaEnvironmentProfile(profile)
        )
        normalized_outcome = outcome if isinstance(outcome, MissingImportOutcome) else MissingImportOutcome(outcome)
    except ValueError as exc:
        raise ValueError("missing-import profile or outcome is invalid") from exc
    return MissingImportObservation(normalized_module, normalized_profile, normalized_outcome)


@dataclass(frozen=True, slots=True)
class DaytonaEnvironmentManifest:
    """Auditable immutable environment identity, safe to retain in evidence."""

    profile: DaytonaEnvironmentProfile
    image_kind: str
    snapshot: str
    base_image: str
    python_version: str
    dependency_sha256: str
    dependencies: tuple[str, ...]
    user: str = "daytona"
    workdir: str = "/home/daytona"
    volume_allowed: bool = False
    warm_pool_eligible: bool = False
    schema_version: str = "fleet.daytona-runtime-manifest/v1"
    helper_protocol: str = "daytona-native-context/v1"

    def as_dict(self) -> dict[str, object]:
        """Return the non-secret manifest payload baked into new images."""
        return {
            "schema_version": self.schema_version,
            "profile": self.profile.value,
            "image_kind": self.image_kind,
            "snapshot": self.snapshot,
            "base_image": self.base_image,
            "python_version": self.python_version,
            "python_executable": "/usr/local/bin/python",
            "dependency_sha256": self.dependency_sha256,
            "dependencies": list(self.dependencies),
            "helper_protocol": self.helper_protocol,
            "capabilities": ["python", "git", "ca-certificates"],
            "resources": {"cpu": self.resources[0], "memory_gib": self.resources[1], "disk_gib": self.resources[2]},
            "user": self.user,
            "workdir": self.workdir,
            "volume_allowed": self.volume_allowed,
            "warm_pool_eligible": self.warm_pool_eligible,
        }

    def image_identity(self) -> dict[str, object]:
        """Return the immutable image payload independent of its execution profile."""
        identity = self.as_dict()
        identity.pop("profile")
        return identity

    @property
    def compatible_profiles(self) -> tuple[DaytonaEnvironmentProfile, ...]:
        """Return profiles that may execute against this immutable image."""
        if self.profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD:
            return (DaytonaEnvironmentProfile.SEMANTIC_CHILD,)
        return (DaytonaEnvironmentProfile.SESSION, DaytonaEnvironmentProfile.WORKSPACE_CHILD)

    @property
    def resources(self) -> tuple[int, int, int]:
        """Return the immutable CPU/memory/disk contract for this profile."""
        return (
            SESSION_RESOURCES
            if self.profile is not DaytonaEnvironmentProfile.SEMANTIC_CHILD
            else SEMANTIC_CHILD_RESOURCES
        )

    @property
    def digest(self) -> str:
        """Return the deterministic digest of the profile-independent image identity."""
        encoded = json.dumps(self.image_identity(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def environment_manifest(
    spec: DaytonaSandboxSpec,
    profile: DaytonaEnvironmentProfile | None = None,
) -> DaytonaEnvironmentManifest:
    """Return the selected profile's reproducible, non-secret contract.

    Session and Workspace children share the full analysis image. Semantic
    children use a lean image and are the only profile eligible for a generic
    clean warm pool; actual pool creation remains an operator action.
    """
    profile = spec.profile if profile is None else profile
    if not isinstance(profile, DaytonaEnvironmentProfile):
        raise TypeError("profile must be a DaytonaEnvironmentProfile")
    semantic = profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD
    dependencies = snapshot_execution_dependencies(profile)
    digest_source = "".join(f"{item}\n" for item in dependencies).encode("utf-8")
    return DaytonaEnvironmentManifest(
        profile=profile,
        image_kind="lean-child" if semantic else "session-analysis",
        snapshot=spec.snapshot,
        base_image=spec.base_image,
        python_version=spec.python_version,
        dependency_sha256=hashlib.sha256(digest_source).hexdigest(),
        dependencies=dependencies,
        volume_allowed=not semantic,
        warm_pool_eligible=semantic,
    )


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
    ) -> Any:
        """
        Create a sandbox with optional volume, network, labeling, and lifecycle configuration.

        Parameters:
            profile (DaytonaEnvironmentProfile): Immutable image/profile contract to use.
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "mount_path", str(self.mount_path))


def sandbox_spec_from_settings(
    settings: Any,
    profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.SESSION,
) -> DaytonaSandboxSpec:
    return DaytonaSandboxSpec.from_settings(settings, profile)


def volume_config_from_settings(settings: Any) -> VolumeConfig:
    return VolumeConfig.from_settings(settings)


def snapshot_execution_dependencies(
    profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.SESSION,
) -> tuple[str, ...]:
    """Load the exact generated-code packages baked into the Snapshot."""
    content = files("fleet_rlm.daytona").joinpath(_SNAPSHOT_REQUIREMENTS).read_text(encoding="utf-8")
    dependencies = tuple(
        line.strip() for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )
    if not dependencies or any("==" not in dependency or dependency.count("==") != 1 for dependency in dependencies):
        raise RuntimeError("Snapshot dependencies must use exact non-empty == pins")
    if profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD:
        # Native child execution still constructs/executes DSPy RLM code in the
        # sandbox.  Keep the child image lean by omitting the optional analysis
        # libraries, but never omit the pinned runtime kernel itself.
        dependencies = tuple(
            dependency for dependency in dependencies if dependency.split("==", 1)[0].strip().lower() == "dspy"
        )
        if not dependencies:
            raise RuntimeError("SemanticChild snapshot dependencies must include pinned dspy")
    return dependencies


_IMPORT_NAME_OVERRIDES: dict[str, str] = {"beautifulsoup4": "bs4"}


def snapshot_dependency_import_names(
    profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.SESSION,
) -> tuple[tuple[str, str, str], ...]:
    """Return dependency distributions, import module names, and pinned versions included in the snapshot.

    Returns:
        tuple[tuple[str, str, str], ...]: Triples containing each distribution name,
        its import module name, and its pinned version.
    """
    triples = []
    for dependency in snapshot_execution_dependencies(profile):
        package, version = dependency.split("==", 1)
        triples.append((package, _IMPORT_NAME_OVERRIDES.get(package, package.replace("-", "_")), version))
    return tuple(triples)


def snapshot_dependency_sha256(
    profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.SESSION,
) -> str:
    """Return the stable digest of the canonical Snapshot dependency contract."""
    canonical = "".join(f"{dependency}\n" for dependency in snapshot_execution_dependencies(profile))
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

    # Session and WorkspaceChild share one immutable analysis image. Bake its
    # canonical Session manifest regardless of the execution profile selected
    # by the caller; profile-specific mounts and labels remain runtime policy.
    manifest_profile = (
        DaytonaEnvironmentProfile.SEMANTIC_CHILD
        if spec.profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD
        else DaytonaEnvironmentProfile.SESSION
    )
    manifest = environment_manifest(spec, manifest_profile)
    image_profile = manifest.profile
    image = (
        Image.base(spec.base_image)
        .run_commands(
            "apt-get update && apt-get install -y --no-install-recommends "
            "git ca-certificates && rm -rf /var/lib/apt/lists/*",
            "groupadd --gid 1000 daytona",
            "useradd --uid 1000 --gid daytona --create-home --home-dir /home/daytona --shell /bin/bash daytona",
            "chown -R daytona:daytona /home/daytona",
        )
        .pip_install(list(snapshot_execution_dependencies(image_profile)))
        .env(
            {
                "PYTHONUNBUFFERED": "1",
                "FLEET_SNAPSHOT_DEPENDENCIES_SHA256": snapshot_dependency_sha256(image_profile),
            }
        )
        .workdir("/home/daytona")
    )
    # v5 is retained as a rollback image whose existing provider definition
    # predates the runtime manifest. New immutable images carry the manifest
    # and its digest so runtime probes can validate the actual profile.
    if not (spec.snapshot == "fleet-rlm-python313-v5" and image_profile is DaytonaEnvironmentProfile.SESSION):
        encoded = json.dumps(manifest.as_dict(), sort_keys=True, separators=(",", ":"))
        image = image.run_commands(
            "mkdir -p /opt/fleet && "
            f"printf '%s' {shlex.quote(encoded)} > /opt/fleet/runtime-manifest.json && "
            "chmod 0444 /opt/fleet/runtime-manifest.json"
        ).env({"FLEET_SNAPSHOT_MANIFEST_SHA256": manifest.digest})
    # Keep the manifest build step root-owned, then drop to the non-root
    # runtime user for all generated code and interpreter execution.
    image = image.dockerfile_commands(["USER daytona"])
    return image


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
        # Tolerate a concurrent writer: a failed mkdir that the follow-up
        # stat resolves to an existing directory (ours or another writer's)
        # is success, not an error.
        info = await _file_info(fs, path)
        if info is None:
            raise map_provider_error(exc) from exc
        _assert_directory(info)
        return
    # create_folder returning normally is the creation confirmation; a
    # success-path re-stat costs one extra provider round-trip per directory
    # on the session cold-start path for no additional safety.


async def _ensure_directories(fs: Any, directories: Iterable[str]) -> None:
    """Ensure every directory exists, creating independent siblings concurrently.

    Directories are grouped into depth levels (path segment count); within a
    level, siblings share no parent/child relationship in this batch, so they
    are created with ``asyncio.gather``. Each directory keeps the idempotent
    verify-then-create contract of :func:`_require_directory`, whose
    post-failure re-stat tolerates concurrent creation by an unrelated
    writer.
    """
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
    "BASE_IMAGE",
    "DEFAULT_CHILD_SNAPSHOT_NAME",
    "DEFAULT_SNAPSHOT_NAME",
    "SEMANTIC_CHILD_RESOURCES",
    "SESSION_RESOURCES",
    "DaytonaEnvironmentManifest",
    "DaytonaEnvironmentProfile",
    "DaytonaSandboxSpec",
    "ExpectedWorkspaceMount",
    "MissingImportObservation",
    "MissingImportOutcome",
    "SandboxProvisioner",
    "VolumeConfig",
    "build_snapshot_image",
    "environment_manifest",
    "normalize_missing_import_observation",
    "snapshot_dependency_import_names",
    "snapshot_dependency_sha256",
    "snapshot_execution_dependencies",
]
