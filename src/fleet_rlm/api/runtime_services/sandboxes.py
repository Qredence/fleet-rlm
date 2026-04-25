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
        client = _build_daytona_client()

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
            items.append(_sandbox_list_item(sandbox=sandbox, labels=labels))

        return SandboxListResponse(
            items=items,
            total=len(items),
            page=getattr(result, "page", page),
            total_pages=max(1, math.ceil(len(items) / max(1, limit))),
        )
    finally:
        await _close_daytona_client(client)


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
        client = _build_daytona_client()
        sandbox = await _get_sandbox(client, sandbox_id)
        _raise_if_sandbox_inaccessible(
            sandbox,
            owner_labels=owner_labels,
            allow_unlabeled_legacy=allow_unlabeled_legacy,
        )
        return _sandbox_detail_response(sandbox)
    finally:
        await _close_daytona_client(client)


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
        client = _build_daytona_client()
        sandbox = await _get_sandbox(client, sandbox_id)
        _raise_if_sandbox_inaccessible(
            sandbox,
            owner_labels=owner_labels,
            allow_unlabeled_legacy=allow_unlabeled_legacy,
        )
        await _management_session(sandbox).adelete()
    finally:
        await _close_daytona_client(client)


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
        client = _build_daytona_client()
        sandbox = await _get_sandbox(client, sandbox_id)
        _raise_if_sandbox_inaccessible(
            sandbox,
            owner_labels=owner_labels,
            allow_unlabeled_legacy=allow_unlabeled_legacy,
        )
        await _management_session(sandbox).aarchive()
    finally:
        await _close_daytona_client(client)


def _build_daytona_client() -> Any:
    config = _daytona_config.resolve_daytona_config()
    return _daytona_runtime._build_daytona_client(config)


async def _close_daytona_client(client: Any | None) -> None:
    if client is None:
        return
    close = getattr(client, "close", None)
    if callable(close):
        await _await_if_needed(close())


async def _get_sandbox(client: Any, sandbox_id: str) -> Any:
    return await _await_if_needed(client.get(sandbox_id))


def _management_session(sandbox: Any) -> Any:
    return _daytona_runtime.DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url=None,
        ref=None,
        volume_name=None,
        workspace_path="/",
    )


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


def _sandbox_state(sandbox: Any) -> str:
    state = getattr(sandbox, "state", None)
    return str(getattr(state, "value", state) or "unknown")


def _volume_mount_id(volume: Any) -> Any:
    if isinstance(volume, str):
        return volume
    volume_id = getattr(volume, "volume_id", None)
    if volume_id is None:
        volume_id = getattr(volume, "id", None)
    return volume_id


def _volume_mount_name(volume: Any, volume_id: Any) -> Any:
    if isinstance(volume, str):
        return volume
    volume_name = getattr(volume, "name", None)
    return volume_id if volume_name is None else volume_name


def _sandbox_volume_name(sandbox: Any) -> Any:
    volumes = getattr(sandbox, "volumes", None)
    if not volumes or not isinstance(volumes, list):
        return None
    first_volume = volumes[0]
    if isinstance(first_volume, str):
        return first_volume or None
    volume_id = getattr(first_volume, "volume_id", None)
    if volume_id is None:
        volume_id = getattr(first_volume, "name", None)
    if volume_id is None:
        volume_id = getattr(first_volume, "id", None)
    return volume_id


def _sandbox_volume_mounts(sandbox: Any) -> tuple[Any, list[dict[str, Any]]]:
    volume_name = None
    volume_mounts: list[dict[str, Any]] = []
    volumes = getattr(sandbox, "volumes", None)
    if not volumes or not isinstance(volumes, list):
        return volume_name, volume_mounts

    for volume in volumes:
        volume_id = _volume_mount_id(volume)
        volume_mount_name = _volume_mount_name(volume, volume_id)
        if isinstance(volume, str):
            mount = {"id": volume, "name": volume}
        else:
            mount = {
                "id": volume_id,
                "name": volume_mount_name,
                "mount_path": getattr(volume, "mount_path", None),
            }
            mount = {key: value for key, value in mount.items() if value is not None}
        volume_mounts.append(mount)
        if volume_name is None:
            volume_name = volume_id or volume_mount_name
    return volume_name, volume_mounts


def _sandbox_env_vars(sandbox: Any) -> dict[str, Any]:
    env_vars = getattr(sandbox, "env", None) or {}
    return env_vars if isinstance(env_vars, dict) else {}


def _sandbox_image_name(sandbox: Any) -> str | None:
    image = getattr(sandbox, "image", None)
    if image is None:
        return None
    image_name = getattr(image, "name", None)
    if image_name is None:
        image_name = getattr(image, "image", None)
    if image_name is None:
        return str(image) if image else None
    return image_name


def _sandbox_resources(sandbox: Any) -> tuple[Any, Any, Any]:
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
    return cpu, memory, disk


def _sandbox_list_item(*, sandbox: Any, labels: dict[str, str]) -> SandboxListItem:
    return SandboxListItem(
        id=str(getattr(sandbox, "id", "")),
        name=str(getattr(sandbox, "name", "")),
        state=_sandbox_state(sandbox),
        created_at=getattr(sandbox, "created_at", None),
        volume_name=_sandbox_volume_name(sandbox),
        labels=labels,
        cpu=getattr(sandbox, "cpu", None),
        memory=getattr(sandbox, "memory", None),
        disk=getattr(sandbox, "disk", None),
    )


def _sandbox_detail_response(sandbox: Any) -> SandboxDetailResponse:
    volume_name, volume_mounts = _sandbox_volume_mounts(sandbox)
    cpu, memory, disk = _sandbox_resources(sandbox)
    return SandboxDetailResponse(
        id=str(getattr(sandbox, "id", "")),
        name=str(getattr(sandbox, "name", "")),
        state=_sandbox_state(sandbox),
        created_at=getattr(sandbox, "created_at", None),
        volume_name=volume_name,
        labels=_sandbox_labels(sandbox),
        cpu=cpu,
        memory=memory,
        disk=disk,
        env_vars=_sandbox_env_vars(sandbox),
        image=_sandbox_image_name(sandbox),
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
