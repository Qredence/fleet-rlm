"""Daytona snapshot operations — CRUD, bootstrap, resolution, and fallback."""

from __future__ import annotations

import dataclasses
import logging
import re
import shlex
import time as _time
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from .async_compat import _run_sync_in_thread
from .config import ResolvedDaytonaConfig, resolve_daytona_config
from .config import (
    build_daytona_client as _build_daytona_client,
)
from .config import (
    classify_daytona_sdk_error as _classify_daytona_sdk_error,
)
from .config import (
    daytona_import_error as _daytona_import_error,
)
from .config import format_daytona_sdk_error as _format_daytona_sdk_error
from .errors import DaytonaDiagnosticError
from .models import SandboxSpec

if TYPE_CHECKING:
    from .protocols import DaytonaSandbox

logger = logging.getLogger(__name__)

DEFAULT_SNAPSHOT_PACKAGES: list[str] = [
    "dspy-ai",
    "numpy",
    "pandas",
    "httpx",
    "pydantic",
]

DEFAULT_SNAPSHOT_NAME = "fleet-rlm-base"
DEFAULT_SNAPSHOT_BASE_IMAGE = "python:3.12-slim"
_VALID_PACKAGE_SPEC_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\],<>=!~]*$")


def _experimental_call(
    sandbox: DaytonaSandbox,
    method_name: str,
    *args: Any,
    category: str = "sandbox_experimental_error",
    phase: str = "sandbox_experimental",
    **kwargs: Any,
) -> Any:
    """Safely invoke an experimental Daytona SDK method on *sandbox*."""
    try:
        method = getattr(sandbox, method_name)
        return method(*args, **kwargs)
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona {method_name} failure: {exc}",
            category=category,
            phase=phase,
        ) from exc


def _snapshot_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "name": snapshot.name,
        "id": snapshot.id,
        "state": str(getattr(snapshot, "state", "unknown")),
        "image_name": getattr(snapshot, "image_name", None),
    }


def build_base_snapshot_image(
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    packages: list[str] | None = None,
) -> Any:
    """Build the default Daytona declarative image used by snapshots and fallback sandboxes."""
    try:
        from daytona import Image as DaytonaImage
    except ImportError as exc:  # pragma: no cover - environment specific
        raise _daytona_import_error(exc) from exc

    packages_to_install = packages if packages is not None else DEFAULT_SNAPSHOT_PACKAGES
    for package in packages_to_install:
        if not package or not _VALID_PACKAGE_SPEC_PATTERN.fullmatch(package):
            msg = f"Invalid package spec for snapshot image install: {package!r}"
            raise ValueError(msg)

    image = DaytonaImage.base(base_image).run_commands("pip install uv")
    if packages_to_install:
        install_command = shlex.join(["uv", "pip", "install", "--system", *packages_to_install])
        image = image.run_commands(install_command)
    return image


def list_snapshots(
    config: ResolvedDaytonaConfig | None = None,
) -> list[dict[str, Any]]:
    """Return a lightweight list of available snapshots."""
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        result = client.snapshot.list()
        items = result.items if hasattr(result, "items") else result
        return [_snapshot_summary(snapshot) for snapshot in items]
    finally:
        with suppress(Exception):
            client.close()


async def alist_snapshots(
    config: ResolvedDaytonaConfig | None = None,
) -> list[dict[str, Any]]:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(list_snapshots, config)


def get_snapshot(
    name: str,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> dict[str, Any] | None:
    """Look up a snapshot by *name*, returning a summary dict or ``None``."""
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snapshot = client.snapshot.get(name)
        return _snapshot_summary(snapshot)
    except Exception as exc:
        if _snapshot_lookup_missing(exc):
            logger.debug("snapshot_lookup_missing", extra={"name": name}, exc_info=True)
            return None
        raise DaytonaDiagnosticError(
            f"Daytona snapshot lookup failure: {_format_daytona_sdk_error(exc)}",
            category="sandbox_snapshot_error",
            phase="snapshot_lookup",
        ) from exc
    finally:
        with suppress(Exception):
            client.close()


async def aget_snapshot(
    name: str,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> dict[str, Any] | None:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(get_snapshot, name, config=config)


def _snapshot_lookup_missing(exc: BaseException) -> bool:
    classification = _classify_daytona_sdk_error(exc)
    if classification.status_code == 404:
        return True
    lowered = classification.message.lower()
    return "snapshot" in lowered and "not found" in lowered


def _snapshot_create_conflict(exc: BaseException) -> bool:
    classification = _classify_daytona_sdk_error(exc)
    lowered = classification.message.lower()
    return classification.status_code in {400, 409} and any(
        token in lowered for token in ("already exists", "conflict", "duplicate")
    )


def _wait_for_snapshot_refresh_target(
    name: str,
    *,
    previous_snapshot_id: str | None,
    config: ResolvedDaytonaConfig,
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """Wait for a refreshed snapshot name to disappear or be replaced."""

    deadline = _time.monotonic() + timeout
    interval = 0.1

    while _time.monotonic() < deadline:
        current = get_snapshot(name, config=config)
        if current is None:
            return None
        current_id = str(current.get("id") or "") or None
        if previous_snapshot_id is None or current_id != previous_snapshot_id:
            return current
        _time.sleep(interval)
        interval = min(interval * 2, 1.0)

    raise DaytonaDiagnosticError(
        f"Timed out waiting for Daytona snapshot '{name}' to finish refreshing",
        category="sandbox_snapshot_error",
        phase="snapshot_refresh",
    )


def create_snapshot(
    name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    packages: list[str] | None = None,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Create a new Daytona snapshot with pre-installed packages."""
    try:
        from daytona.common.snapshot import CreateSnapshotParams
    except ImportError as exc:  # pragma: no cover - environment specific
        raise _daytona_import_error(exc) from exc

    image = build_base_snapshot_image(base_image=base_image, packages=packages)
    params = CreateSnapshotParams(name=name, image=image)
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snapshot = client.snapshot.create(params, on_logs=on_logs, timeout=0)
        logger.info("Snapshot '%s' created (id=%s)", snapshot.name, snapshot.id)
        return _snapshot_summary(snapshot)
    finally:
        with suppress(Exception):
            client.close()


async def acreate_snapshot(
    name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    packages: list[str] | None = None,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(
        create_snapshot, name, base_image=base_image, packages=packages, config=config, on_logs=on_logs
    )


def delete_snapshot(
    name: str,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> None:
    """Delete a Daytona snapshot by name or id."""
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snapshot = client.snapshot.get(name)
        client.snapshot.delete(snapshot)
    finally:
        with suppress(Exception):
            client.close()


async def adelete_snapshot(
    name: str,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> None:
    """Async wrapper — runs blocking SDK call in a thread."""
    await _run_sync_in_thread(delete_snapshot, name, config=config)


def bootstrap_snapshot(
    name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    refresh: bool = False,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Ensure the reusable Fleet Daytona base snapshot exists."""
    cfg = config or resolve_daytona_config()
    existing = get_snapshot(name, config=cfg)
    if existing is not None and not refresh:
        return {**existing, "created": False, "refreshed": False}
    if existing is not None:
        previous_snapshot_id = str(existing.get("id") or "") or None
        delete_snapshot(previous_snapshot_id or str(existing.get("name") or name), config=cfg)
        replacement = _wait_for_snapshot_refresh_target(
            name,
            previous_snapshot_id=previous_snapshot_id,
            config=cfg,
        )
        if replacement is not None:
            return {**replacement, "created": False, "refreshed": True}

    try:
        created = create_snapshot(
            name=name,
            base_image=base_image,
            config=cfg,
            on_logs=on_logs,
        )
    except Exception as exc:
        if _snapshot_create_conflict(exc):
            replacement = get_snapshot(name, config=cfg)
            if replacement is not None:
                return {**replacement, "created": False, "refreshed": existing is not None}
        raise
    return {**created, "created": True, "refreshed": existing is not None}


async def abootstrap_snapshot(
    name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    refresh: bool = False,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(
        bootstrap_snapshot, name, base_image=base_image, refresh=refresh, config=config, on_logs=on_logs
    )


def resolve_snapshot(
    preferred_name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> str | None:
    """Return the snapshot name if it exists and is ``ACTIVE``, else ``None``."""
    info = get_snapshot(preferred_name, config=config)
    state = str(info.get("state", "")).upper() if info else ""
    if info and state in ("ACTIVE", "SNAPSHOTSTATE.ACTIVE"):
        return info["name"]
    return None


async def aresolve_snapshot(
    preferred_name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> str | None:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(resolve_snapshot, preferred_name, config=config)


def resolve_sandbox_spec_snapshot(
    spec: SandboxSpec,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> SandboxSpec:
    """Resolve a snapshot-backed spec, falling back to the shared declarative image when needed."""
    if not spec.snapshot or spec.uses_declarative_image:
        return spec
    active_snapshot = resolve_snapshot(spec.snapshot, config=config)
    if active_snapshot is not None:
        return spec
    logger.info(
        "Snapshot '%s' not active; falling back to declarative image",
        spec.snapshot,
    )
    return fallback_to_declarative_image(spec)


async def aresolve_sandbox_spec_snapshot(
    spec: SandboxSpec,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> SandboxSpec:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(resolve_sandbox_spec_snapshot, spec, config=config)


def resolve_default_snapshot(*, image: Any, snapshot: str | None) -> str | None:
    """Choose the runtime default snapshot when neither image nor snapshot is set."""
    if snapshot or image:
        return snapshot
    return DEFAULT_SNAPSHOT_NAME


def fallback_to_declarative_image(spec: SandboxSpec) -> SandboxSpec:
    """Replace a snapshot-based spec with a declarative image build."""
    image = build_base_snapshot_image()
    return dataclasses.replace(spec, image=image, snapshot=None)


def create_sandbox_snapshot(
    session: Any,
    *,
    name: str,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Create a snapshot from the current state of a sandbox session."""
    _experimental_call(
        session.sandbox,
        "_experimental_create_snapshot",
        name=name,
        timeout=timeout,
        category="sandbox_snapshot_error",
        phase="sandbox_snapshot",
    )
    return {
        "name": name,
        "sandbox_id": getattr(session, "sandbox_id", None),
        "status": "created",
    }


async def acreate_sandbox_snapshot(
    session: Any,
    *,
    name: str,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(create_sandbox_snapshot, session, name=name, timeout=timeout)


__all__ = [
    "DEFAULT_SNAPSHOT_BASE_IMAGE",
    "DEFAULT_SNAPSHOT_NAME",
    "DEFAULT_SNAPSHOT_PACKAGES",
    "abootstrap_snapshot",
    "acreate_sandbox_snapshot",
    "acreate_snapshot",
    "adelete_snapshot",
    "aget_snapshot",
    "alist_snapshots",
    "aresolve_sandbox_spec_snapshot",
    "aresolve_snapshot",
    "build_base_snapshot_image",
    "fallback_to_declarative_image",
    "resolve_default_snapshot",
]
