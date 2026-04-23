"""Runtime service helpers for Daytona sandbox operations."""

from __future__ import annotations

from typing import Any

from fleet_rlm.integrations.daytona import config as _daytona_config
from fleet_rlm.integrations.daytona.async_compat import _await_if_needed
from fleet_rlm.integrations.daytona import runtime as _daytona_runtime

from ..schemas.core import SandboxListItem, SandboxListResponse


async def load_sandbox_list(
    page: int = 1,
    limit: int = 100,
) -> SandboxListResponse:
    """List active Daytona sandboxes.

    Wraps the Daytona SDK ``client.list()`` method and normalizes sandbox
    attributes into the canonical API response shape.
    """
    client: Any | None = None
    try:
        config = _daytona_config.resolve_daytona_config()
        client = _daytona_runtime._build_daytona_client(config)

        result = await _await_if_needed(
            client.list(page=page, limit=limit),
        )
        items: list[SandboxListItem] = []
        raw_items = getattr(result, "items", result) if result else []
        for sandbox in raw_items:
            # Extract volume name from volumes or labels
            volume_name = None
            volumes = getattr(sandbox, "volumes", None)
            if volumes and isinstance(volumes, list):
                first_volume = volumes[0]
                # volumes may be strings or VolumeMount objects
                if isinstance(first_volume, str):
                    volume_name = first_volume or None
                else:
                    volume_name = getattr(first_volume, "volume_id", None)
                    if volume_name is None:
                        volume_name = getattr(first_volume, "name", None)
                    if volume_name is None:
                        volume_name = getattr(first_volume, "id", None)

            # Parse state
            state = getattr(sandbox, "state", None)
            state_value = str(getattr(state, "value", state) or "unknown")

            # Parse labels
            labels = getattr(sandbox, "labels", None) or {}
            if not isinstance(labels, dict):
                labels = {}

            items.append(
                SandboxListItem(
                    id=str(getattr(sandbox, "id", "")),
                    name=str(getattr(sandbox, "name", "")),
                    state=state_value,
                    created_at=getattr(sandbox, "created_at", None),
                    volume_name=volume_name,
                    labels=labels,
                    cpu=getattr(sandbox, "cpu", None),
                    memory=getattr(sandbox, "memory", None),
                    disk=getattr(sandbox, "disk", None),
                )
            )

        return SandboxListResponse(
            items=items,
            total=getattr(result, "total", len(items)),
            page=getattr(result, "page", page),
            total_pages=getattr(result, "total_pages", 1),
        )
    finally:
        if client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                await _await_if_needed(close())
