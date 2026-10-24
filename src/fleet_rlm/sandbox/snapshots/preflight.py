"""Runtime snapshot readiness, provider identity, caching, and provenance."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any

from daytona import DaytonaNotFoundError

from .definition import CPU_FLOOR, DEFAULT_DEFINITION, DISK_FLOOR, MEMORY_FLOOR, SNAPSHOT_NAME

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
