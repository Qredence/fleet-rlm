"""Daytona mounted-sandbox implementation of the Workspace Memory port."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fleet_rlm.daytona.workspace_agent import WorkspaceAgentStorageError, run_workspace_agent
from fleet_rlm.files.memory_models import (
    WORKSPACE_MEMORY_BYTE_BUDGET,
    WorkspaceMemoryAppendResult,
    WorkspaceMemoryReadResult,
    WorkspaceMemoryStoreFullError,
    WorkspaceMemoryStoreUnavailableError,
    validate_workspace_memory_content,
    validate_workspace_memory_record,
)
from fleet_rlm.files.volume_paths import VolumePaths, as_posix

_MEMORY_NAME = "MEMORIES.md"
_MAX_IDLE_MEMORY_FILE_PARENT_LOCKS = 128


class _MemoryRootLock:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.users = 0


_memory_file_parent_locks: OrderedDict[str, _MemoryRootLock] = OrderedDict()
_memory_file_parent_locks_guard = threading.Lock()


@contextmanager
def _memory_file_parent_append_lock(memory_file_parent: str) -> Iterator[None]:
    """Serialize local-process appends while retaining only a bounded idle lock cache."""
    with _memory_file_parent_locks_guard:
        entry = _memory_file_parent_locks.get(memory_file_parent)
        if entry is None:
            entry = _MemoryRootLock()
            _memory_file_parent_locks[memory_file_parent] = entry
        else:
            _memory_file_parent_locks.move_to_end(memory_file_parent)
        entry.users += 1
    try:
        with entry.lock:
            yield
    finally:
        with _memory_file_parent_locks_guard:
            entry.users -= 1
            _memory_file_parent_locks.move_to_end(memory_file_parent)
            while len(_memory_file_parent_locks) > _MAX_IDLE_MEMORY_FILE_PARENT_LOCKS:
                idle_parent = next(
                    (parent for parent, candidate in _memory_file_parent_locks.items() if not candidate.users),
                    None,
                )
                if idle_parent is None:
                    break
                _memory_file_parent_locks.pop(idle_parent)


class DaytonaWorkspaceMemoryStore:
    """Use the mounted Workspace Volume root's fixed ``MEMORIES.md`` file only."""

    def __init__(
        self,
        sandbox: Any,
        *,
        volume_paths: VolumePaths,
        max_upload_bytes: int,
    ) -> None:
        if max_upload_bytes < 1:
            raise ValueError("Workspace Memory capacity must be positive")
        expected_file = volume_paths.root / _MEMORY_NAME
        if volume_paths.memory_file != expected_file:
            raise ValueError("Workspace Memory must use the configured volume root")
        self._sandbox = sandbox
        self._volume_root = as_posix(volume_paths.root)
        self._memory_file_parent = as_posix(expected_file.parent)
        self._max_upload_bytes = int(max_upload_bytes)

    def read_tail(self, *, byte_budget: int) -> WorkspaceMemoryReadResult:
        if type(byte_budget) is not int or not 0 < byte_budget <= WORKSPACE_MEMORY_BYTE_BUDGET:
            raise WorkspaceMemoryStoreUnavailableError()
        try:
            payload = self._run(
                operation="tail_read",
                max_bytes=byte_budget,
                total_file_bytes=self._max_upload_bytes,
            )
            content = payload.get("content")
            truncated = payload.get("truncated")
            total_bytes = payload.get("total_bytes")
            bytes_returned = payload.get("bytes_returned")
            if (
                not isinstance(content, str)
                or type(truncated) is not bool
                or type(total_bytes) is not int
                or type(bytes_returned) is not int
            ):
                raise ValueError("invalid memory response")
            if (
                bytes_returned < 0
                or bytes_returned > byte_budget
                or total_bytes < bytes_returned
                or total_bytes > self._max_upload_bytes
                or bytes_returned != len(content.encode("utf-8"))
            ):
                raise ValueError("invalid memory response")
            validate_workspace_memory_content(content)
            return WorkspaceMemoryReadResult(
                content=content,
                truncated=truncated,
                bytes_returned=bytes_returned,
                byte_budget=byte_budget,
                total_bytes=total_bytes,
            )
        except Exception as exc:
            if isinstance(exc, WorkspaceMemoryStoreUnavailableError):
                raise
            raise WorkspaceMemoryStoreUnavailableError() from exc

    def append_record(self, record: str) -> WorkspaceMemoryAppendResult:
        if not isinstance(record, str):
            raise WorkspaceMemoryStoreUnavailableError()
        try:
            validate_workspace_memory_record(record)
            data = record.encode("utf-8")
        except (UnicodeError, ValueError) as exc:
            raise WorkspaceMemoryStoreUnavailableError() from exc
        if not data or len(data) > self._max_upload_bytes:
            raise WorkspaceMemoryStoreFullError()
        try:
            with _memory_file_parent_append_lock(self._memory_file_parent):
                payload = self._run(
                    operation="memory_append",
                    max_bytes=self._max_upload_bytes,
                    total_file_bytes=self._max_upload_bytes,
                    content=data,
                )
            entry = payload.get("entry")
            if not isinstance(entry, dict):
                raise ValueError("invalid memory response")
            total_bytes = entry.get("byte_size")
            if type(total_bytes) is not int or not len(data) <= total_bytes <= self._max_upload_bytes:
                raise ValueError("invalid memory response")
            return WorkspaceMemoryAppendResult(entry_bytes=len(data), total_bytes=total_bytes)
        except Exception as exc:
            if isinstance(exc, WorkspaceMemoryStoreFullError):
                raise
            if isinstance(exc, ValueError) and "maximum size" in str(exc):
                raise WorkspaceMemoryStoreFullError() from exc
            raise WorkspaceMemoryStoreUnavailableError() from exc

    def _run(
        self,
        *,
        operation: str,
        max_bytes: int,
        total_file_bytes: int,
        content: bytes = b"",
    ) -> dict[str, object]:
        import base64

        try:
            return run_workspace_agent(
                self._sandbox,
                volume_root=self._volume_root,
                root=self._memory_file_parent,
                operation=operation,
                relative=_MEMORY_NAME,
                allow_missing=True,
                max_bytes=max_bytes,
                total_file_bytes=total_file_bytes,
                limit=0,
                overwrite=False,
                content_b64=base64.b64encode(content).decode("ascii"),
            )
        except WorkspaceAgentStorageError as exc:
            raise WorkspaceMemoryStoreUnavailableError() from exc
