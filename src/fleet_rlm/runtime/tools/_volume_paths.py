"""Shared volume path helpers for runtime tools."""

from __future__ import annotations

import os
from pathlib import Path


def volume_root(volume_mount_path: str | None = None) -> Path | None:
    root = (
        volume_mount_path
        or os.environ.get("FLEET_RLM_VOLUME_MOUNT_PATH")
        or os.environ.get("DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH")
    )
    if not root:
        return None
    return Path(root)


def knowledge_root(volume_mount_path: str | None = None) -> Path | None:
    root = volume_root(volume_mount_path)
    if root is None:
        return None
    return root / "knowledge"


def skills_root(volume_mount_path: str | None = None) -> Path | None:
    root = volume_root(volume_mount_path)
    if root is None:
        return None
    return root / "skills"


__all__ = ["volume_root", "knowledge_root", "skills_root"]
