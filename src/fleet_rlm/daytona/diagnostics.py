"""Opt-in, disposable Daytona environment diagnostics.

The doctor never creates Fleet domain rows or Sandbox bindings. External work is
behind an injectable dependency seam so unit tests remain credential-free.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shlex
import time
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources import files
from typing import Any, Literal, Protocol
from uuid import uuid4

from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.errors import DaytonaAdapterError, classify_provider_error
from fleet_rlm.daytona.provider import LiveDaytonaPlatform, LiveDaytonaVolumeClient, build_daytona_client
from fleet_rlm.daytona.specs import (
    BASE_IMAGE,
    DEFAULT_CHILD_SNAPSHOT_NAME,
    DEFAULT_SNAPSHOT_NAME,
    SEMANTIC_CHILD_RESOURCES,
    SESSION_RESOURCES,
    DaytonaEnvironmentProfile,
    DaytonaSandboxSpec,
    ExpectedWorkspaceMount,
    sandbox_spec_from_settings,
    verify_sandbox_spec,
    verify_sandbox_workspace_mount,
    volume_config_from_settings,
)
from fleet_rlm.persistence.database import ensure_database_compatible
from fleet_rlm.sessions.bindings import workspace_volume_subpath

_SNAPSHOT_REQUIREMENTS = "snapshot-requirements.txt"
_BROWSER_INSTALL = "python -m playwright install --with-deps chromium"
_BROWSER_PATH = "/opt/fleet/playwright"
_IMPORT_NAME_OVERRIDES: dict[str, str] = {"beautifulsoup4": "bs4"}
_IMPORT_NAME = re.compile(r"^[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*$", re.IGNORECASE)


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
    browser_enabled: bool = False

    def as_dict(self) -> dict[str, object]:
        """Return the non-secret manifest payload baked into new images."""
        payload: dict[str, object] = {
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
            "capabilities": ["python", "git", "ca-certificates", "procps"],
            "resources": {"cpu": self.resources[0], "memory_gib": self.resources[1], "disk_gib": self.resources[2]},
            "user": self.user,
            "workdir": self.workdir,
            "volume_allowed": self.volume_allowed,
            "warm_pool_eligible": self.warm_pool_eligible,
        }
        if self.browser_enabled:
            payload["browser"] = {
                "capability_version": "public-page/v1",
                "install_command": _BROWSER_INSTALL,
                "browsers_path": _BROWSER_PATH,
                "chromium_sandbox": True,
            }
        return payload

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
        return ()
    return dependencies


def snapshot_dependency_import_names(
    profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.SESSION,
    *,
    spec: DaytonaSandboxSpec | None = None,
) -> tuple[tuple[str, str, str], ...]:
    triples = []
    dependencies = snapshot_execution_dependencies(profile) if spec is None else environment_manifest(spec).dependencies
    for dependency in dependencies:
        package, version = dependency.split("==", 1)
        triples.append((package, _IMPORT_NAME_OVERRIDES.get(package, package.replace("-", "_")), version))
    return tuple(triples)


def snapshot_dependency_sha256(
    profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.SESSION,
) -> str:
    canonical = "".join(f"{dependency}\n" for dependency in snapshot_execution_dependencies(profile))
    return hashlib.sha256(canonical.encode()).hexdigest()


def environment_manifest(
    spec: DaytonaSandboxSpec,
    profile: DaytonaEnvironmentProfile | None = None,
) -> DaytonaEnvironmentManifest:
    profile = spec.profile if profile is None else profile
    if not isinstance(profile, DaytonaEnvironmentProfile):
        raise TypeError("profile must be a DaytonaEnvironmentProfile")
    semantic = profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD
    dependencies = snapshot_execution_dependencies(profile)
    legacy = spec.snapshot == "fleet-rlm-python313-v5" and not semantic
    if legacy:
        dependencies = tuple(item for item in dependencies if not item.startswith("playwright=="))
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
        browser_enabled=not semantic and not legacy,
    )


def build_snapshot_image(spec: DaytonaSandboxSpec) -> Any:
    from daytona import Image

    manifest_profile = (
        DaytonaEnvironmentProfile.SEMANTIC_CHILD
        if spec.profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD
        else DaytonaEnvironmentProfile.SESSION
    )
    manifest = environment_manifest(spec, manifest_profile)
    image_profile = manifest.profile
    image = Image.base(spec.base_image).run_commands(
        "apt-get update && apt-get install -y --no-install-recommends "
        "git ca-certificates procps && rm -rf /var/lib/apt/lists/*",
        "groupadd --gid 1000 daytona",
        "useradd --uid 1000 --gid daytona --create-home --home-dir /home/daytona --shell /bin/bash daytona",
        "chown -R daytona:daytona /home/daytona",
    )
    dependencies = manifest.dependencies
    if dependencies:
        image = image.pip_install(list(dependencies))
    if manifest.browser_enabled:
        image = image.env({"PLAYWRIGHT_BROWSERS_PATH": _BROWSER_PATH}).run_commands(
            f"{_BROWSER_INSTALL} && chmod -R a+rX {_BROWSER_PATH} && rm -rf /var/lib/apt/lists/*"
        )
    image = image.env(
        {
            "PYTHONUNBUFFERED": "1",
            "FLEET_SNAPSHOT_DEPENDENCIES_SHA256": manifest.dependency_sha256,
        }
    ).workdir("/home/daytona")
    if not (spec.snapshot == "fleet-rlm-python313-v5" and image_profile is DaytonaEnvironmentProfile.SESSION):
        encoded = json.dumps(manifest.as_dict(), sort_keys=True, separators=(",", ":"))
        image = image.run_commands(
            "mkdir -p /opt/fleet && "
            f"printf '%s' {shlex.quote(encoded)} > /opt/fleet/runtime-manifest.json && "
            "chmod 0444 /opt/fleet/runtime-manifest.json"
        ).env({"FLEET_SNAPSHOT_MANIFEST_SHA256": manifest.digest})
    image = image.dockerfile_commands(["USER daytona"])
    return image


DoctorStepName = Literal[
    "settings",
    "database",
    "provider",
    "rlm",
    "snapshot",
    "manifest",
    "imports",
    "region",
    "mount",
    "sandbox",
    "interpreter",
    "capacity",
    "cleanup",
]
DoctorStepStatus = Literal["pass", "fail", "unsupported", "not-exercised"]
DoctorFailureCategory = Literal[
    "settings",
    "database",
    "auth",
    "quota",
    "network_timeout",
    "provider_5xx",
    "request_validation",
    "mount_mismatch",
    "snapshot_mismatch",
    "rlm_provider",
    "interpreter",
    "cleanup",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class DaytonaDoctorStep:
    """One safe, user-displayable diagnostic outcome."""

    name: DoctorStepName
    status: DoctorStepStatus
    message: str
    category: DoctorFailureCategory | None = None

    @property
    def ok(self) -> bool:
        """Project the existing public success flag from the authoritative status."""
        return self.status == "pass"


@dataclass(frozen=True, slots=True)
class DaytonaDoctorResult:
    """Complete doctor outcome in execution order."""

    ok: bool
    steps: tuple[DaytonaDoctorStep, ...]
    failure_category: DoctorFailureCategory | None = None
    readiness: tuple[DaytonaDoctorStep, ...] = ()


def profile_readiness_steps(
    *,
    snapshot_status: DoctorStepStatus = "not-exercised",
    imports_status: DoctorStepStatus = "not-exercised",
    mount_status: DoctorStepStatus = "not-exercised",
    region_status: DoctorStepStatus = "not-exercised",
    capacity_status: DoctorStepStatus = "not-exercised",
) -> tuple[DaytonaDoctorStep, ...]:
    """Project explicit internal probe statuses without contacting Daytona."""
    for status in (snapshot_status, imports_status, mount_status, region_status, capacity_status):
        if status not in {"pass", "fail", "unsupported", "not-exercised"}:
            raise ValueError("probe status is unsupported")

    def status_step(
        name: DoctorStepName,
        status: DoctorStepStatus,
        messages: dict[DoctorStepStatus, str],
    ) -> DaytonaDoctorStep:
        return DaytonaDoctorStep(name, status, messages[status])

    snapshot_messages = {
        "pass": "Immutable snapshot identity was checked.",
        "fail": "Configured Daytona snapshot identity did not match the provider runtime.",
        "unsupported": "Provider sandbox does not expose snapshot identity for validation.",
        "not-exercised": "Snapshot identity was not exercised.",
    }
    capacity_message = {
        "pass": "Optional Daytona capacity capability is available.",
        "not-exercised": "Capacity capability was not exercised.",
        "unsupported": "Daytona SDK does not expose optional capacity management.",
        "fail": "Optional Daytona capacity check failed.",
    }[capacity_status]
    return (
        DaytonaDoctorStep(
            "snapshot",
            snapshot_status,
            snapshot_messages[snapshot_status],
            "snapshot_mismatch" if snapshot_status == "fail" else None,
        ),
        DaytonaDoctorStep(
            "manifest",
            "pass",
            "Image, dependency, helper-protocol, and resource manifest is locally available.",
        ),
        status_step(
            "imports",
            imports_status,
            {
                "pass": "Configured snapshot imports were exercised.",
                "fail": "Configured snapshot import check failed.",
                "not-exercised": "Configured snapshot imports were not exercised.",
                "unsupported": "Snapshot import inspection is unsupported by this diagnostic seam.",
            },
        ),
        status_step(
            "region",
            region_status,
            {
                "pass": "Configured Daytona region was validated.",
                "not-exercised": "Daytona region validation was not exercised.",
                "unsupported": "Daytona region validation is unsupported by this provider seam.",
                "fail": "Configured Daytona region validation failed.",
            },
        ),
        status_step(
            "mount",
            mount_status,
            {
                "pass": "Scoped Workspace Volume mount was validated.",
                "fail": "Scoped Workspace Volume mount validation failed.",
                "not-exercised": "Scoped Workspace Volume mount was not exercised.",
                "unsupported": "Workspace Volume mount validation is unsupported by this diagnostic seam.",
            },
        ),
        DaytonaDoctorStep(
            "capacity",
            capacity_status,
            capacity_message,
        ),
    )


class DaytonaDoctorDependencies(Protocol):
    """External operations used by :func:`run_daytona_doctor`."""

    async def check_database(self, settings: Settings) -> None: ...

    async def resolve_volume(self, settings: Settings) -> str: ...

    async def check_rlm_readiness(self, settings: Settings, sandbox: Any) -> None: ...

    async def create_sandbox(
        self,
        *,
        expected_mount: ExpectedWorkspaceMount,
        labels: dict[str, str],
        ephemeral: bool,
    ) -> Any: ...

    async def verify_mount(self, sandbox: Any, expected_mount: ExpectedWorkspaceMount) -> None: ...

    async def execute(self, sandbox: Any) -> str: ...

    async def delete_sandbox(self, sandbox: Any) -> None: ...

    async def close(self) -> None: ...


class _ProductionDaytonaDoctorDependencies:
    """Production operations composed from Fleet's canonical adapters."""

    def __init__(self, settings: Settings) -> None:
        self._client = build_daytona_client(settings)
        self._sandbox_spec = sandbox_spec_from_settings(settings)
        self._platform = LiveDaytonaPlatform(
            self._client,
            self._sandbox_spec,
            {
                DaytonaEnvironmentProfile.SEMANTIC_CHILD: sandbox_spec_from_settings(
                    settings, DaytonaEnvironmentProfile.SEMANTIC_CHILD
                )
            },
        )
        self._volume_client = LiveDaytonaVolumeClient(self._client)
        self._volume_config = volume_config_from_settings(settings)

    async def check_database(self, settings: Settings) -> None:
        database_url = (settings.database_url or "").strip()
        await ensure_database_compatible(database_url)

    async def resolve_volume(self, settings: Settings) -> str:
        del settings
        volume = await self._volume_client.get(self._volume_config.name, create=False)
        volume_id = getattr(volume, "id", None)
        if volume_id is None:
            raise DaytonaAdapterError(
                message="configured Daytona Volume did not expose an id",
                cause_type="VolumeVisibilityError",
            )
        return str(volume_id)

    async def create_sandbox(
        self,
        *,
        expected_mount: ExpectedWorkspaceMount,
        labels: dict[str, str],
        ephemeral: bool,
    ) -> Any:
        return await self._platform.create(
            volume_id=expected_mount.volume_id,
            mount_path=expected_mount.mount_path,
            volume_subpath=expected_mount.volume_subpath,
            labels=labels,
            with_volume=True,
            ephemeral=ephemeral,
        )

    async def verify_mount(self, sandbox: Any, expected_mount: ExpectedWorkspaceMount) -> None:
        refresh = getattr(sandbox, "refresh_data", None)
        if callable(refresh):
            await refresh()
        mounts = getattr(sandbox, "volumes", None)
        if mounts is None:
            mounts = getattr(sandbox, "mounts", None)
        if not mounts:
            raise DaytonaAdapterError(
                message="sandbox did not expose scoped volume mount metadata",
                cause_type="WorkspaceMountMismatch",
            )
        verify_sandbox_workspace_mount(sandbox, expected_mount)
        verify_sandbox_spec(sandbox, self._sandbox_spec)

    async def execute(self, sandbox: Any) -> str:
        """
        Run the Daytona interpreter diagnostic in a temporary sandbox context.

        Parameters:
            sandbox (Any): Sandbox whose code interpreter runs the diagnostic.

        Returns:
            str: Diagnostic process output.

        Raises:
            RuntimeError: If the interpreter reports an execution error.
        """
        context = await sandbox.code_interpreter.create_context()
        run_error: BaseException | None = None
        try:
            dependencies = snapshot_dependency_import_names(spec=self._sandbox_spec)
            result = await sandbox.code_interpreter.run_code(
                "import importlib, importlib.metadata, os, shutil, sys\n"
                "assert sys.version_info[:3] == (3, 13, 13)\n"
                "assert os.geteuid() != 0\n"
                "assert os.getcwd() == '/home/daytona'\n"
                "assert shutil.which('git'), 'git toolchain missing from snapshot'\n"
                f"dependencies = {dependencies!r}\n"
                "for package, module, expected in dependencies:\n"
                "    importlib.import_module(module)\n"
                "    assert importlib.metadata.version(package) == expected\n"
                "print('fleet-doctor-ok')",
                context=context,
            )
            stdout = getattr(result, "stdout", result)
            error = getattr(result, "error", None)
            if error:
                raise RuntimeError("interpreter returned an error")
            return str(stdout or "")
        except BaseException as exc:
            run_error = exc
            raise
        finally:
            try:
                await sandbox.code_interpreter.delete_context(context)
            except Exception:
                if run_error is None:
                    raise

    async def delete_sandbox(self, sandbox: Any) -> None:
        if getattr(sandbox, "id", None) is None:
            raise DaytonaAdapterError(
                message="disposable sandbox did not expose an id",
                cause_type="SandboxIdentityError",
            )
        await self._platform.delete(sandbox)

    async def close(self) -> None:
        await self._client.close()

    async def check_rlm_readiness(self, settings: Settings, sandbox: Any) -> None:
        from fleet_rlm.daytona.children import ChildSandboxes
        from fleet_rlm.daytona.interpreter import sandbox_interpreter_factory
        from fleet_rlm.daytona.leases import DaytonaAdmission
        from fleet_rlm.rlm.execution import probe_configured_root_lm

        loop = asyncio.get_running_loop()
        tasks: set[asyncio.Future[Any]] = set()
        children = ChildSandboxes(
            platform=self._platform,
            admission=DaytonaAdmission(),
            dispatcher=None,
            provider_tasks=tasks,
        )
        factory = children.build_child_factory(
            volume_id=None,
            workspace_id=uuid4(),
            run_id=uuid4(),
            deadline=loop.time() + 120,
            execution_output_cap=2_000,
            profile=DaytonaEnvironmentProfile.SEMANTIC_CHILD,
        )
        try:
            await probe_configured_root_lm(
                settings,
                interpreter_factory=sandbox_interpreter_factory(sandbox, loop=loop, execution_output_cap=2_000),
                child_runtime_factory=factory,
            )
        finally:
            children.accepting = False
            await children._retry_child_cleanup_records(deadline=time.monotonic() + 120)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if children._child_cleanup_records:
                raise RuntimeError("diagnostic child cleanup remains unresolved")


_SUCCESS_MESSAGES: dict[DoctorStepName, str] = {
    "settings": "Fleet Daytona settings are valid.",
    "database": "Database connection and Alembic revision are compatible.",
    "provider": "Daytona authentication and Volume access succeeded.",
    "rlm": "Configured Root LM satisfies the pinned DSPy RLM action contract.",
    "sandbox": "Disposable scoped Sandbox mount is valid.",
    "interpreter": "Daytona interpreter executed the diagnostic.",
    "cleanup": "Disposable Daytona Sandbox was deleted.",
}

_FAILURE_MESSAGES: dict[DoctorFailureCategory, str] = {
    "settings": "Required Fleet Daytona settings are missing or invalid.",
    "database": "Database connection or Alembic revision validation failed.",
    "auth": "Daytona authentication was rejected.",
    "quota": "Daytona capacity or quota prevented the diagnostic.",
    "network_timeout": "Daytona could not be reached before the request timed out.",
    "provider_5xx": "Daytona returned a provider service error.",
    "request_validation": "Daytona rejected the diagnostic request.",
    "mount_mismatch": "The disposable Sandbox Volume mount did not match the requested scope.",
    "snapshot_mismatch": "The disposable Sandbox snapshot did not match the configured Fleet snapshot.",
    "rlm_provider": "The configured Root LM is not compatible with the pinned DSPy RLM action protocol.",
    "interpreter": "The Daytona interpreter diagnostic failed.",
    "cleanup": "Disposable Daytona Sandbox cleanup failed.",
    "unknown": "The Daytona diagnostic failed safely.",
}


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    if value is None:
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _failure_category(exc: BaseException, step: DoctorStepName) -> DoctorFailureCategory:
    step_categories: dict[DoctorStepName, DoctorFailureCategory] = {
        "settings": "settings",
        "database": "database",
        "cleanup": "cleanup",
    }
    if step in step_categories:
        return step_categories[step]
    if step == "rlm":
        return "rlm_provider"
    if step == "sandbox" and _is_snapshot_mismatch(exc):
        return "snapshot_mismatch"
    provider_kind = classify_provider_error(exc)
    if provider_kind == "mount_mismatch":
        return "mount_mismatch"
    if provider_kind == "auth":
        return "auth"
    if provider_kind == "quota":
        return "quota"
    if provider_kind in {"network", "timeout"}:
        return "network_timeout"
    if provider_kind == "provider_5xx":
        return "provider_5xx"
    if provider_kind == "request_validation":
        return "request_validation"
    if step == "interpreter":
        return "interpreter"
    status = _status_code(exc)
    if status in {405, 406, 415}:
        return "request_validation"
    error_name = type(exc).__name__.lower()
    if isinstance(exc, (TimeoutError, ConnectionError)) or any(
        marker in error_name for marker in ("timeout", "network", "connection", "connect")
    ):
        return "network_timeout"
    return "unknown"


def _failed_step(name: DoctorStepName, exc: BaseException) -> DaytonaDoctorStep:
    category = _failure_category(exc, name)
    return DaytonaDoctorStep(name, "fail", _FAILURE_MESSAGES[category], category)


def _is_snapshot_mismatch(exc: BaseException) -> bool:
    cause_type = getattr(exc, "cause_type", None)
    return isinstance(cause_type, str) and "snapshot" in cause_type.lower() and "mismatch" in cause_type.lower()


def _snapshot_probe_status(sandbox: Any, expected_spec: DaytonaSandboxSpec) -> DoctorStepStatus:
    """Validate provider-reported snapshot identity when the SDK exposes it."""
    marker = getattr(sandbox, "snapshot", None)
    if marker is None:
        return "unsupported"
    if not isinstance(marker, str):
        marker = getattr(marker, "name", None)
    if not isinstance(marker, str) or not marker.strip():
        return "unsupported"
    if marker.strip() != expected_spec.snapshot:
        raise DaytonaAdapterError(
            message="sandbox snapshot does not match configured Fleet snapshot",
            cause_type="SandboxSnapshotMismatch",
        )
    return "pass"


def _settings_failure(settings: Settings) -> Exception | None:
    key = settings.daytona_api_key
    raw_key = key.get_secret_value().strip() if key is not None else ""
    if not raw_key or not (settings.database_url or "").strip():
        return ValueError("required settings are missing")
    try:
        volume_config_from_settings(settings)
        sandbox_spec_from_settings(settings)
    except (TypeError, ValueError) as exc:
        return exc
    return None


async def run_daytona_doctor(
    settings: Settings,
    *,
    dependencies: DaytonaDoctorDependencies | None = None,
) -> DaytonaDoctorResult:
    """Run the opt-in Daytona doctor and always clean up acquired resources."""
    settings_error = _settings_failure(settings)
    if settings_error is not None:
        failed = _failed_step("settings", settings_error)
        return DaytonaDoctorResult(
            ok=False,
            steps=(failed,),
            failure_category=failed.category,
            readiness=profile_readiness_steps(),
        )
    if dependencies is None:
        try:
            dependencies = _ProductionDaytonaDoctorDependencies(settings)
        except Exception as exc:
            failed = _failed_step("provider", exc)
            return DaytonaDoctorResult(
                ok=False,
                steps=(
                    DaytonaDoctorStep("settings", "pass", _SUCCESS_MESSAGES["settings"]),
                    failed,
                ),
                failure_category=failed.category,
                readiness=profile_readiness_steps(),
            )

    steps: list[DaytonaDoctorStep] = [DaytonaDoctorStep("settings", "pass", _SUCCESS_MESSAGES["settings"])]
    volume = volume_config_from_settings(settings)
    snapshot_spec = sandbox_spec_from_settings(settings)
    workspace_id = uuid4()
    doctor_id = uuid4().hex
    sandbox: Any | None = None
    primary_failure: DaytonaDoctorStep | None = None
    mount_status: DoctorStepStatus = "not-exercised"
    imports_status: DoctorStepStatus = "not-exercised"
    snapshot_status: DoctorStepStatus = "not-exercised"
    current_step: DoctorStepName = "database"
    try:
        await dependencies.check_database(settings)
        steps.append(DaytonaDoctorStep("database", "pass", _SUCCESS_MESSAGES["database"]))
        current_step = "provider"
        volume_id = await dependencies.resolve_volume(settings)
        steps.append(DaytonaDoctorStep("provider", "pass", _SUCCESS_MESSAGES["provider"]))
        expected = ExpectedWorkspaceMount(
            volume_id=volume_id,
            volume_subpath=workspace_volume_subpath(workspace_id),
            mount_path=volume.mount_path,
            workspace_id=workspace_id,
        )
        labels = {
            "fleet_package": "fleet_rlm",
            "purpose": "fleet-daytona-doctor",
            "doctor_id": doctor_id,
            "workspace_id": str(workspace_id),
        }
        current_step = "sandbox"
        sandbox = await dependencies.create_sandbox(
            expected_mount=expected,
            labels=labels,
            ephemeral=True,
        )
        snapshot_status = _snapshot_probe_status(sandbox, snapshot_spec)
        verify_mount = getattr(dependencies, "verify_mount", None)
        if not callable(verify_mount):
            mount_status = "unsupported"
        else:
            # Set failure before entering the provider call. Any exception
            # after this point means the mount probe was exercised and failed;
            # it must not be mistaken for an early, unexercised exit.
            mount_status = "fail"
            await verify_mount(sandbox, expected)
            mount_status = "pass"
        steps.append(DaytonaDoctorStep("sandbox", "pass", _SUCCESS_MESSAGES["sandbox"]))
        check_rlm_readiness = getattr(dependencies, "check_rlm_readiness", None)
        if callable(check_rlm_readiness):
            current_step = "rlm"
            await check_rlm_readiness(settings, sandbox)
            steps.append(DaytonaDoctorStep("rlm", "pass", _SUCCESS_MESSAGES["rlm"]))
        current_step = "interpreter"
        execute = getattr(dependencies, "execute", None)
        if not callable(execute):
            imports_status = "unsupported"
        else:
            # As above, record the attempted state before awaiting external
            # work so import/runtime failures are reported as ``fail``.
            imports_status = "fail"
            output = await execute(sandbox)
            if output.strip() != "fleet-doctor-ok":
                raise RuntimeError("interpreter diagnostic output mismatch")
            imports_status = "pass"
            steps.append(DaytonaDoctorStep("interpreter", "pass", _SUCCESS_MESSAGES["interpreter"]))
    except Exception as exc:
        if current_step == "sandbox" and _is_snapshot_mismatch(exc):
            snapshot_status = "fail"
        primary_failure = _failed_step(current_step, exc)
        steps.append(primary_failure)
    finally:
        if sandbox is not None:
            try:
                await dependencies.delete_sandbox(sandbox)
            except Exception as exc:
                cleanup_failure = _failed_step("cleanup", exc)
                steps.append(cleanup_failure)
                if primary_failure is None:
                    primary_failure = cleanup_failure
            else:
                steps.append(DaytonaDoctorStep("cleanup", "pass", _SUCCESS_MESSAGES["cleanup"]))
        try:
            await dependencies.close()
        except Exception as exc:
            cleanup_failure = _failed_step("cleanup", exc)
            steps.append(cleanup_failure)
            if primary_failure is None:
                primary_failure = cleanup_failure

    return DaytonaDoctorResult(
        ok=primary_failure is None,
        steps=tuple(steps),
        failure_category=primary_failure.category if primary_failure is not None else None,
        readiness=profile_readiness_steps(
            snapshot_status=snapshot_status,
            imports_status=imports_status,
            mount_status=mount_status,
        ),
    )


__all__ = [
    "BASE_IMAGE",
    "DEFAULT_CHILD_SNAPSHOT_NAME",
    "DEFAULT_SNAPSHOT_NAME",
    "SEMANTIC_CHILD_RESOURCES",
    "SESSION_RESOURCES",
    "DaytonaDoctorDependencies",
    "DaytonaDoctorResult",
    "DaytonaDoctorStep",
    "DaytonaEnvironmentManifest",
    "DaytonaEnvironmentProfile",
    "DaytonaSandboxSpec",
    "DoctorStepStatus",
    "MissingImportObservation",
    "MissingImportOutcome",
    "build_snapshot_image",
    "environment_manifest",
    "normalize_missing_import_observation",
    "profile_readiness_steps",
    "run_daytona_doctor",
    "snapshot_dependency_import_names",
    "snapshot_dependency_sha256",
    "snapshot_execution_dependencies",
]
