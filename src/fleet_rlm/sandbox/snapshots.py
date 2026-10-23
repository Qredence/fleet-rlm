"""Repository-owned snapshot definition, provider preflight, and recorded identity."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, replace
from typing import Any

from daytona import CreateSnapshotParams, Daytona, DaytonaConfig, Image, Resources
from daytona.common.image import SupportedPythonSeries

from fleet_rlm.config import get_settings

# Repository-owned Turn environment; provider availability is checked by preflight.
SNAPSHOT_NAME = "fleet-rlm-python313-v13"
PYTHON_VERSION: SupportedPythonSeries = "3.13"
PACKAGE_PINS: tuple[tuple[str, str], ...] = (
    ("numpy", "2.5.1"),
    ("pandas", "3.0.5"),
    ("mpmath", "1.4.1"),
)
CPU_FLOOR = 4
MEMORY_FLOOR = 8
DISK_FLOOR = 8
BUILD_COMMAND = 'uv run python -c "from fleet_rlm.sandbox.snapshots import build_snapshot; build_snapshot()"'
MAX_REFUSAL_CHARS = 1000
PROVENANCE_KEYS = (
    "snapshot_name",
    "snapshot_id",
    "snapshot_cpu",
    "snapshot_mem",
    "snapshot_disk",
    "snapshot_definition_digest",
)
PRE_PROVENANCE = "pre-provenance"


def provenance_state(values: dict[str, Any]) -> str:
    """Distinguish recorded, unresolved, and historical snapshot provenance."""
    if all(key in values for key in PROVENANCE_KEYS):
        return "recorded"
    return "unresolved" if "deadline_source" in values else PRE_PROVENANCE


@dataclass(frozen=True)
class SnapshotDefinition:
    """The complete, hashable definition of one execution environment."""

    name: str
    cpu: int
    memory: int
    disk: int
    python_version: SupportedPythonSeries
    packages: tuple[tuple[str, str], ...]

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical dictionary representation of the snapshot definition."""
        return {
            "name": self.name,
            "resources": {"cpu": self.cpu, "memory": self.memory, "disk": self.disk},
            "image": {"builder": "debian_slim", "python": self.python_version},
            "packages": dict(sorted(self.packages)),
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
        return Image.debian_slim(self.python_version).pip_install(
            [f"{package}=={version}" for package, version in self.packages]
        )


DEFAULT_DEFINITION = SnapshotDefinition(
    name=SNAPSHOT_NAME,
    cpu=CPU_FLOOR,
    memory=MEMORY_FLOOR,
    disk=DISK_FLOOR,
    python_version=PYTHON_VERSION,
    packages=PACKAGE_PINS,
)


@dataclass(frozen=True)
class SnapshotIdentity:
    """What the provider actually resolved, recorded so provenance never re-derives it."""

    name: str
    id: str | None
    cpu: int | None
    mem: int | None
    disk: int | None
    definition_digest: str

    def as_dict(self) -> dict[str, Any]:
        """Return the resolved snapshot identity as a dictionary."""
        return {
            "name": self.name,
            "id": self.id,
            "cpu": self.cpu,
            "mem": self.mem,
            "disk": self.disk,
            "definition_digest": self.definition_digest,
        }

    def provenance(self) -> dict[str, Any]:
        """Return the resolved snapshot identity formatted with provenance keys."""
        return {
            "snapshot_name": self.name,
            "snapshot_id": self.id,
            "snapshot_cpu": self.cpu,
            "snapshot_mem": self.mem,
            "snapshot_disk": self.disk,
            "snapshot_definition_digest": self.definition_digest,
        }


class SnapshotUnavailableError(RuntimeError):
    """The Turn snapshot is missing, not active, or below the required floors."""


class _ResolutionFlight:
    __slots__ = ("error", "event", "identity")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.identity: SnapshotIdentity | None = None
        self.error: BaseException | None = None


_RESOLUTIONS: dict[tuple[str, str, str], SnapshotIdentity] = {}
_RESOLUTIONS_LOCK = threading.Lock()
_INFLIGHT: dict[tuple[str, str, str], _ResolutionFlight] = {}


def clear_preflight_cache() -> None:
    """Drop cached snapshot resolutions."""
    with _RESOLUTIONS_LOCK:
        _RESOLUTIONS.clear()


def _optional_int(value: Any) -> int | None:
    """Convert a resource value to int, returning None on TypeError or ValueError."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_snapshot(key: tuple[str, str, str], service: Any, name: str) -> SnapshotIdentity:
    """Read and cache an active snapshot identity that meets the repository resource floors.

    Provider lookup errors are treated as a missing snapshot. Raise SnapshotUnavailableError
    for missing, inactive, or undersized snapshots.
    """
    try:
        snapshot = service.get(name)
    except Exception:
        snapshot = None
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
        raise SnapshotUnavailableError(
            f"Snapshot '{name}' is unusable: state={state}, cpu={cpu}, mem={mem}, disk={disk}. "
            f"Required: state=active, cpu>={CPU_FLOOR}, mem>={MEMORY_FLOOR}, disk>={DISK_FLOOR}. "
            f"Build it from the repository definition with: {BUILD_COMMAND}"
        )
    definition = replace(DEFAULT_DEFINITION, name=name)
    identity = SnapshotIdentity(
        name=str(getattr(snapshot, "name", name) or name),
        id=getattr(snapshot, "id", None),
        cpu=cpu,
        mem=mem,
        disk=disk,
        definition_digest=definition.digest(),
    )
    with _RESOLUTIONS_LOCK:
        _RESOLUTIONS[key] = identity
    return identity


def preflight(
    service: Any,
    *,
    name: str = SNAPSHOT_NAME,
    api_key: str = "",
    org_id: str = "",
    refresh: bool = False,
) -> SnapshotIdentity:
    """Resolve and validate a Turn snapshot before any sandbox is provisioned.

    Return the provider identity with the repository definition digest. Successful
    resolutions are cached by API key, organization, and name; `refresh` bypasses the
    cached result. Concurrent lookups for the same key share an in-flight resolution.

    Raises:
        SnapshotUnavailableError: The snapshot is missing, not active, or below a resource
            floor. Provider lookup errors are converted to this refusal.
    """
    key = (api_key, org_id, name)
    with _RESOLUTIONS_LOCK:
        if not refresh and (cached := _RESOLUTIONS.get(key)) is not None:
            return cached
        flight = _INFLIGHT.get(key)
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
        flight.identity = _resolve_snapshot(key, service, name)
        return flight.identity
    except BaseException as exc:
        flight.error = exc
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
    *, name: str = SNAPSHOT_NAME, api_key: str | None = None, org_id: str | None = None
) -> SnapshotIdentity:
    """Build the repository-defined snapshot and verify it through the provider.

    Omitted credentials come from Fleet settings; `name` overrides only the provider name.

    Raises:
        ValueError: An API key or organization is empty after credential resolution.
        SnapshotUnavailableError: The created snapshot cannot be read, is not active,
            or is below a resource floor.

    Settings, image-building, and provider creation errors propagate.
    """
    key, organization = _resolved_credentials(api_key, org_id)
    client = Daytona(DaytonaConfig(api_key=key, organization_id=organization))
    client.snapshot.create(
        CreateSnapshotParams(name=name, image=DEFAULT_DEFINITION.image(), resources=DEFAULT_DEFINITION.resources())
    )
    return preflight(client.snapshot, name=name, api_key=key, org_id=organization, refresh=True)


def verify_snapshot(
    *, name: str = SNAPSHOT_NAME, api_key: str | None = None, org_id: str | None = None
) -> SnapshotIdentity:
    """Read the snapshot back from the provider and enforce the Turn state and floors.

    Refreshes the cached identity. Omitted credentials come from Fleet settings.

    Raises:
        ValueError: An API key or organization is empty after credential resolution.
        SnapshotUnavailableError: The snapshot lookup fails, is not active, or is below a
            resource floor.

    Settings and provider client initialization errors propagate.
    """
    key, organization = _resolved_credentials(api_key, org_id)
    client = Daytona(DaytonaConfig(api_key=key, organization_id=organization))
    return preflight(client.snapshot, name=name, api_key=key, org_id=organization, refresh=True)
