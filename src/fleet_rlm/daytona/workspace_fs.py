"""Daytona-backed implementation of the Session Workspace port."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any, cast

from fleet_rlm.files.workspace_models import WorkspaceEntry, WorkspaceListResult
from fleet_rlm.files.workspace_validation import normalize_workspace_path


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
        self._sandbox = sandbox
        self._volume_root = str(volume_path)
        self._root = str(root_path)
        self._max_file_bytes = int(max_file_bytes)

    @property
    def root(self) -> str:
        return self._root

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
        entry = payload.get("entry")
        if not isinstance(entry, dict):
            raise RuntimeError("workspace write returned invalid entry")
        return _entry_from_payload(cast(Mapping[str, object], entry))

    def _absolute(self, relative: str) -> str:
        return self._root if relative == "." else str(PurePosixPath(self._root) / relative)

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
        target = self._absolute(relative)
        code = "\n".join((
            "import base64, heapq, json, os, stat, time",
            f"volume_root = {self._volume_root!r}",
            f"root = {self._root!r}",
            f"target = {target!r}",
            f"relative = {relative!r}",
            f"allow_missing = {allow_missing!r}",
            f"operation = {operation!r}",
            f"max_bytes = {int(max_bytes)!r}",
            f"limit = {int(limit)!r}",
            f"overwrite = {overwrite!r}",
            f"content_b64 = {content_b64!r}",
            "def respond(payload):",
            "    print(json.dumps(payload))",
            "    raise SystemExit(0)",
            "def fail(error, **extra):",
            "    respond({'ok': False, 'error': error, **extra})",
            "def guard_path(path, *, allow_missing_path):",
            "    volume_real = os.path.realpath(volume_root)",
            "    target_real = os.path.realpath(path)",
            "    if os.path.commonpath([volume_root, root]) != volume_root:",
            "        fail('unsafe')",
            "    if os.path.commonpath([root, path]) != root:",
            "        fail('unsafe')",
            "    if os.path.commonpath([volume_real, target_real]) != volume_real:",
            "        fail('unsafe')",
            "    current = volume_root",
            "    relative_parts = os.path.relpath(path, volume_root).split(os.sep)",
            "    for part in ([] if relative_parts == ['.'] else relative_parts):",
            "        current = os.path.join(current, part)",
            "        if not os.path.lexists(current):",
            "            if allow_missing_path:",
            "                return",
            "            fail('not_found')",
            "        if stat.S_ISLNK(os.lstat(current).st_mode):",
            "            fail('unsafe')",
            "def ensure_parents(path):",
            "    parent = os.path.dirname(path)",
            "    if parent and parent != path:",
            "        os.makedirs(parent, mode=0o700, exist_ok=True)",
            "        guard_path(parent, allow_missing_path=False)",
            "def entry_for(path):",
            "    info = os.stat(path, follow_symlinks=False)",
            "    modified_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(info.st_mtime))",
            "    if stat.S_ISDIR(info.st_mode):",
            "        return {'path': relative if path == target else os.path.relpath(path, root), 'kind': 'directory', 'byte_size': None, 'modified_at': modified_at}",
            "    return {'path': relative, 'kind': 'file', 'byte_size': info.st_size, 'modified_at': modified_at}",
            "try:",
            "    guard_path(target, allow_missing_path=allow_missing)",
            "    if operation == 'list':",
            "        if not os.path.lexists(target):",
            "            if relative == '.':",
            "                respond({'ok': True, 'entries': [], 'truncated': False})",
            "            fail('not_found')",
            "        if stat.S_ISLNK(os.lstat(target).st_mode):",
            "            fail('unsafe')",
            "        if not stat.S_ISDIR(os.stat(target, follow_symlinks=False).st_mode):",
            "            fail('not_directory')",
            "        entries = []",
            "        with os.scandir(target) as scanner:",
            "            candidates = heapq.nsmallest(limit + 1, scanner, key=lambda item: item.name)",
            "        truncated = len(candidates) > limit",
            "        for item in candidates[:limit]:",
            "            child_relative = item.name if relative == '.' else f'{relative}/{item.name}'",
            "            child_stat = item.stat(follow_symlinks=False)",
            "            modified_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(child_stat.st_mtime))",
            "            if stat.S_ISDIR(child_stat.st_mode):",
            "                entries.append({'path': child_relative, 'kind': 'directory', 'byte_size': None, 'modified_at': modified_at})",
            "            else:",
            "                entries.append({'path': child_relative, 'kind': 'file', 'byte_size': child_stat.st_size, 'modified_at': modified_at})",
            "        respond({'ok': True, 'entries': entries, 'truncated': truncated})",
            "    if operation == 'stat':",
            "        if not os.path.lexists(target):",
            "            respond({'ok': True, 'entry': None})",
            "        if stat.S_ISLNK(os.lstat(target).st_mode):",
            "            fail('unsafe')",
            "        respond({'ok': True, 'entry': entry_for(target)})",
            "    if operation == 'read':",
            "        if not os.path.lexists(target):",
            "            fail('not_found')",
            "        if stat.S_ISDIR(os.stat(target, follow_symlinks=False).st_mode):",
            "            fail('is_directory')",
            "        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)",
            "        fd = os.open(target, flags)",
            "        try:",
            "            data = os.read(fd, max_bytes + 1)",
            "        finally:",
            "            os.close(fd)",
            "        if len(data) > max_bytes:",
            "            fail('read_bound')",
            "        try:",
            "            content = data.decode('utf-8')",
            "        except UnicodeDecodeError:",
            "            fail('invalid_utf8')",
            "        respond({'ok': True, 'content': content})",
            "    if operation == 'write':",
            "        ensure_parents(target)",
            "        guard_path(target, allow_missing_path=True)",
            "        payload = base64.b64decode(content_b64.encode('ascii'))",
            "        if len(payload) > max_bytes:",
            "            fail('too_large')",
            "        if os.path.lexists(target):",
            "            if stat.S_ISDIR(os.stat(target, follow_symlinks=False).st_mode):",
            "                fail('is_directory')",
            "            if not overwrite:",
            "                fail('conflict')",
            "            flags = os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0)",
            "            fd = os.open(target, flags)",
            "            try:",
            "                os.ftruncate(fd, 0)",
            "                os.write(fd, payload)",
            "            finally:",
            "                os.close(fd)",
            "        else:",
            "            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)",
            "            fd = os.open(target, flags, 0o600)",
            "            try:",
            "                os.write(fd, payload)",
            "            finally:",
            "                os.close(fd)",
            "        respond({'ok': True, 'entry': entry_for(target)})",
            "    fail('unsupported')",
            "except FileNotFoundError:",
            "    fail('not_found')",
            "except FileExistsError:",
            "    fail('conflict')",
            "except IsADirectoryError:",
            "    fail('is_directory')",
            "except OSError:",
            "    fail('unsafe')",
        ))
        response = self._sandbox.process.code_run(code)
        if int(getattr(response, "exit_code", 1)) != 0:
            raise ValueError("workspace path is unsafe")
        try:
            payload = json.loads(str(getattr(response, "result", "")))
        except (TypeError, ValueError) as exc:
            raise ValueError("workspace path is unsafe") from exc
        if payload.get("ok") is not True:
            error = payload.get("error")
            if error == "not_found":
                raise FileNotFoundError(relative)
            if error == "conflict":
                raise FileExistsError(relative)
            if error == "is_directory":
                raise IsADirectoryError(relative)
            if error == "not_directory":
                raise NotADirectoryError(relative)
            if error == "read_bound":
                raise ValueError("workspace file exceeds read bound")
            if error == "too_large":
                raise ValueError("workspace file exceeds maximum size")
            if error == "invalid_utf8":
                raise ValueError("workspace file is not valid UTF-8")
            raise ValueError("workspace path is unsafe")
        return payload
