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
        code = "\n".join(
            (
                "import base64, json, os, stat, time",
                f"volume_root = {self._volume_root!r}",
                f"root = {self._root!r}",
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
                "class UnsafePath(Exception):",
                "    pass",
                "def open_directory(path, *, dir_fd=None, create=False):",
                "    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_NOFOLLOW', 0)",
                "    try:",
                "        existing = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)",
                "        if stat.S_ISLNK(existing.st_mode):",
                "            raise UnsafePath(path)",
                "    except FileNotFoundError:",
                "        existing = None",
                "    if existing is not None:",
                "        return os.open(path, flags, dir_fd=dir_fd)",
                "    try:",
                "        return os.open(path, flags, dir_fd=dir_fd)",
                "    except FileNotFoundError:",
                "        if not create:",
                "            raise",
                "        try:",
                "            os.mkdir(path, 0o700, dir_fd=dir_fd)",
                "        except FileExistsError:",
                "            pass",
                "        return os.open(path, flags, dir_fd=dir_fd)",
                "def open_chain(*, create=False):",
                "    fds = []",
                "    try:",
                "        volume_fd = open_directory(volume_root)",
                "        fds.append(volume_fd)",
                "        root_parts = os.path.relpath(root, volume_root).split(os.sep)",
                "        if root_parts == ['.'] or root_parts == ['']:",
                "            root_parts = []",
                "        for part in root_parts:",
                "            next_fd = open_directory(part, dir_fd=fds[-1], create=create)",
                "            fds.append(next_fd)",
                "        root_fd = fds[-1]",
                "        return fds, root_fd",
                "    except BaseException:",
                "        for fd in reversed(fds):",
                "            try:",
                "                os.close(fd)",
                "            except OSError:",
                "                pass",
                "        raise",
                "def split_relative(value):",
                "    return [] if value == '.' else value.split('/')",
                "def open_parent(root_fd, parts, *, create=False):",
                "    fds = []",
                "    current_fd = root_fd",
                "    try:",
                "        for part in parts:",
                "            current_fd = open_directory(part, dir_fd=current_fd, create=create)",
                "            fds.append(current_fd)",
                "        return fds, current_fd",
                "    except BaseException:",
                "        for fd in reversed(fds):",
                "            try:",
                "                os.close(fd)",
                "            except OSError:",
                "                pass",
                "        raise",
                "def close_all(fds):",
                "    for fd in reversed(fds):",
                "        try:",
                "            os.close(fd)",
                "        except OSError:",
                "            pass",
                "def entry_for(info, entry_path):",
                "    modified_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(info.st_mtime))",
                "    if stat.S_ISDIR(info.st_mode):",
                "        return {'path': entry_path, 'kind': 'directory', 'byte_size': None, 'modified_at': modified_at}",
                "    return {'path': entry_path, 'kind': 'file', 'byte_size': info.st_size, 'modified_at': modified_at}",
                "def write_all(fd, payload):",
                "    offset = 0",
                "    while offset < len(payload):",
                "        offset += os.write(fd, payload[offset:])",
                "def write_new(parent_fd, name, payload):",
                "    fd = None",
                "    created = False",
                "    try:",
                "        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)",
                "        fd = os.open(name, flags, 0o600, dir_fd=parent_fd)",
                "        created = True",
                "        write_all(fd, payload)",
                "        os.fsync(fd)",
                "        return os.fstat(fd)",
                "    except BaseException:",
                "        if created:",
                "            try:",
                "                os.unlink(name, dir_fd=parent_fd)",
                "            except OSError:",
                "                pass",
                "        raise",
                "    finally:",
                "        if fd is not None:",
                "            os.close(fd)",
                "def replace_atomically(parent_fd, name, payload):",
                "    temporary = f'.fleet-write-{os.getpid()}-{time.time_ns()}'",
                "    fd = None",
                "    try:",
                "        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600, dir_fd=parent_fd)",
                "        write_all(fd, payload)",
                "        os.fsync(fd)",
                "        os.close(fd)",
                "        fd = None",
                "        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)",
                "        try:",
                "            os.fsync(parent_fd)",
                "        except OSError:",
                "            pass",
                "        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)",
                "    finally:",
                "        if fd is not None:",
                "            os.close(fd)",
                "        try:",
                "            os.unlink(temporary, dir_fd=parent_fd)",
                "        except FileNotFoundError:",
                "            pass",
                "try:",
                "    try:",
                "        base_fds, root_fd = open_chain(create=operation == 'write')",
                "    except FileNotFoundError:",
                "        if relative == '.' and operation == 'list':",
                "            respond({'ok': True, 'entries': [], 'truncated': False})",
                "        if relative == '.' and operation == 'stat':",
                "            respond({'ok': True, 'entry': None})",
                "        raise",
                "    relative_parts = split_relative(relative)",
                "    if operation == 'list':",
                "        target_fds, target_fd = open_parent(root_fd, relative_parts)",
                "        try:",
                "            entries = []",
                "            observed = []",
                "            with os.scandir(target_fd) as scanner:",
                "                for item in scanner:",
                "                    observed.append(item)",
                "                    if len(observed) >= limit + 1:",
                "                        break",
                "            truncated = len(observed) > limit",
                "            for item in sorted(observed[:limit], key=lambda value: value.name):",
                "                child_stat = os.stat(item.name, dir_fd=target_fd, follow_symlinks=False)",
                "                child_relative = item.name if relative == '.' else f'{relative}/{item.name}'",
                "                entries.append(entry_for(child_stat, child_relative))",
                "        finally:",
                "            close_all(target_fds)",
                "        respond({'ok': True, 'entries': entries, 'truncated': truncated})",
                "    if operation == 'stat':",
                "        if not relative_parts:",
                "            respond({'ok': True, 'entry': entry_for(os.fstat(root_fd), '.')})",
                "        parent_fds, parent_fd = open_parent(root_fd, relative_parts[:-1])",
                "        try:",
                "            try:",
                "                target_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)",
                "            except FileNotFoundError:",
                "                if allow_missing:",
                "                    respond({'ok': True, 'entry': None})",
                "                raise",
                "            if stat.S_ISLNK(target_stat.st_mode):",
                "                fail('unsafe')",
                "            respond({'ok': True, 'entry': entry_for(target_stat, relative)})",
                "        finally:",
                "            close_all(parent_fds)",
                "    if operation == 'read':",
                "        if not relative_parts:",
                "            fail('is_directory')",
                "        parent_fds, parent_fd = open_parent(root_fd, relative_parts[:-1])",
                "        fd = None",
                "        try:",
                "            target_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)",
                "            if stat.S_ISLNK(target_stat.st_mode):",
                "                fail('unsafe')",
                "            if stat.S_ISDIR(target_stat.st_mode):",
                "                fail('is_directory')",
                "            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)",
                "            fd = os.open(relative_parts[-1], flags, dir_fd=parent_fd)",
                "            data = os.read(fd, max_bytes + 1)",
                "        except FileNotFoundError:",
                "            fail('not_found')",
                "        finally:",
                "            if fd is not None:",
                "                os.close(fd)",
                "            close_all(parent_fds)",
                "        if len(data) > max_bytes:",
                "            fail('read_bound')",
                "        try:",
                "            content = data.decode('utf-8')",
                "        except UnicodeDecodeError:",
                "            fail('invalid_utf8')",
                "        respond({'ok': True, 'content': content})",
                "    if operation == 'write':",
                "        payload = base64.b64decode(content_b64.encode('ascii'))",
                "        if len(payload) > max_bytes:",
                "            fail('too_large')",
                "        parent_fds, parent_fd = open_parent(root_fd, relative_parts[:-1], create=True)",
                "        try:",
                "            try:",
                "                existing_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)",
                "            except FileNotFoundError:",
                "                existing_stat = None",
                "            if existing_stat is not None:",
                "                if stat.S_ISLNK(existing_stat.st_mode):",
                "                    fail('unsafe')",
                "                if stat.S_ISDIR(existing_stat.st_mode):",
                "                    fail('is_directory')",
                "                if not overwrite:",
                "                    fail('conflict')",
                "                written_stat = replace_atomically(parent_fd, relative_parts[-1], payload)",
                "            else:",
                "                written_stat = write_new(parent_fd, relative_parts[-1], payload)",
                "        finally:",
                "            close_all(parent_fds)",
                "        respond({'ok': True, 'entry': entry_for(written_stat, relative)})",
                "    fail('unsupported')",
                "except FileNotFoundError:",
                "    fail('not_found')",
                "except FileExistsError:",
                "    fail('conflict')",
                "except IsADirectoryError:",
                "    fail('is_directory')",
                "except NotADirectoryError:",
                "    fail('not_directory')",
                "except UnsafePath:",
                "    fail('unsafe')",
                "except OSError:",
                "    fail('unsafe')",
                "finally:",
                "    close_all(base_fds if 'base_fds' in locals() else [])",
            )
        )
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
