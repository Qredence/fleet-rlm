"""Snapshot-focused Daytona helpers.

This module owns both snapshot inventory/build helpers and the runtime-facing
fallback routines used when a named default snapshot is not yet active.
"""

from __future__ import annotations

import dataclasses
import logging
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
from .types import SandboxSpec

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


def _snapshot_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "name": snapshot.name,
        "id": snapshot.id,
        "state": str(getattr(snapshot, "state", "unknown")),
        "image_name": getattr(snapshot, "image_name", None),
    }


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
    base_image: str = "python:3.12-slim",
    packages: list[str] | None = None,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Create a new Daytona snapshot with pre-installed packages.

    Returns a summary dict with the snapshot ``name``, ``id``, and ``state``.
    """
    try:
        from daytona import Image as DaytonaImage
        from daytona.common.snapshot import CreateSnapshotParams
    except ImportError as exc:  # pragma: no cover - environment specific
        raise _daytona_import_error(exc) from exc

    packages_to_install = (
        packages if packages is not None else DEFAULT_SNAPSHOT_PACKAGES
    )

    image = DaytonaImage.base(base_image)
    image = image.run_commands("pip install uv")
    if packages_to_install:
        image = image.run_commands(
            f"uv pip install --system {' '.join(packages_to_install)}"
        )

    params = CreateSnapshotParams(name=name, image=image)
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snapshot = await _await_if_needed(
            client.snapshot.create(params, on_logs=on_logs, timeout=0)
        )
        logger.info("Snapshot '%s' created (id=%s)", snapshot.name, snapshot.id)
        return _snapshot_summary(snapshot)
    finally:
        await _await_if_needed(client.close())


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


def resolve_default_snapshot(*, image: Any, snapshot: str | None) -> str | None:
    """Choose the runtime default snapshot when neither image nor snapshot is set."""
    if snapshot or image:
        return snapshot
    return DEFAULT_SNAPSHOT_NAME


def fallback_to_declarative_image(spec: SandboxSpec) -> SandboxSpec:
    """Replace a snapshot-based spec with a declarative image build."""
    try:
        from daytona import Image as DaytonaImage
    except ImportError as exc:  # pragma: no cover - environment specific
        raise _daytona_import_error(exc) from exc

    image = DaytonaImage.base("python:3.12-slim")
    image = image.run_commands("pip install uv")
    if DEFAULT_SNAPSHOT_PACKAGES:
        image = image.run_commands(
            f"uv pip install --system {' '.join(DEFAULT_SNAPSHOT_PACKAGES)}"
        )
    return dataclasses.replace(spec, image=image, snapshot=None)


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
    "DEFAULT_SNAPSHOT_NAME",
    "DEFAULT_SNAPSHOT_PACKAGES",
    "acreate_sandbox_snapshot",
    "acreate_snapshot",
    "aget_snapshot",
    "alist_snapshots",
    "aresolve_snapshot",
    "create_sandbox_snapshot",
    "fallback_to_declarative_image",
    "resolve_default_snapshot",
]
