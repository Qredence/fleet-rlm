"""Versioned Daytona image recipe, runtime readiness/provenance, and explicit operator operations."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from daytona import (
    CreateSnapshotParams,
    Daytona,
    DaytonaConfig,
    DaytonaNotFoundError,
    Image,
    Resources,
)
from daytona.common.image import SupportedPythonSeries

# Repository-owned Turn environment; provider availability is checked by preflight.
SNAPSHOT_NAME = "fleet-rlm-python313-v14"
PYTHON_VERSION: SupportedPythonSeries = "3.13"
PYTHON_BASE_IMAGE = "python:3.13-slim-bookworm@sha256:e32be0e55814a08977501ca863271d9bd02443710db8f2afaf8372e2c4508530"
SYSTEM_PACKAGES = ("git", "nodejs", "npm", "ripgrep", "jq", "ca-certificates")
NPM_GLOBAL_PACKAGES = ("pnpm@10.18.3", "typescript@5.9.3")
LOCK_PATH = Path(__file__).with_name("snapshot-requirements.txt")


def _locked_packages(path: Path) -> tuple[tuple[str, str], ...]:
    """Read package pins from uv's multiline, hash-locked requirements format."""
    packages: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(("#", " ", "\t")) or "==" not in line:
            continue
        name, _, version = line.split()[0].partition("==")
        packages.append((name, version))
    return tuple(packages)


PACKAGE_PINS = _locked_packages(LOCK_PATH)
CPU_FLOOR = 4
MEMORY_FLOOR = 8
DISK_FLOOR = 8


@dataclass(frozen=True)
class SnapshotDefinition:
    """The declared recipe for one execution environment, including its Python lock."""

    name: str
    cpu: int
    memory: int
    disk: int
    python_version: SupportedPythonSeries
    packages: tuple[tuple[str, str], ...]
    base_image: str = PYTHON_BASE_IMAGE
    system_packages: tuple[str, ...] = SYSTEM_PACKAGES
    npm_global_packages: tuple[str, ...] = NPM_GLOBAL_PACKAGES

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical dictionary representation of the snapshot definition."""
        return {
            "name": self.name,
            "resources": {"cpu": self.cpu, "memory": self.memory, "disk": self.disk},
            "image": {"base": self.base_image, "python": self.python_version},
            "system_packages": list(self.system_packages),
            "npm_global_packages": list(self.npm_global_packages),
            "packages": dict(sorted(self.packages)),
            "build_lock_sha256": hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest(),
        }

    def digest(self) -> str:
        """Return the SHA-256 hex digest of the canonical snapshot definition."""
        canonical = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def resources(self) -> Resources:
        """Return the Daytona Resources object for this snapshot definition."""
        return Resources(cpu=self.cpu, memory=self.memory, disk=self.disk)

    def image(self) -> Image:
        """Return the Daytona Image builder for this snapshot definition."""
        return (
            Image.base(self.base_image)
            .run_commands(
                "apt-get update && apt-get install -y --no-install-recommends "
                + " ".join(self.system_packages)
                + " && rm -rf /var/lib/apt/lists/*",
                "npm install --global " + " ".join(self.npm_global_packages),
            )
            .pip_install_from_requirements(
                str(LOCK_PATH), extra_options="--require-hashes --only-binary=:all: --no-cache-dir"
            )
        )


DEFAULT_DEFINITION = SnapshotDefinition(
    name=SNAPSHOT_NAME,
    cpu=CPU_FLOOR,
    memory=MEMORY_FLOOR,
    disk=DISK_FLOOR,
    python_version=PYTHON_VERSION,
    packages=PACKAGE_PINS,
)


PREFLIGHT_CACHE_TTL_SECONDS = 60
SNAPSHOT_BUILD_TIMEOUT_SECONDS = 20 * 60
MAX_REFUSAL_CHARS = 1000
BUILD_COMMAND = "Open /setup in fleet cli and choose Build snapshot"
PROVENANCE_KEYS = (
    "snapshot_name",
    "snapshot_id",
    "snapshot_cpu",
    "snapshot_mem",
    "snapshot_disk",
    "snapshot_definition_digest",
    "snapshot_build_digest",
)
_REQUIRED_PROVENANCE_KEYS = PROVENANCE_KEYS[:-1]
PRE_PROVENANCE = "pre-provenance"


def provenance_state(values: dict[str, Any]) -> str:
    """Distinguish recorded, unresolved, and historical snapshot provenance."""
    if all(key in values for key in _REQUIRED_PROVENANCE_KEYS):
        return "recorded"
    return "unresolved" if "deadline_source" in values else PRE_PROVENANCE


@dataclass(frozen=True)
class SnapshotIdentity:
    """What the provider actually resolved, recorded so provenance never re-derives it."""

    name: str
    id: str | None
    cpu: int | None
    mem: int | None
    disk: int | None
    definition_digest: str | None
    build_digest: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the resolved snapshot identity as a dictionary."""
        result = {
            "name": self.name,
            "id": self.id,
            "cpu": self.cpu,
            "mem": self.mem,
            "disk": self.disk,
            "definition_digest": self.definition_digest,
        }
        if self.build_digest is not None:
            result["build_digest"] = self.build_digest
        return result

    def provenance(self) -> dict[str, Any]:
        """Return the resolved snapshot identity formatted with provenance keys."""
        result = {
            "snapshot_name": self.name,
            "snapshot_id": self.id,
            "snapshot_cpu": self.cpu,
            "snapshot_mem": self.mem,
            "snapshot_disk": self.disk,
            "snapshot_definition_digest": self.definition_digest,
        }
        if self.build_digest is not None:
            result["snapshot_build_digest"] = self.build_digest
        return result


class SnapshotUnavailableError(RuntimeError):
    """The Turn snapshot is missing, not active, or below the required floors."""

    def __init__(self, message: str, *, state: str = "unknown") -> None:
        super().__init__(message)
        self.state = state


class _ResolutionFlight:
    __slots__ = ("error", "event", "identity")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.identity: SnapshotIdentity | None = None
        self.error: BaseException | None = None


_RESOLUTIONS: dict[tuple[str, str, str], SnapshotIdentity] = {}
_RESOLUTION_TIMES: dict[tuple[str, str, str], float] = {}
_RESOLUTIONS_LOCK = threading.Lock()
_INFLIGHT: dict[tuple[str, str, str], _ResolutionFlight] = {}


def clear_preflight_cache() -> None:
    """Drop resolutions and detach old flights; their existing waiters still finish."""
    with _RESOLUTIONS_LOCK:
        _RESOLUTIONS.clear()
        _RESOLUTION_TIMES.clear()
        _INFLIGHT.clear()


def _optional_int(value: Any) -> int | None:
    """Convert a resource value to int, returning None on TypeError or ValueError."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_snapshot(service: Any, name: str) -> SnapshotIdentity:
    """Read and cache an active snapshot identity that meets the repository resource floors.

    Only not-found provider errors are treated as a missing snapshot. Other provider
    failures raise a distinct refusal so access and availability issues stay actionable.
    """
    try:
        snapshot = service.get(name)
    except Exception as exc:
        if _is_not_found(exc):
            snapshot = None
        else:
            raise SnapshotUnavailableError(
                f"Snapshot '{name}' lookup failed ({type(exc).__name__}). "
                "Check Daytona access and provider availability, then retry.",
                state="lookup_failed",
            ) from exc
    raw_state = getattr(snapshot, "state", None) if snapshot is not None else None
    state = str(getattr(raw_state, "value", raw_state) or "").strip().lower() or "missing"
    cpu = _optional_int(getattr(snapshot, "cpu", None)) if snapshot is not None else None
    mem = _optional_int(getattr(snapshot, "mem", None)) if snapshot is not None else None
    disk = _optional_int(getattr(snapshot, "disk", None)) if snapshot is not None else None
    if (
        state != "active"
        or cpu is None
        or mem is None
        or disk is None
        or cpu < CPU_FLOOR
        or mem < MEMORY_FLOOR
        or disk < DISK_FLOOR
    ):
        recovery = (
            " Open /setup and choose Activate snapshot, then retry."
            if state == "inactive"
            else (
                " The snapshot may still be building; wait for it to become active and retry."
                if state in {"pending", "building", "pulling", "snapshotting"}
                else (
                    f" Build it from the repository definition with: {BUILD_COMMAND}"
                    if name == SNAPSHOT_NAME
                    else " Check the configured custom snapshot name and build it in Daytona."
                )
            )
        )
        raise SnapshotUnavailableError(
            f"Snapshot '{name}' is unusable: state={state}, cpu={cpu}, mem={mem}, disk={disk}. "
            f"Required: state=active, cpu>={CPU_FLOOR}, mem>={MEMORY_FLOOR}, disk>={DISK_FLOOR}."
            f"{recovery}",
            state=state,
        )
    identity = SnapshotIdentity(
        name=str(getattr(snapshot, "name", name) or name),
        id=getattr(snapshot, "id", None),
        cpu=cpu,
        mem=mem,
        disk=disk,
        definition_digest=DEFAULT_DEFINITION.digest() if name == SNAPSHOT_NAME else None,
        build_digest=_provider_build_digest(snapshot),
    )
    return identity


def _is_not_found(error: Exception) -> bool:
    """Recognize provider not-found errors without hiding other failures."""
    if isinstance(error, DaytonaNotFoundError):
        return True
    for attr in ("status", "status_code", "http_status"):
        if getattr(error, attr, None) == 404:
            return True
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None) == 404


def _provider_build_digest(snapshot: Any) -> str | None:
    """Hash provider-reported image/build metadata without storing raw Dockerfile contents."""
    build_info = getattr(snapshot, "build_info", None)
    if build_info is None:
        return None

    def value(obj: Any, key: str) -> Any:
        return getattr(obj, key, None) if not isinstance(obj, dict) else obj.get(key)

    dockerfile = value(build_info, "dockerfile_content")
    content = {
        "image_name": getattr(snapshot, "image_name", None),
        "snapshot_ref": value(build_info, "snapshot_ref"),
        "context_hashes": value(build_info, "context_hashes"),
        "dockerfile_sha256": hashlib.sha256(dockerfile.encode()).hexdigest() if dockerfile else None,
    }
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def preflight(
    service: Any,
    *,
    name: str = SNAPSHOT_NAME,
    api_key: str = "",
    org_id: str = "",
    refresh: bool = False,
) -> SnapshotIdentity:
    """Resolve and validate a Turn snapshot before any sandbox is provisioned.

    Return provider identity and the intended definition digest for the repository name.
    Successes are cached for 60 seconds by API key, organization, and name. Ordinary
    callers share an in-flight lookup; explicit refresh starts a new provider lookup.

    Raises:
        SnapshotUnavailableError: The snapshot is missing, not active, or below a resource
            floor. Provider lookup errors are converted to this refusal.
    """
    key = (api_key, org_id, name)
    with _RESOLUTIONS_LOCK:
        cached = _RESOLUTIONS.get(key)
        resolved_at = _RESOLUTION_TIMES.get(key, 0.0)
        if not refresh and cached is not None and time.monotonic() - resolved_at < PREFLIGHT_CACHE_TTL_SECONDS:
            return cached
        _RESOLUTIONS.pop(key, None)
        _RESOLUTION_TIMES.pop(key, None)
        flight = None if refresh else _INFLIGHT.get(key)
        leader = flight is None
        if leader:
            flight = _INFLIGHT[key] = _ResolutionFlight()

    if not leader:
        flight.event.wait()
        if flight.error is not None:
            raise flight.error
        assert flight.identity is not None
        return flight.identity

    try:
        flight.identity = _resolve_snapshot(service, name)
        with _RESOLUTIONS_LOCK:
            if _INFLIGHT.get(key) is flight:
                _RESOLUTIONS[key] = flight.identity
                _RESOLUTION_TIMES[key] = time.monotonic()
        return flight.identity
    except BaseException as exc:
        flight.error = exc
        with _RESOLUTIONS_LOCK:
            if _INFLIGHT.get(key) is flight:
                _RESOLUTIONS.pop(key, None)
                _RESOLUTION_TIMES.pop(key, None)
        raise
    finally:
        with _RESOLUTIONS_LOCK:
            if _INFLIGHT.get(key) is flight:
                _INFLIGHT.pop(key)
        flight.event.set()


def _resolved_credentials(api_key: str | None, org_id: str | None) -> tuple[str, str]:
    """Resolve omitted credentials from Fleet settings and strip surrounding whitespace.

    Raise ValueError when either resolved credential is empty; settings errors propagate.
    """
    if api_key is None or org_id is None:
        from fleet_rlm.config import get_settings

        settings = get_settings()
        if api_key is None:
            api_key = settings.fleet_daytona_api_key
        if org_id is None:
            org_id = settings.fleet_daytona_org_id
    key = (api_key or "").strip()
    organization = (org_id or "").strip()
    if not key:
        raise ValueError(
            "Daytona API key is not configured: set FLEET_DAYTONA_API_KEY in the environment or .env, "
            "or pass api_key explicitly."
        )
    if not organization:
        raise ValueError(
            "Daytona organization is not configured: set FLEET_DAYTONA_ORG_ID, add org_id to the "
            "[daytona] table of the Fleet TOML configuration, or pass org_id explicitly."
        )
    return key, organization


def build_snapshot(
    *,
    name: str = SNAPSHOT_NAME,
    api_key: str | None = None,
    org_id: str | None = None,
    timeout_seconds: float = SNAPSHOT_BUILD_TIMEOUT_SECONDS,
    on_logs: Callable[[str], None] | None = print,
) -> SnapshotIdentity:
    """Build the repository-defined snapshot and verify it through the provider.

    Omitted credentials come from Fleet settings; `name` overrides only the provider name.
    The build streams logs and waits up to 20 minutes by default. A timeout does not
    guarantee the remote build stopped: inspect provider state before retrying.

    Raises:
        ValueError: Credentials are empty or the build timeout is not finite and positive.
        SnapshotUnavailableError: The created snapshot cannot be read, is not active,
            or is below a resource floor.

    Settings, image-building, and provider creation errors propagate.
    """
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Snapshot build timeout_seconds must be finite and positive")
    key, organization = _resolved_credentials(api_key, org_id)
    client = Daytona(DaytonaConfig(api_key=key, organization_id=organization))
    try:
        existing = client.snapshot.get(name)
    except Exception as exc:
        if not _is_not_found(exc):
            raise SnapshotUnavailableError(
                "Snapshot lookup failed; verify access before building.", state="lookup_failed"
            ) from None
    else:
        if existing is not None:
            raise SnapshotUnavailableError(
                "Snapshot already exists; verify or activate it instead of replacing it.", state="exists"
            )
    client.snapshot.create(
        CreateSnapshotParams(name=name, image=DEFAULT_DEFINITION.image(), resources=DEFAULT_DEFINITION.resources()),
        timeout=timeout_seconds,
        on_logs=on_logs,
    )
    return preflight(client.snapshot, name=name, api_key=key, org_id=organization, refresh=True)


def activate_snapshot(
    *,
    name: str = SNAPSHOT_NAME,
    api_key: str | None = None,
    org_id: str | None = None,
    timeout_seconds: float = SNAPSHOT_BUILD_TIMEOUT_SECONDS,
) -> SnapshotIdentity:
    """Activate explicitly, then poll readiness without repeating the mutation."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Snapshot activation timeout_seconds must be finite and positive")
    key, organization = _resolved_credentials(api_key, org_id)
    client = Daytona(DaytonaConfig(api_key=key, organization_id=organization))
    try:
        return preflight(client.snapshot, name=name, api_key=key, org_id=organization, refresh=True)
    except SnapshotUnavailableError as exc:
        if exc.state != "inactive":
            raise
    deadline = time.monotonic() + timeout_seconds
    # Daytona 0.224.0's public activate() omits the generated client's request timeout.
    snapshots_api = cast(Any, client.snapshot)._SnapshotService__snapshots_api
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SnapshotUnavailableError(
            "Activation is not confirmed; verify provider state before retrying.", state="pending"
        ) from None
    snapshot = snapshots_api.get_snapshot(name, _request_timeout=remaining)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SnapshotUnavailableError(
            "Activation is not confirmed; verify provider state before retrying.", state="pending"
        ) from None
    snapshots_api.activate_snapshot(snapshot.id, _request_timeout=remaining)

    def get_snapshot_with_timeout(snapshot_name: str) -> Any:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise TimeoutError("Snapshot activation readiness deadline expired")
        return snapshots_api.get_snapshot(snapshot_name, _request_timeout=remaining_seconds)

    timed_snapshot_service = SimpleNamespace(get=get_snapshot_with_timeout)
    while True:
        try:
            return preflight(timed_snapshot_service, name=name, api_key=key, org_id=organization, refresh=True)
        except SnapshotUnavailableError as exc:
            if exc.state not in {"inactive", "pending", "building", "pulling", "snapshotting", "activating"}:
                if exc.state == "lookup_failed" and time.monotonic() >= deadline:
                    raise SnapshotUnavailableError(
                        "Activation is not confirmed; verify provider state before retrying.", state="pending"
                    ) from None
                raise
            if time.monotonic() >= deadline:
                raise SnapshotUnavailableError(
                    "Activation is not confirmed; verify provider state before retrying.", state="pending"
                ) from None
        time.sleep(min(2, max(0, deadline - time.monotonic())))
