"""Daytona-backed implementation of the Session Workspace port."""

from __future__ import annotations

import base64
import contextlib
import json
import time
from collections.abc import Mapping
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from threading import Lock
from typing import Any, cast

from fleet_rlm.daytona.errors import is_sandbox_not_found, map_provider_error
from fleet_rlm.daytona.workspace_agent import WorkspaceAgentStorageError, run_workspace_agent, run_workspace_agent_async
from fleet_rlm.files.volume_paths import DEFAULT_VOLUME_MOUNT_PATH, as_posix, validate_mount_path
from fleet_rlm.files.volume_storage import VolumeFile
from fleet_rlm.files.workspace_models import WorkspaceEntry, WorkspaceListResult, WorkspaceTextPage
from fleet_rlm.files.workspace_validation import normalize_workspace_path


class WorkspaceStorageError(OSError):
    """Mounted-volume mutation failure that is not a client path error."""

    code = "unsupported_storage"
    public_message = "Session Workspace storage does not support this mutation"


_CACHEABLE_PATTERNS = (
    "artifacts/*",
    "sessions/*/output/*",
    "recursive/*/*",
)


def _cacheable_path(path: str, mount_path: str) -> bool:
    """Match cache patterns against a path relative to the trusted mount.

    Patterns match segment-wise so a ``*`` wildcard never crosses a ``/``
    boundary; ``artifacts/*`` matches direct children only.
    """
    try:
        relative = PurePosixPath(path).relative_to(PurePosixPath(mount_path))
    except ValueError:
        return False
    parts = relative.parts
    for pattern in _CACHEABLE_PATTERNS:
        pattern_parts = PurePosixPath(pattern).parts
        if len(parts) == len(pattern_parts) and all(
            fnmatchcase(part, pattern_part) for part, pattern_part in zip(parts, pattern_parts, strict=True)
        ):
            return True
    return False


def _list_cache_key(root: str, *, max_depth: int, max_files: int) -> str:
    return f"list:{root}:depth={max_depth}:count={max_files}"


def _modified_timestamp(value: Any) -> float | None:
    if hasattr(value, "timestamp"):
        try:
            value = value.timestamp()
        except Exception:
            return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class _LRUCache:
    """Thread-safe LRU cache for file content with byte and entry limits."""

    def __init__(self, max_size_mb: int = 100, max_entries: int = 1024):
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._cache: dict[str, tuple[bytes, float]] = {}
        self.max_bytes = max_size_mb * 1024 * 1024
        self.max_entries = max_entries
        self._lock = Lock()
        self._current_size = 0

    def get(self, key: str) -> bytes | None:
        """Get cached value by key, updating access time."""
        with self._lock:
            if key not in self._cache:
                return None
            value, _timestamp = self._cache[key]
            # Update access time
            self._cache[key] = (value, time.time())
            return value

    def put(self, key: str, value: bytes) -> None:
        """Store value in cache with current timestamp."""
        if len(value) > self.max_bytes:
            self.evict(key)
            return
        with self._lock:
            # Replace existing key without spurious evictions
            if key in self._cache:
                old_value, _ = self._cache.pop(key)
                self._current_size -= len(old_value)

            # Evict oldest entries while either the byte budget or the entry
            # count limit would be exceeded.
            while self._cache and (
                self._current_size + len(value) > self.max_bytes or len(self._cache) >= self.max_entries
            ):
                oldest_key = min(self._cache, key=lambda k: self._cache[k][1])
                old_value, _ = self._cache.pop(oldest_key)
                self._current_size -= len(old_value)

            self._cache[key] = (value, time.time())
            self._current_size += len(value)

    def evict(self, key: str) -> None:
        """Remove specific key from cache."""
        with self._lock:
            if key in self._cache:
                value, _ = self._cache.pop(key)
                self._current_size -= len(value)

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
            self._current_size = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


class VolumeFSCacheState:
    """Shared, generation-aware cache coordinator for one sandbox and mount.

    Both volume adapters over the same sandbox and mount must share one
    instance so a mutation through either adapter invalidates the other's
    view. A monotonically increasing mutation generation prevents an in-flight
    read, stat, or listing from re-caching provider data that was fetched
    before a local mutation invalidated it.
    """

    def __init__(self, *, content_max_size_mb: int = 100, metadata_max_size_mb: int = 10):
        self._content = _LRUCache(max_size_mb=content_max_size_mb)
        self._metadata = _LRUCache(max_size_mb=metadata_max_size_mb)
        self._lock = Lock()
        self._generation = 0

    @property
    def generation(self) -> int:
        """Snapshot the mutation generation before fetching provider data."""
        with self._lock:
            return self._generation

    def invalidate_mutation(self, path: str) -> None:
        """Record a local mutation: bump generation, drop affected entries."""
        with self._lock:
            self._generation += 1
            self._content.evict(path)
            self._metadata.clear()

    def get_content(self, key: str) -> bytes | None:
        return self._content.get(key)

    def put_content(self, key: str, value: bytes, *, generation: int) -> None:
        """Store fetched content only when no mutation happened since fetch."""
        with self._lock:
            if generation != self._generation:
                return
            self._content.put(key, value)

    def get_metadata(self, key: str) -> bytes | None:
        return self._metadata.get(key)

    def put_metadata(self, key: str, value: bytes, *, generation: int) -> None:
        """Store fetched metadata only when no mutation happened since fetch."""
        with self._lock:
            if generation != self._generation:
                return
            self._metadata.put(key, value)


class AsyncDaytonaVolumeFS:
    """Native async byte I/O over one mounted Workspace Volume Scope."""

    CACHEABLE_PATTERNS = _CACHEABLE_PATTERNS

    def __init__(
        self,
        sandbox: Any,
        *,
        mount_path: str = DEFAULT_VOLUME_MOUNT_PATH,
        cache_state: VolumeFSCacheState | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.mount_path = str(validate_mount_path(mount_path))
        self._cache_state = cache_state if cache_state is not None else VolumeFSCacheState()

    def _should_cache(self, path: str) -> bool:
        return _cacheable_path(path, self.mount_path)

    def _invalidate_mutation(self, path: str) -> None:
        self._cache_state.invalidate_mutation(path)

    async def write_bytes(self, logical_path: str, data: bytes) -> None:
        path = as_posix(logical_path)
        parent = str(PurePosixPath(path).parent)
        with contextlib.suppress(Exception):
            await self.sandbox.fs.create_folder(parent, "700")
        try:
            await self.sandbox.fs.upload_file(data, path)
        finally:
            self._invalidate_mutation(path)

    async def read_bytes(self, logical_path: str, *, use_cache: bool = True) -> bytes:
        path = as_posix(logical_path)

        # Check cache first
        if use_cache and self._should_cache(path):
            cached = self._cache_state.get_content(path)
            if cached is not None:
                return cached

        # Snapshot the mutation generation before fetching so a concurrent
        # local mutation prevents caching stale provider data.
        generation = self._cache_state.generation

        # Perform actual read
        raw = await self.sandbox.fs.download_file(path)
        if isinstance(raw, bytes):
            data = raw
        elif isinstance(raw, str):
            data = raw.encode("utf-8")
        else:
            data = bytes(raw)

        # Cache result if applicable
        if use_cache and self._should_cache(path):
            self._cache_state.put_content(path, data, generation=generation)

        return data

    async def exists(self, logical_path: str) -> bool:
        try:
            await self.read_bytes(logical_path)
        except Exception as exc:
            if _provider_not_found(exc) or is_sandbox_not_found(exc):
                return False
            raise map_provider_error(exc) from exc
        return True

    async def remove(self, logical_path: str) -> None:
        path = as_posix(logical_path)
        try:
            await self.sandbox.fs.delete_file(path)
        except Exception as exc:
            if not _provider_not_found(exc):
                raise map_provider_error(exc) from exc
        finally:
            self._invalidate_mutation(path)

    async def stat(self, logical_path: str) -> dict[str, Any] | None:
        """Get file metadata with caching."""
        path = as_posix(logical_path)

        # Check metadata cache
        cache_key = f"stat:{path}"
        cached = self._cache_state.get_metadata(cache_key)
        if cached is not None:
            return json.loads(cached.decode("utf-8"))

        generation = self._cache_state.generation
        try:
            # Get file info from sandbox
            entry = await self.sandbox.fs.list_files(
                str(PurePosixPath(path).parent),
                depth=1,
            )
            for e in entry:
                if getattr(e, "path", None) == path:
                    result = {
                        "path": path,
                        "is_dir": getattr(e, "is_dir", False),
                        "mod_time": _modified_timestamp(getattr(e, "mod_time", None)),
                    }
                    # Cache metadata
                    self._cache_state.put_metadata(cache_key, json.dumps(result).encode("utf-8"), generation=generation)
                    return result
            return None
        except Exception as exc:
            if _provider_not_found(exc) or is_sandbox_not_found(exc):
                return None
            raise map_provider_error(exc) from exc

    async def list_files(self, logical_root: str, *, max_depth: int, max_files: int) -> tuple[VolumeFile, ...]:
        if max_depth <= 0:
            raise ValueError("max_depth must be positive")
        if max_files <= 0:
            raise ValueError("max_files must be positive")
        root = as_posix(logical_root)

        # Check if directory listing is cached
        cache_key = _list_cache_key(root, max_depth=max_depth, max_files=max_files)
        cached = self._cache_state.get_metadata(cache_key)
        if cached is not None:
            cached_data = json.loads(cached.decode("utf-8"))
            return tuple(VolumeFile(f["path"], f["modified_at"]) for f in cached_data)

        generation = self._cache_state.generation
        entries = await self.sandbox.fs.list_files(root, depth=max_depth)
        results: list[VolumeFile] = []
        for entry in entries:
            path = getattr(entry, "path", None)
            if not isinstance(path, str) or not _is_under(path, root) or bool(getattr(entry, "is_dir", False)):
                continue
            modified_at = _modified_timestamp(getattr(entry, "mod_time", None))
            if modified_at is None:
                continue
            results.append(VolumeFile(path, modified_at))
            if len(results) >= max_files:
                break

        # Cache directory listing
        if len(results) <= 100:  # Only cache small listings
            cached_data = [{"path": rf.path, "modified_at": rf.modified_at} for rf in results]
            self._cache_state.put_metadata(cache_key, json.dumps(cached_data).encode("utf-8"), generation=generation)

        return tuple(results)


class DaytonaSandboxVolumeFs:
    """Synchronous mounted-byte view used only by DSPy host tools."""

    CACHEABLE_PATTERNS = _CACHEABLE_PATTERNS

    def __init__(
        self,
        sandbox: Any,
        *,
        mount_path: str = DEFAULT_VOLUME_MOUNT_PATH,
        cache_state: VolumeFSCacheState | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.mount_path = str(validate_mount_path(mount_path))
        self._cache_state = cache_state if cache_state is not None else VolumeFSCacheState()

    def _should_cache(self, path: str) -> bool:
        return _cacheable_path(path, self.mount_path)

    def _invalidate_mutation(self, path: str) -> None:
        self._cache_state.invalidate_mutation(path)

    def write_bytes(self, logical_path: str, data: bytes) -> None:
        path = as_posix(logical_path)
        parent = str(PurePosixPath(path).parent)
        with contextlib.suppress(Exception):
            self.sandbox.fs.create_folder(parent, "700")
        try:
            self.sandbox.fs.upload_file(data, path)
        finally:
            self._invalidate_mutation(path)

    def read_bytes(self, logical_path: str, *, use_cache: bool = True) -> bytes:
        path = as_posix(logical_path)

        # Check cache first
        if use_cache and self._should_cache(path):
            cached = self._cache_state.get_content(path)
            if cached is not None:
                return cached

        # Snapshot the mutation generation before fetching so a concurrent
        # local mutation prevents caching stale provider data.
        generation = self._cache_state.generation

        # Perform actual read
        raw = self.sandbox.fs.download_file(path)
        if isinstance(raw, bytes):
            data = raw
        elif isinstance(raw, str):
            data = raw.encode("utf-8")
        else:
            data = bytes(raw)

        # Cache result if applicable
        if use_cache and self._should_cache(path):
            self._cache_state.put_content(path, data, generation=generation)

        return data

    def exists(self, logical_path: str) -> bool:
        try:
            self.read_bytes(logical_path)
        except Exception as exc:
            if _provider_not_found(exc) or is_sandbox_not_found(exc):
                return False
            raise map_provider_error(exc) from exc
        return True

    def remove(self, logical_path: str) -> None:
        path = as_posix(logical_path)
        try:
            self.sandbox.fs.delete_file(path)
        except Exception as exc:
            if not _provider_not_found(exc):
                raise map_provider_error(exc) from exc
        finally:
            self._invalidate_mutation(path)

    def list_files(self, logical_root: str, *, max_depth: int, max_files: int) -> tuple[VolumeFile, ...]:
        if max_depth <= 0:
            raise ValueError("max_depth must be positive")
        if max_files <= 0:
            raise ValueError("max_files must be positive")
        root = as_posix(logical_root)

        cache_key = _list_cache_key(root, max_depth=max_depth, max_files=max_files)
        cached = self._cache_state.get_metadata(cache_key)
        if cached is not None:
            cached_data = json.loads(cached.decode("utf-8"))
            return tuple(VolumeFile(f["path"], f["modified_at"]) for f in cached_data)

        generation = self._cache_state.generation
        entries = self.sandbox.fs.list_files(root, depth=max_depth)
        results: list[VolumeFile] = []
        for entry in entries:
            path = getattr(entry, "path", None)
            if not isinstance(path, str) or not _is_under(path, root) or bool(getattr(entry, "is_dir", False)):
                continue
            modified_at = _modified_timestamp(getattr(entry, "mod_time", None))
            if modified_at is None:
                continue
            results.append(VolumeFile(path, modified_at))
            if len(results) >= max_files:
                break

        if len(results) <= 100:
            cached_data = [{"path": rf.path, "modified_at": rf.modified_at} for rf in results]
            self._cache_state.put_metadata(cache_key, json.dumps(cached_data).encode("utf-8"), generation=generation)
        return tuple(results)


def _is_under(path: str, root: str) -> bool:
    try:
        PurePosixPath(path).relative_to(PurePosixPath(root))
    except ValueError:
        return False
    return True


def _provider_not_found(exc: BaseException) -> bool:
    if isinstance(exc, FileNotFoundError) or getattr(exc, "status_code", None) == 404:
        return True
    response = getattr(exc, "response", None)
    return response is not None and getattr(response, "status_code", None) == 404


_MAX_CURSOR_CHARS = 512


def _encode_text_cursor(path: str, offset: int) -> str:
    payload = json.dumps(
        {"offset": offset, "path": path, "v": 1},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_text_cursor(cursor: str, path: str, byte_size: int) -> int:
    if not isinstance(cursor, str) or not cursor or len(cursor) > _MAX_CURSOR_CHARS:
        raise ValueError("workspace cursor is invalid")
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise ValueError("workspace cursor is invalid") from None
    if not isinstance(payload, dict) or set(payload) != {"offset", "path", "v"}:
        raise ValueError("workspace cursor is invalid")
    version = payload.get("v")
    cursor_path = payload.get("path")
    offset = payload.get("offset")
    if (
        type(version) is not int
        or version != 1
        or cursor_path != path
        or type(offset) is not int
        or offset < 0
        or offset > byte_size
    ):
        raise ValueError("workspace cursor is invalid")
    if _encode_text_cursor(cursor_path, offset) != cursor:
        raise ValueError("workspace cursor is invalid")
    return offset


def _normalize_list_cursor(path: str, after: str | None) -> str | None:
    if after is None:
        return None
    normalized = normalize_workspace_path(after)
    parent = normalized.rpartition("/")[0] or "."
    if parent != path:
        raise ValueError("workspace list cursor is invalid")
    return normalized


def _entry_from_payload(raw: Mapping[str, object]) -> WorkspaceEntry:
    byte_size = raw.get("byte_size")
    modified_at = raw.get("modified_at")
    checksum = raw.get("checksum")
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
        checksum_sha256=str(checksum) if isinstance(checksum, str) else None,
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

    def list_entries(
        self,
        path: str,
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> WorkspaceListResult:
        relative = normalize_workspace_path(path, allow_root=True)
        if limit < 1 or limit > 100:
            raise ValueError("workspace list limit must be between 1 and 100")
        normalized_after = _normalize_list_cursor(relative, after)
        payload = self._atomic_run(
            operation="list",
            relative=relative,
            allow_missing=relative == ".",
            max_bytes=0,
            limit=limit,
            overwrite=False,
            content_b64="",
            after=normalized_after or "",
            offset=0,
            max_chars=0,
        )
        raw_entries = payload.get("entries")
        entries: list[WorkspaceEntry] = []
        if isinstance(raw_entries, list):
            for item in raw_entries:
                if isinstance(item, dict):
                    entries.append(_entry_from_payload(cast(Mapping[str, object], item)))
        next_cursor = payload.get("next_cursor")
        return WorkspaceListResult(
            entries=tuple(entries),
            truncated=bool(payload.get("truncated")),
            next_cursor=str(next_cursor) if isinstance(next_cursor, str) else None,
        )

    def stat(self, path: str, *, include_checksum: bool = False) -> WorkspaceEntry | None:
        relative = normalize_workspace_path(path, allow_root=True)
        payload = self._atomic_run(
            operation="stat",
            relative=relative,
            allow_missing=True,
            max_bytes=self._max_file_bytes if include_checksum else 0,
            limit=0,
            overwrite=False,
            content_b64="",
            after="",
            offset=0,
            max_chars=0,
            checksum=include_checksum,
        )
        if payload.get("entry") is None:
            if relative == ".":
                return WorkspaceEntry(".", "directory", None, None)
            return None
        entry = payload["entry"]
        if not isinstance(entry, dict):
            raise RuntimeError("workspace stat returned invalid entry")
        return _entry_from_payload(cast(Mapping[str, object], entry))

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int,
    ) -> WorkspaceTextPage:
        relative = normalize_workspace_path(path)
        bound = min(self._max_file_bytes, int(max_bytes))
        if bound < 1 or max_chars < 1:
            raise ValueError("workspace read bound must be positive")
        if max_chars > 10_000:
            raise ValueError("workspace read character bound is invalid")
        offset = 0
        if cursor is not None:
            stat = self.stat(relative)
            if stat is None or stat.byte_size is None:
                raise ValueError("workspace cursor is invalid")
            offset = _decode_text_cursor(cursor, relative, stat.byte_size)
        payload = self._atomic_run(
            operation="read_page",
            relative=relative,
            allow_missing=False,
            max_bytes=bound,
            limit=0,
            overwrite=False,
            content_b64="",
            after="",
            offset=offset,
            max_chars=max_chars,
        )
        content = payload.get("content")
        if not isinstance(content, str):
            raise RuntimeError("workspace read returned invalid content")
        byte_size = payload.get("byte_size")
        next_offset = payload.get("next_offset")
        eof = bool(payload.get("eof"))
        if not isinstance(byte_size, int) or not isinstance(next_offset, int):
            raise RuntimeError("workspace read returned invalid page metadata")
        next_cursor = None if eof else _encode_text_cursor(relative, next_offset)
        return WorkspaceTextPage(content, next_cursor, byte_size, eof)

    def read_text(self, path: str, *, max_bytes: int) -> str:
        """Compatibility adapter for internal callers that need a bounded whole read."""
        page = self.read_text_page(path, cursor=None, max_chars=max_bytes, max_bytes=max_bytes)
        if not page.eof:
            raise ValueError("workspace file exceeds read bound")
        return page.content

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
            after="",
            offset=0,
            max_chars=0,
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

    def append_text(self, path: str, content: str) -> WorkspaceEntry:
        relative = normalize_workspace_path(path)
        if not isinstance(content, str):
            raise ValueError("workspace content must be text")
        data = content.encode("utf-8")
        if len(data) > self._max_file_bytes:
            raise ValueError("workspace file exceeds maximum size")
        payload = self._atomic_run(
            operation="append",
            relative=relative,
            allow_missing=True,
            max_bytes=self._max_file_bytes,
            limit=0,
            overwrite=False,
            content_b64=base64.b64encode(data).decode("ascii"),
            after="",
            offset=0,
            max_chars=0,
        )
        raw_warnings = payload.get("warnings")
        self._last_warnings = (
            tuple(cast(dict[str, object], item) for item in raw_warnings if isinstance(item, dict))
            if isinstance(raw_warnings, list)
            else ()
        )
        entry = payload.get("entry")
        if not isinstance(entry, dict):
            raise RuntimeError("workspace append returned invalid entry")
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
        after: str,
        offset: int,
        max_chars: int,
        checksum: bool = False,
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
                after=after,
                offset=offset,
                max_chars=max_chars,
                checksum=checksum,
            )
        except WorkspaceAgentStorageError as exc:
            raise WorkspaceStorageError(*exc.args) from exc


class AsyncDaytonaSessionWorkspaceFS:
    """Native async Session Workspace adapter for FastAPI-facing access."""

    def __init__(self, sandbox: Any, *, volume_root: str, root: str, max_file_bytes: int) -> None:
        validated = DaytonaSessionWorkspaceFS(
            sandbox,
            volume_root=volume_root,
            root=root,
            max_file_bytes=max_file_bytes,
        )
        self._sandbox = sandbox
        self._volume_root = validated._volume_root
        self._root = validated._root
        self._max_file_bytes = max_file_bytes
        self._last_warnings: tuple[dict[str, object], ...] = ()

    @property
    def last_warnings(self) -> tuple[dict[str, object], ...]:
        return self._last_warnings

    async def list_entries(
        self,
        path: str,
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> WorkspaceListResult:
        relative = normalize_workspace_path(path, allow_root=True)
        if limit < 1 or limit > 100:
            raise ValueError("workspace list limit must be between 1 and 100")
        payload = await self._atomic_run(
            operation="list",
            relative=relative,
            allow_missing=relative == ".",
            limit=limit,
            after=_normalize_list_cursor(relative, after) or "",
        )
        raw_entries = payload.get("entries", [])
        entries = (
            tuple(
                _entry_from_payload(cast(Mapping[str, object], item)) for item in raw_entries if isinstance(item, dict)
            )
            if isinstance(raw_entries, list)
            else ()
        )
        cursor = payload.get("next_cursor")
        return WorkspaceListResult(
            entries=entries,
            truncated=bool(payload.get("truncated")),
            next_cursor=str(cursor) if isinstance(cursor, str) else None,
        )

    async def stat(self, path: str, *, include_checksum: bool = False) -> WorkspaceEntry | None:
        relative = normalize_workspace_path(path, allow_root=True)
        payload = await self._atomic_run(
            operation="stat",
            relative=relative,
            allow_missing=True,
            max_bytes=self._max_file_bytes if include_checksum else 0,
            checksum=include_checksum,
        )
        entry = payload.get("entry")
        if entry is None:
            return WorkspaceEntry(".", "directory", None, None) if relative == "." else None
        if not isinstance(entry, dict):
            raise RuntimeError("workspace stat returned invalid entry")
        return _entry_from_payload(cast(Mapping[str, object], entry))

    async def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int,
    ) -> WorkspaceTextPage:
        relative = normalize_workspace_path(path)
        bound = min(self._max_file_bytes, int(max_bytes))
        if bound < 1 or max_chars < 1:
            raise ValueError("workspace read bound must be positive")
        if max_chars > 10_000:
            raise ValueError("workspace read character bound is invalid")
        offset = 0
        if cursor is not None:
            entry = await self.stat(relative)
            if entry is None or entry.byte_size is None:
                raise ValueError("workspace cursor is invalid")
            offset = _decode_text_cursor(cursor, relative, entry.byte_size)
        payload = await self._atomic_run(
            operation="read_page",
            relative=relative,
            max_bytes=bound,
            offset=offset,
            max_chars=max_chars,
        )
        content = payload.get("content")
        byte_size = payload.get("byte_size")
        next_offset = payload.get("next_offset")
        if not isinstance(content, str) or not isinstance(byte_size, int) or not isinstance(next_offset, int):
            raise RuntimeError("workspace read returned invalid page")
        eof = bool(payload.get("eof"))
        return WorkspaceTextPage(
            content,
            None if eof else _encode_text_cursor(relative, next_offset),
            byte_size,
            eof,
        )

    async def read_text(self, path: str, *, max_bytes: int) -> str:
        page = await self.read_text_page(path, cursor=None, max_chars=max_bytes, max_bytes=max_bytes)
        if not page.eof:
            raise ValueError("workspace file exceeds read bound")
        return page.content

    async def write_text(self, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry:
        return await self._mutate("write", path, content, overwrite=overwrite)

    async def append_text(self, path: str, content: str) -> WorkspaceEntry:
        return await self._mutate("append", path, content, overwrite=False)

    async def _mutate(self, operation: str, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry:
        relative = normalize_workspace_path(path)
        if not isinstance(content, str):
            raise ValueError("workspace content must be text")
        data = content.encode("utf-8")
        if len(data) > self._max_file_bytes:
            raise ValueError("workspace file exceeds maximum size")
        payload = await self._atomic_run(
            operation=operation,
            relative=relative,
            allow_missing=True,
            max_bytes=self._max_file_bytes,
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
            raise RuntimeError(f"workspace {operation} returned invalid entry")
        return _entry_from_payload(cast(Mapping[str, object], entry))

    async def _atomic_run(
        self,
        *,
        operation: str,
        relative: str,
        allow_missing: bool = False,
        max_bytes: int = 0,
        limit: int = 0,
        overwrite: bool = False,
        content_b64: str = "",
        after: str = "",
        offset: int = 0,
        max_chars: int = 0,
        checksum: bool = False,
    ) -> dict[str, object]:
        try:
            return await run_workspace_agent_async(
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
                after=after,
                offset=offset,
                max_chars=max_chars,
                checksum=checksum,
            )
        except WorkspaceAgentStorageError as exc:
            raise WorkspaceStorageError(*exc.args) from exc
