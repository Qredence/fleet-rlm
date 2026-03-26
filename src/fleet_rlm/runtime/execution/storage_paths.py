"""Canonical persistent-storage naming helpers for runtime backends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .interpreter_protocol import RLMInterpreterProtocol


@dataclass(frozen=True, slots=True)
class RuntimeStorageRoots:
    """Canonical persistent storage roots for one interpreter runtime."""

    mounted_root: str
    memory_root: str
    workspace_root: str

    @property
    def allowed_root(self) -> str:
        return self.mounted_root


def runtime_storage_roots(interpreter: RLMInterpreterProtocol) -> RuntimeStorageRoots:
    """Return canonical mounted/memory/workspace roots for an interpreter."""
    mounted_root = str(
        getattr(interpreter, "volume_mount_path", "/data") or "/data"
    ).rstrip("/")
    if mounted_root.endswith("/memory"):
        memory_root = mounted_root
        workspace_root = str(PurePosixPath(mounted_root) / "workspace")
        return RuntimeStorageRoots(
            mounted_root=mounted_root,
            memory_root=memory_root,
            workspace_root=workspace_root,
        )

    normalized_root = mounted_root or "/data"
    return RuntimeStorageRoots(
        mounted_root=normalized_root,
        memory_root=str(PurePosixPath(normalized_root) / "memory"),
        workspace_root=str(PurePosixPath(normalized_root) / "workspace"),
    )
