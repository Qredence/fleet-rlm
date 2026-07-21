"""Daytona-backed implementation of the Session Workspace port."""

from __future__ import annotations

import base64
import errno
import json
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any, cast

from fleet_rlm.files.workspace_models import WorkspaceEntry, WorkspaceListResult
from fleet_rlm.files.workspace_validation import normalize_workspace_path

# Captured from a real Daytona-mounted Volume on 2026-07-20: ``os.link``
# returns EPERM for the temporary-file publication attempt.  This allowlist is
# consulted only around that one link operation; EPERM from any other I/O
# operation remains a hard failure.
_UNSUPPORTED_LINK_ERRNOS = frozenset({errno.EPERM})
# Daytona-mounted Volumes may also report ENOSYS when replacement is not
# implemented by the provider filesystem.  Keep this allowlist scoped to the
# atomic replacement operation; ENOSYS from any other I/O remains a hard error.
# The adapter runs on macOS during local development but emits code for the
# Linux Daytona Sandbox.  Bake both host errno constants (for local unit tests)
# and explicit Linux numbers: ENOSYS=38, EOPNOTSUPP/ENOTSUP=95.
_UNSUPPORTED_REPLACE_ERRNOS = frozenset(
    {
        errno.EPERM,
        errno.EXDEV,
        errno.EOPNOTSUPP,
        getattr(errno, "ENOTSUP", errno.EOPNOTSUPP),
        errno.ENOSYS,
        38,
        95,
    }
)


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
        code = "\n".join(
            (
                "import base64, errno, json, os, stat, time",
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
                "class StorageError(Exception):",
                "    def __init__(self, errno_value):",
                "        super().__init__(errno_value)",
                "        self.errno = errno_value",
                "class ReplacementUnsupported(Exception):",
                "    def __init__(self, errno_value):",
                "        super().__init__(errno_value)",
                "        self.errno = errno_value",
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
                "        try:",
                "            written = os.write(fd, payload[offset:])",
                "        except InterruptedError:",
                "            continue",
                "        if written <= 0:",
                "            raise OSError('short write')",
                "        offset += written",
                "def fsync_directory(parent_fd):",
                "    try:",
                "        os.fsync(parent_fd)",
                "    except OSError as exc:",
                "        raise StorageError(exc.errno) from exc",
                "def replace_existing(parent_fd, name, payload):",
                "    temporary = f'.fleet-write-{os.getpid()}-{time.time_ns()}'",
                "    fd = None",
                "    temporary_removed = False",
                "    cleanup_errno = None",
                "    try:",
                "        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)",
                "        fd = os.open(temporary, flags, 0o600, dir_fd=parent_fd)",
                "        write_all(fd, payload)",
                "        os.fsync(fd)",
                "        os.close(fd)",
                "        fd = None",
                "        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)",
                "        temporary_removed = True",
                "        try:",
                "            fsync_directory(parent_fd)",
                "        except StorageError as exc:",
                "            cleanup_errno = exc.errno",
                "        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False), cleanup_errno",
                "    except (FileNotFoundError, FileExistsError):",
                "        raise",
                "    except OSError as exc:",
                f"        if exc.errno in {sorted(_UNSUPPORTED_REPLACE_ERRNOS)!r}:",
                "            raise ReplacementUnsupported(exc.errno) from exc",
                "        raise StorageError(exc.errno) from exc",
                "    finally:",
                "        if fd is not None:",
                "            os.close(fd)",
                "        if not temporary_removed:",
                "            try:",
                "                os.unlink(temporary, dir_fd=parent_fd)",
                "            except OSError:",
                "                pass",
                "def write_new_direct(parent_fd, name, payload):",
                "    fd = None",
                "    created_stat = None",
                "    def cleanup_created():",
                "        if created_stat is None:",
                "            return",
                "        try:",
                "            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)",
                "            if (current.st_dev, current.st_ino) == (created_stat.st_dev, created_stat.st_ino):",
                "                os.unlink(name, dir_fd=parent_fd)",
                "        except OSError:",
                "            pass",
                "    try:",
                "        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)",
                "        fd = os.open(name, flags, 0o600, dir_fd=parent_fd)",
                "        created_stat = os.fstat(fd)",
                "        write_all(fd, payload)",
                "        os.fsync(fd)",
                "        fsync_directory(parent_fd)",
                "        return os.fstat(fd)",
                "    except FileExistsError:",
                "        raise",
                "    except OSError as exc:",
                "        cleanup_created()",
                "        raise StorageError(exc.errno) from exc",
                "    except BaseException:",
                "        cleanup_created()",
                "        raise",
                "    finally:",
                "        if fd is not None:",
                "            os.close(fd)",
                "def publish_new(parent_fd, name, payload):",
                "    temporary = f'.fleet-write-{os.getpid()}-{time.time_ns()}'",
                "    fd = None",
                "    temporary_removed = False",
                "    cleanup_errno = None",
                "    try:",
                "        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600, dir_fd=parent_fd)",
                "        write_all(fd, payload)",
                "        os.fsync(fd)",
                "        os.close(fd)",
                "        fd = None",
                "        try:",
                "            os.link(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)",
                "            pass",
                "        except OSError as exc:",
                "            if exc.errno == errno.EEXIST:",
                "                raise FileExistsError(name) from exc",
                f"            if exc.errno not in {sorted(_UNSUPPORTED_LINK_ERRNOS)!r}:",
                "                raise StorageError(exc.errno) from exc",
                "            direct_stat = write_new_direct(parent_fd, name, payload)",
                "            try:",
                "                os.unlink(temporary, dir_fd=parent_fd)",
                "                temporary_removed = True",
                "            except OSError as cleanup_exc:",
                "                cleanup_errno = cleanup_exc.errno",
                "            return direct_stat, cleanup_errno",
                "        fsync_directory(parent_fd)",
                "        try:",
                "            os.unlink(temporary, dir_fd=parent_fd)",
                "            temporary_removed = True",
                "        except OSError as exc:",
                "            cleanup_errno = exc.errno",
                "        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False), cleanup_errno",
                "    finally:",
                "        if fd is not None:",
                "            os.close(fd)",
                "        if not temporary_removed:",
                "            try:",
                "                os.unlink(temporary, dir_fd=parent_fd)",
                "            except OSError:",
                "                pass",
                "            else:",
                "                temporary_removed = True",
                "def read_existing(parent_fd, name, max_bytes):",
                "    fd = None",
                "    try:",
                "        fd = os.open(name, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0), dir_fd=parent_fd)",
                "        data = os.read(fd, max_bytes + 1)",
                "        if len(data) > max_bytes:",
                "            raise StorageError(errno.EFBIG)",
                "        return data",
                "    except OSError as exc:",
                "        if isinstance(exc, StorageError):",
                "            raise",
                "        raise StorageError(exc.errno) from exc",
                "    finally:",
                "        if fd is not None:",
                "            os.close(fd)",
                "def overwrite_existing_direct(parent_fd, name, payload, previous):",
                "    fd = None",
                "    cleanup_errno = None",
                "    try:",
                "        fd = os.open(name, os.O_WRONLY | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), dir_fd=parent_fd)",
                "        write_all(fd, payload)",
                "    except OSError as exc:",
                "        if fd is not None:",
                "            os.close(fd)",
                "            fd = None",
                "        try:",
                "            restore_fd = os.open(name, os.O_WRONLY | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), dir_fd=parent_fd)",
                "            try:",
                "                write_all(restore_fd, previous)",
                "                os.fsync(restore_fd)",
                "            finally:",
                "                os.close(restore_fd)",
                "        except OSError as restore_exc:",
                "            raise StorageError(restore_exc.errno) from restore_exc",
                "        raise StorageError(exc.errno) from exc",
                "    try:",
                "        os.fsync(fd)",
                "    except OSError as exc:",
                "        cleanup_errno = exc.errno",
                "    finally:",
                "        if fd is not None:",
                "            os.close(fd)",
                "    try:",
                "        fsync_directory(parent_fd)",
                "    except StorageError as exc:",
                "        cleanup_errno = exc.errno",
                "    return os.stat(name, dir_fd=parent_fd, follow_symlinks=False), cleanup_errno",
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
                "        fallback_overwrite = False",
                "        warnings = []",
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
                "                try:",
                "                    written_stat = replace_existing(parent_fd, relative_parts[-1], payload)",
                "                except ReplacementUnsupported:",
                "                    previous = read_existing(parent_fd, relative_parts[-1], max_bytes)",
                "                    written_stat = overwrite_existing_direct(parent_fd, relative_parts[-1], payload, previous)",
                "                    fallback_overwrite = True",
                "            else:",
                "                written_stat = publish_new(parent_fd, relative_parts[-1], payload)",
                "        finally:",
                "            close_all(parent_fds)",
                "        cleanup_errno = None",
                "        if fallback_overwrite:",
                "            warnings.append({'code': 'non_atomic_overwrite'})",
                "        if type(written_stat) is tuple and len(written_stat) == 2:",
                "            written_stat, cleanup_errno = written_stat",
                "        response = {'ok': True, 'entry': entry_for(written_stat, relative)}",
                "        if cleanup_errno is not None:",
                "            warnings.append({'code': 'cleanup_failed', 'errno': cleanup_errno})",
                "        if warnings:",
                "            response['warnings'] = warnings",
                "        respond(response)",
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
                "except StorageError as exc:",
                "    fail('unsupported_storage', errno=exc.errno)",
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
            if error == "unsupported_storage":
                raise WorkspaceStorageError(str(payload.get("errno") or "unknown"))
            raise ValueError("workspace path is unsafe")
        return payload
