"""Canonical persistent-storage naming helpers for runtime backends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .interpreter_protocol import RLMInterpreterProtocol

_CURRENT_DAYTONA_MOUNT_PARENT = PurePosixPath("/home/daytona")


@dataclass(frozen=True, slots=True)
class RuntimeStorageRoots:
    """Canonical persistent storage roots for one interpreter runtime."""

    mounted_root: str
    memory_root: str
    artifacts_root: str
    buffers_root: str
    meta_root: str
    memories_root: str
    knowledge_root: str
    skills_root: str
    sessions_root: str
    logs_root: str
    uploads_root: str

    @property
    def allowed_root(self) -> str:
        return self.mounted_root


def mounted_storage_roots(mounted_root: str) -> RuntimeStorageRoots:
    """Return canonical durable roots for a mounted volume path."""
    normalized_root = str(mounted_root or "/data").rstrip("/") or "/data"
    base = PurePosixPath(normalized_root)
    if base.name == "memory" and base.parent != _CURRENT_DAYTONA_MOUNT_PARENT:
        base = base.parent
        normalized_root = str(base)
    return RuntimeStorageRoots(
        mounted_root=normalized_root,
        memory_root=str(base / "memory"),
        artifacts_root=str(base / "artifacts"),
        buffers_root=str(base / "buffers"),
        meta_root=str(base / "meta"),
        memories_root=str(base / "memories"),
        knowledge_root=str(base / "knowledge"),
        skills_root=str(base / "skills"),
        sessions_root=str(base / "sessions"),
        logs_root=str(base / "logs"),
        uploads_root=str(base / "uploads"),
    )


def runtime_storage_roots(interpreter: RLMInterpreterProtocol) -> RuntimeStorageRoots:
    """Return canonical mounted durable roots for an interpreter."""
    mounted_root = str(getattr(interpreter, "volume_mount_path", "/data") or "/data").rstrip("/") or "/data"
    return mounted_storage_roots(mounted_root)
