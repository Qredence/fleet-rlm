"""Runtime service helpers for Daytona sandbox operations."""

from __future__ import annotations

import math
from typing import Any

from fastapi import HTTPException

from fleet_rlm.integrations.daytona import config as _daytona_config
from fleet_rlm.integrations.daytona.async_compat import _await_if_needed
from fleet_rlm.integrations.daytona import runtime as _daytona_runtime
from fleet_rlm.utils.sandbox_ownership import (
    SANDBOX_OWNER_LABEL,
    sandbox_has_owner_label,
    sandbox_owner_matches,
)

from ..schemas.core import SandboxDetailResponse, SandboxListItem, SandboxListResponse


async def load_sandbox_list(
    page: int = 1,
    limit: int = 100,
    *,
    owner_labels: dict[str, str] | None = None,
    allow_unlabeled_legacy: bool = False,
) -> SandboxListResponse:
    """List active Daytona sandboxes.

    Wraps the Daytona SDK ``client.list()`` method and normalizes sandbox
    attributes into the canonical API response shape.
    """
    client: Any | None = None
    try:
        config = _daytona_config.resolve_daytona_config()
        client = _daytona_runtime._build_daytona_client(config)

        labels_filter = _list_labels_filter(
            owner_labels=owner_labels,
            allow_unlabeled_legacy=allow_unlabeled_legacy,
        )
        result = await _list_sandboxes(
            client,
            page=page,
            limit=limit,
            labels_filter=labels_filter,
        )
        items: list[SandboxListItem] = []
        raw_items = getattr(result, "items", result) if result else []
        for sandbox in raw_items:
            labels = _sandbox_labels(sandbox)
            if not _sandbox_is_accessible(
                labels,
                owner_labels=owner_labels,
                allow_unlabeled_legacy=allow_unlabeled_legacy,
            ):
                continue
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
            total=len(items),
            page=getattr(result, "page", page),
            total_pages=max(1, math.ceil(len(items) / max(1, limit))),
        )
    finally:
        if client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                await _await_if_needed(close())


async def load_sandbox_detail(
    sandbox_id: str,
    *,
    owner_labels: dict[str, str] | None = None,
    allow_unlabeled_legacy: bool = False,
) -> SandboxDetailResponse:
    """Get detailed information for a single Daytona sandbox by ID.

    Wraps the Daytona SDK ``client.get()`` method and normalizes sandbox
    attributes into the canonical API response shape.
    """
    client: Any | None = None
    try:
        config = _daytona_config.resolve_daytona_config()
        client = _daytona_runtime._build_daytona_client(config)

        sandbox = await _await_if_needed(client.get(sandbox_id))
        _raise_if_sandbox_inaccessible(
            sandbox,
            owner_labels=owner_labels,
            allow_unlabeled_legacy=allow_unlabeled_legacy,
        )

        # Extract volume name from volumes
        volume_name = None
        volume_mounts: list[dict[str, Any]] = []
        volumes = getattr(sandbox, "volumes", None)
        if volumes and isinstance(volumes, list):
            for vol in volumes:
                if isinstance(vol, str):
                    volume_mounts.append({"id": vol, "name": vol})
                    if volume_name is None:
                        volume_name = vol or None
                else:
                    vol_id = getattr(vol, "volume_id", None)
                    vol_name = getattr(vol, "name", None)
                    vol_path = getattr(vol, "mount_path", None)
                    if vol_id is None:
                        vol_id = getattr(vol, "id", None)
                    if vol_name is None:
                        vol_name = vol_id
                    mount = {
                        "id": vol_id,
                        "name": vol_name,
                        "mount_path": vol_path,
                    }
                    mount = {k: v for k, v in mount.items() if v is not None}
                    volume_mounts.append(mount)
                    if volume_name is None:
                        volume_name = vol_id or vol_name

        # Parse state
        state = getattr(sandbox, "state", None)
        state_value = str(getattr(state, "value", state) or "unknown")

        # Parse labels
        labels = _sandbox_labels(sandbox)

        # Parse env vars
        env_vars = getattr(sandbox, "env", None) or {}
        if not isinstance(env_vars, dict):
            env_vars = {}

        # Parse image info
        image = getattr(sandbox, "image", None)
        image_name = None
        if image is not None:
            image_name = getattr(image, "name", None)
            if image_name is None:
                image_name = getattr(image, "image", None)
            if image_name is None:
                image_name = str(image) if image else None

        # Parse resources
        resources = getattr(sandbox, "resources", None)
        cpu = getattr(sandbox, "cpu", None)
        memory = getattr(sandbox, "memory", None)
        disk = getattr(sandbox, "disk", None)
        if resources is not None:
            if cpu is None:
                cpu = getattr(resources, "cpu", None)
            if memory is None:
                memory = getattr(resources, "memory", None)
            if disk is None:
                disk = getattr(resources, "disk", None)

        return SandboxDetailResponse(
            id=str(getattr(sandbox, "id", "")),
            name=str(getattr(sandbox, "name", "")),
            state=state_value,
            created_at=getattr(sandbox, "created_at", None),
            volume_name=volume_name,
            labels=labels,
            cpu=cpu,
            memory=memory,
            disk=disk,
            env_vars=env_vars,
            image=image_name,
            snapshot=getattr(sandbox, "snapshot", None),
            language=getattr(sandbox, "language", None),
            auto_stop_interval=getattr(sandbox, "auto_stop_interval", None),
            auto_archive_interval=getattr(sandbox, "auto_archive_interval", None),
            auto_delete_interval=getattr(sandbox, "auto_delete_interval", None),
            ephemeral=getattr(sandbox, "ephemeral", None),
            network_block_all=getattr(sandbox, "network_block_all", None),
            network_allow_list=getattr(sandbox, "network_allow_list", None),
            volumes=volume_mounts,
        )
    finally:
        if client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                await _await_if_needed(close())


async def delete_sandbox(
    sandbox_id: str,
    *,
    owner_labels: dict[str, str] | None = None,
    allow_unlabeled_legacy: bool = False,
) -> None:
    """Stop and delete a Daytona sandbox by ID.

    Wraps ``DaytonaSandboxSession.adelete()`` to perform a graceful stop
    followed by deletion.
    """
    client: Any | None = None
    try:
        config = _daytona_config.resolve_daytona_config()
        client = _daytona_runtime._build_daytona_client(config)

        sandbox = await _await_if_needed(client.get(sandbox_id))
        _raise_if_sandbox_inaccessible(
            sandbox,
            owner_labels=owner_labels,
            allow_unlabeled_legacy=allow_unlabeled_legacy,
        )
        session = _daytona_runtime.DaytonaSandboxSession(
            sandbox=sandbox,
            repo_url=None,
            ref=None,
            volume_name=None,
            workspace_path="/",
        )
        await session.adelete()
    finally:
        if client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                await _await_if_needed(close())


async def archive_sandbox(
    sandbox_id: str,
    *,
    owner_labels: dict[str, str] | None = None,
    allow_unlabeled_legacy: bool = False,
) -> None:
    """Archive a Daytona sandbox by ID to cold storage.

    Wraps ``DaytonaSandboxSession.aarchive()`` to move the sandbox to
    cold storage for later recovery.
    """
    client: Any | None = None
    try:
        config = _daytona_config.resolve_daytona_config()
        client = _daytona_runtime._build_daytona_client(config)

        sandbox = await _await_if_needed(client.get(sandbox_id))
        _raise_if_sandbox_inaccessible(
            sandbox,
            owner_labels=owner_labels,
            allow_unlabeled_legacy=allow_unlabeled_legacy,
        )
        session = _daytona_runtime.DaytonaSandboxSession(
            sandbox=sandbox,
            repo_url=None,
            ref=None,
            volume_name=None,
            workspace_path="/",
        )
        await session.aarchive()
    finally:
        if client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                await _await_if_needed(close())


def _list_labels_filter(
    *,
    owner_labels: dict[str, str] | None,
    allow_unlabeled_legacy: bool,
) -> dict[str, str] | None:
    if allow_unlabeled_legacy or not owner_labels:
        return None
    owner_value = owner_labels.get(SANDBOX_OWNER_LABEL)
    return {SANDBOX_OWNER_LABEL: owner_value} if owner_value else None


async def _list_sandboxes(
    client: Any,
    *,
    page: int,
    limit: int,
    labels_filter: dict[str, str] | None,
) -> Any:
    if labels_filter:
        try:
            return await _await_if_needed(
                client.list(labels=labels_filter, page=page, limit=limit)
            )
        except TypeError:
            pass
    return await _await_if_needed(client.list(page=page, limit=limit))


def _sandbox_labels(sandbox: Any) -> dict[str, str]:
    labels = getattr(sandbox, "labels", None) or {}
    if not isinstance(labels, dict):
        return {}
    return {str(key): str(value) for key, value in labels.items()}


def _sandbox_is_accessible(
    labels: dict[str, str],
    *,
    owner_labels: dict[str, str] | None,
    allow_unlabeled_legacy: bool,
) -> bool:
    owner_label = (owner_labels or {}).get(SANDBOX_OWNER_LABEL)
    if not owner_label:
        return bool(allow_unlabeled_legacy and not sandbox_has_owner_label(labels))
    if sandbox_owner_matches(labels, owner_label=owner_label):
        return True
    return bool(allow_unlabeled_legacy and not sandbox_has_owner_label(labels))


def _raise_if_sandbox_inaccessible(
    sandbox: Any,
    *,
    owner_labels: dict[str, str] | None,
    allow_unlabeled_legacy: bool,
) -> None:
    if _sandbox_is_accessible(
        _sandbox_labels(sandbox),
        owner_labels=owner_labels,
        allow_unlabeled_legacy=allow_unlabeled_legacy,
    ):
        return
    raise HTTPException(status_code=404, detail="Sandbox not found or inaccessible.")
