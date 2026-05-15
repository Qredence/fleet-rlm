"""Snapshot-focused Daytona helpers.

This module owns both snapshot inventory/build helpers and the runtime-facing
fallback routines used when a named default snapshot is not yet active.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import shlex
from typing import Any

from .async_compat import _await_if_needed, _run_async_compat
from .config import ResolvedDaytonaConfig, resolve_daytona_config
from .config import (
    build_daytona_client as _build_daytona_client,
)
from .config import (
    daytona_import_error as _daytona_import_error,
)
from .sandbox_lifecycle import _experimental_call
from .sandbox_spec import SandboxSpec

logger = logging.getLogger(__name__)

# Default pip packages every fleet-rlm sandbox needs.
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


async def alist_snapshots(
    config: ResolvedDaytonaConfig | None = None,
) -> list[dict[str, Any]]:
    """Return a lightweight list of available snapshots.

    Each dict contains ``name``, ``id``, ``state``, and ``image_name``.
    """
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        result = await _await_if_needed(client.snapshot.list())
        items = result.items if hasattr(result, "items") else result
        return [_snapshot_summary(snapshot) for snapshot in items]
    finally:
        await _await_if_needed(client.close())


async def aget_snapshot(
    name: str,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> dict[str, Any] | None:
    """Look up a snapshot by *name*, returning a summary dict or ``None``."""
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snapshot = await _await_if_needed(client.snapshot.get(name))
        return _snapshot_summary(snapshot)
    except Exception:
        logger.debug("snapshot_lookup_failed", extra={"name": name}, exc_info=True)
        return None
    finally:
        await _await_if_needed(client.close())


async def acreate_snapshot(
    name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    packages: list[str] | None = None,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Create a new Daytona snapshot with pre-installed packages.

    Returns a summary dict with the snapshot ``name``, ``id``, and ``state``.
    """
    try:
        from daytona.common.snapshot import CreateSnapshotParams
    except ImportError as exc:  # pragma: no cover - environment specific
        raise _daytona_import_error(exc) from exc

    image = build_base_snapshot_image(base_image=base_image, packages=packages)
    params = CreateSnapshotParams(name=name, image=image)
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snapshot = await _await_if_needed(client.snapshot.create(params, on_logs=on_logs, timeout=0))
        logger.info("Snapshot '%s' created (id=%s)", snapshot.name, snapshot.id)
        return _snapshot_summary(snapshot)
    finally:
        await _await_if_needed(client.close())


async def adelete_snapshot(
    name: str,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> None:
    """Delete a Daytona snapshot by name or id."""
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        await _await_if_needed(client.snapshot.delete(name))
    finally:
        await _await_if_needed(client.close())


async def abootstrap_snapshot(
    name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    refresh: bool = False,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Ensure the reusable Fleet Daytona base snapshot exists.

    Existing snapshots are left untouched unless ``refresh`` is true. The result
    includes ``created`` so CLI callers can distinguish reuse from creation.
    """
    cfg = config or resolve_daytona_config()
    existing = await aget_snapshot(name, config=cfg)
    if existing is not None and not refresh:
        return {**existing, "created": False, "refreshed": False}
    if existing is not None:
        await adelete_snapshot(str(existing.get("id") or name), config=cfg)

    created = await acreate_snapshot(
        name=name,
        base_image=base_image,
        config=cfg,
        on_logs=on_logs,
    )
    return {**created, "created": True, "refreshed": existing is not None}


async def aresolve_snapshot(
    preferred_name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> str | None:
    """Return the snapshot name if it exists and is ``ACTIVE``, else ``None``."""
    info = await aget_snapshot(preferred_name, config=config)
    state = str(info.get("state", "")).upper() if info else ""
    if info and state in ("ACTIVE", "SNAPSHOTSTATE.ACTIVE"):
        return info["name"]
    return None


async def aresolve_sandbox_spec_snapshot(
    spec: SandboxSpec,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> SandboxSpec:
    """Resolve a snapshot-backed spec, falling back to the shared declarative image when needed."""
    if not spec.snapshot or spec.uses_declarative_image:
        return spec
    active_snapshot = await aresolve_snapshot(spec.snapshot, config=config)
    if active_snapshot is not None:
        return spec
    logger.info(
        "Snapshot '%s' not active; falling back to declarative image",
        spec.snapshot,
    )
    return fallback_to_declarative_image(spec)


def resolve_default_snapshot(*, image: Any, snapshot: str | None) -> str | None:
    """Choose the runtime default snapshot when neither image nor snapshot is set."""
    if snapshot or image:
        return snapshot
    return DEFAULT_SNAPSHOT_NAME


def fallback_to_declarative_image(spec: SandboxSpec) -> SandboxSpec:
    """Replace a snapshot-based spec with a declarative image build."""
    image = build_base_snapshot_image()
    return dataclasses.replace(spec, image=image, snapshot=None)


def bootstrap_snapshot(
    name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    refresh: bool = False,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Synchronously ensure the reusable Fleet Daytona base snapshot exists."""
    return _run_async_compat(
        abootstrap_snapshot,
        name=name,
        base_image=base_image,
        refresh=refresh,
        config=config,
        on_logs=on_logs,
    )


async def acreate_sandbox_snapshot(
    session: Any,
    *,
    name: str,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Create a snapshot from the current state of a sandbox session."""
    await _experimental_call(
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


def create_sandbox_snapshot(
    session: Any,
    *,
    name: str,
    timeout: float = 60.0,
) -> dict[str, Any]:
    return _run_async_compat(
        acreate_sandbox_snapshot,
        session,
        name=name,
        timeout=timeout,
    )


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
    "bootstrap_snapshot",
    "build_base_snapshot_image",
    "create_sandbox_snapshot",
    "fallback_to_declarative_image",
    "resolve_default_snapshot",
]
