"""Daytona-backed implementation of the Session Workspace port."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any, cast

from fleet_rlm.daytona.workspace_agent import WorkspaceAgentStorageError, run_workspace_agent
from fleet_rlm.files.workspace_models import WorkspaceEntry, WorkspaceListResult
from fleet_rlm.files.workspace_validation import normalize_workspace_path


class WorkspaceStorageError(OSError):
    """Mounted-volume mutation failure that is not a client path error."""

    code = "unsupported_storage"
    public_message = "Session Workspace storage does not support this mutation"


def _entry_from_payload(raw: Mapping[str, object]) -> WorkspaceEntry:
    byte_size = raw.get("byte_size")
    modified_at = raw.get("modified_at")
    parsed_byte_size: int | None
    if byte_size is None:
        parsed_byte_size = None
    elif isinstance(byte_size, int):
        parsed_byte_size = byte_size
    else:
        parsed_byte_size = int(str(byte_size))
    return WorkspaceEntry(
        path=str(raw["path"]),
        kind="directory" if raw.get("kind") == "directory" else "file",
        byte_size=parsed_byte_size,
        modified_at=str(modified_at) if modified_at else None,
    )


def _is_relative_to(path: PurePosixPath, root: PurePosixPath) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class DaytonaSessionWorkspaceFS:
    """Bind safe Session-relative text operations to one mounted Sandbox."""

    def __init__(
        self,
        sandbox: Any,
        *,
        volume_root: str,
        root: str,
        max_file_bytes: int,
    ) -> None:
        if max_file_bytes < 1:
            raise ValueError("workspace file bound must be positive")
        volume_path = PurePosixPath(volume_root)
        root_path = PurePosixPath(root)
        if (
            not volume_path.is_absolute()
            or not root_path.is_absolute()
            or ".." in volume_path.parts
            or ".." in root_path.parts
        ):
            raise ValueError("workspace root must be under trusted volume")
        try:
            root_path.relative_to(volume_path)
        except ValueError as exc:
            raise ValueError("workspace root must be under trusted volume") from exc
        if root_path == volume_path:
            raise ValueError("workspace root must be distinct from trusted volume")
        for reserved in ("attachments", "artifacts"):
            reserved_path = volume_path / reserved
            if root_path == reserved_path or _is_relative_to(root_path, reserved_path):
                raise ValueError("workspace root must not use attachment or artifact storage")
        self._sandbox = sandbox
        self._volume_root = str(volume_path)
        self._root = str(root_path)
        self._max_file_bytes = int(max_file_bytes)
        self._last_warnings: tuple[dict[str, object], ...] = ()

    @property
    def root(self) -> str:
        return self._root

    @property
    def last_warnings(self) -> tuple[dict[str, object], ...]:
        return self._last_warnings

    def list_entries(self, path: str, *, limit: int = 100) -> WorkspaceListResult:
        relative = normalize_workspace_path(path, allow_root=True)
        if limit < 1 or limit > 100:
            raise ValueError("workspace list limit must be between 1 and 100")
        payload = self._atomic_run(
            operation="list",
            relative=relative,
            allow_missing=relative == ".",
            max_bytes=0,
            limit=limit,
            overwrite=False,
            content_b64="",
        )
        raw_entries = payload.get("entries")
        entries: list[WorkspaceEntry] = []
        if isinstance(raw_entries, list):
            for item in raw_entries:
                if isinstance(item, dict):
                    entries.append(_entry_from_payload(cast(Mapping[str, object], item)))
        return WorkspaceListResult(entries=tuple(entries), truncated=bool(payload.get("truncated")))

    def stat(self, path: str) -> WorkspaceEntry | None:
        relative = normalize_workspace_path(path, allow_root=True)
        payload = self._atomic_run(
            operation="stat",
            relative=relative,
            allow_missing=True,
            max_bytes=0,
            limit=0,
            overwrite=False,
            content_b64="",
        )
        if payload.get("entry") is None:
            if relative == ".":
                return WorkspaceEntry(".", "directory", None, None)
            return None
        entry = payload["entry"]
        if not isinstance(entry, dict):
            raise RuntimeError("workspace stat returned invalid entry")
        return _entry_from_payload(cast(Mapping[str, object], entry))

    def read_text(self, path: str, *, max_bytes: int) -> str:
        relative = normalize_workspace_path(path)
        bound = min(self._max_file_bytes, int(max_bytes))
        if bound < 1:
            raise ValueError("workspace read bound must be positive")
        payload = self._atomic_run(
            operation="read",
            relative=relative,
            allow_missing=False,
            max_bytes=bound,
            limit=0,
            overwrite=False,
            content_b64="",
        )
        content = payload.get("content")
        if not isinstance(content, str):
            raise RuntimeError("workspace read returned invalid content")
        return content

    def write_text(self, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry:
        relative = normalize_workspace_path(path)
        if not isinstance(content, str):
            raise ValueError("workspace content must be text")
        data = content.encode("utf-8")
        if len(data) > self._max_file_bytes:
            raise ValueError("workspace file exceeds maximum size")
        payload = self._atomic_run(
            operation="write",
            relative=relative,
            allow_missing=True,
            max_bytes=self._max_file_bytes,
            limit=0,
            overwrite=overwrite,
            content_b64=base64.b64encode(data).decode("ascii"),
        )
        raw_warnings = payload.get("warnings")
        self._last_warnings = (
            tuple(cast(dict[str, object], item) for item in raw_warnings if isinstance(item, dict))
            if isinstance(raw_warnings, list)
            else ()
        )
        entry = payload.get("entry")
        if not isinstance(entry, dict):
            raise RuntimeError("workspace write returned invalid entry")
        return _entry_from_payload(cast(Mapping[str, object], entry))

    def _atomic_run(
        self,
        *,
        operation: str,
        relative: str,
        allow_missing: bool,
        max_bytes: int,
        limit: int,
        overwrite: bool,
        content_b64: str,
    ) -> dict[str, object]:
        try:
            return run_workspace_agent(
                self._sandbox,
                volume_root=self._volume_root,
                root=self._root,
                operation=operation,
                relative=relative,
                allow_missing=allow_missing,
                max_bytes=max_bytes,
                limit=limit,
                overwrite=overwrite,
                content_b64=content_b64,
            )
        except WorkspaceAgentStorageError as exc:
            raise WorkspaceStorageError(*exc.args) from exc
