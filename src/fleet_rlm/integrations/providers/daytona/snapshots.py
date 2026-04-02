"""Programmatic Daytona snapshot management for fleet-rlm.

Provides helpers to create, list, and resolve pre-baked snapshots so that
sandbox cold-start time is minimised.  Snapshots bundle the base image and
common Python dependencies so that ``client.create()`` can skip the image
build / dependency install phase entirely.
"""

from __future__ import annotations

import logging
from typing import Any

from .runtime_helpers import _await_if_needed, _build_daytona_client
from .config import ResolvedDaytonaConfig, resolve_daytona_config

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
        return [
            {
                "name": s.name,
                "id": s.id,
                "state": str(getattr(s, "state", "unknown")),
                "image_name": getattr(s, "image_name", None),
            }
            for s in (result.items if hasattr(result, "items") else result)
        ]
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
        snap = await _await_if_needed(client.snapshot.get(name))
        return {
            "name": snap.name,
            "id": snap.id,
            "state": str(getattr(snap, "state", "unknown")),
            "image_name": getattr(snap, "image_name", None),
        }
    except Exception:
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
    from daytona import Image as DaytonaImage
    from daytona.common.snapshot import CreateSnapshotParams

    pkgs = packages if packages is not None else DEFAULT_SNAPSHOT_PACKAGES

    image = DaytonaImage.debian_slim("3.12")
    for pkg in pkgs:
        image = image.pip_install(pkg)

    params = CreateSnapshotParams(name=name, image=image)
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snap = await _await_if_needed(
            client.snapshot.create(params, on_logs=on_logs, timeout=0)
        )
        logger.info("Snapshot '%s' created (id=%s)", snap.name, snap.id)
        return {
            "name": snap.name,
            "id": snap.id,
            "state": str(getattr(snap, "state", "unknown")),
            "image_name": getattr(snap, "image_name", None),
        }
    finally:
        await _await_if_needed(client.close())


async def aresolve_snapshot(
    preferred_name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> str | None:
    """Return the snapshot name if it exists and is ``ACTIVE``, else ``None``."""
    info = await aget_snapshot(preferred_name, config=config)
    if info and info.get("state", "").upper() in ("ACTIVE", "SnapshotState.ACTIVE"):
        return info["name"]
    return None
