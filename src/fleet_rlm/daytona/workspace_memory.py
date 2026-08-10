"""Daytona mounted-sandbox implementation of the Workspace Memory port.

The canonical store is ``memory/MEMORIES.md`` under the mounted Workspace
Volume root (migrated on first open from the legacy root ``MEMORIES.md``).
Reads are tolerant (malformed lines are skipped with a bounded warning count);
writes stay strict and serialized process-locally; every durable mutation is
one fsync'd rewrite through the mounted Workspace agent machinery.
"""

from __future__ import annotations

import base64
import json
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fleet_rlm.daytona.workspace_agent import WorkspaceAgentStorageError, run_workspace_agent
from fleet_rlm.files.memory_models import (
    WORKSPACE_MEMORY_BYTE_BUDGET,
    WORKSPACE_MEMORY_HEADER,
    WORKSPACE_MEMORY_INJECTION_TAIL_BYTES,
    WORKSPACE_MEMORY_MAX_LIST_LIMIT,
    WorkspaceMemoryAppendResult,
    WorkspaceMemoryEntry,
    WorkspaceMemoryEntryNotFoundError,
    WorkspaceMemoryListResult,
    WorkspaceMemoryReadResult,
    WorkspaceMemoryRecordError,
    WorkspaceMemoryStoreFullError,
    WorkspaceMemoryStoreUnavailableError,
    count_workspace_memory_warnings,
    normalize_workspace_memory_category,
    normalize_workspace_memory_id,
    normalize_workspace_memory_learning,
    parse_workspace_memory_lines,
    validate_workspace_memory_record,
)
from fleet_rlm.files.volume_paths import VolumePaths, as_posix

_MEMORY_DIR_NAME = "memory"
_MEMORY_NAME = "MEMORIES.md"
_HEADER_BYTES = (WORKSPACE_MEMORY_HEADER + "\n").encode("utf-8")
_MAX_IDLE_MEMORY_FILE_PARENT_LOCKS = 128


class _MemoryRootLock:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.users = 0


_memory_file_parent_locks: OrderedDict[str, _MemoryRootLock] = OrderedDict()
_memory_file_parent_locks_guard = threading.Lock()


@contextmanager
def _memory_file_parent_append_lock(memory_file_parent: str) -> Iterator[None]:
    """Serialize local-process memory writes with a bounded idle lock cache."""
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


_migrated_memory_dirs: OrderedDict[str, None] = OrderedDict()
_migrated_memory_dirs_guard = threading.Lock()
_MAX_MIGRATED_MEMORY_DIRS = 128


def _memory_dir_is_marked_migrated(memory_dir: str) -> bool:
    with _migrated_memory_dirs_guard:
        return memory_dir in _migrated_memory_dirs


def _mark_memory_dir_migrated(memory_dir: str) -> None:
    with _migrated_memory_dirs_guard:
        _migrated_memory_dirs[memory_dir] = None
        _migrated_memory_dirs.move_to_end(memory_dir)
        while len(_migrated_memory_dirs) > _MAX_MIGRATED_MEMORY_DIRS:
            _migrated_memory_dirs.popitem(last=False)


# Query-independent caching of a Workspace Memory digest is unsafe once the
# composition depends on the current request. Turn preparation therefore
# composes on demand and uses mutations only as ordinary immediate writes.


def invalidate_workspace_memory_digest(volume_root: str) -> None:
    """Cache-free compatibility point for existing mutation call sites."""
    del volume_root


_INJECTION_RELEVANT_LIMIT = 4
_INJECTION_RECENT_COUNT = 4


def _injection_query(request: str) -> str:
    """Use only the current user request, bounded to the search-tool limit."""
    if not isinstance(request, str):
        return ""
    body = request.strip()
    if not body:
        return ""
    encoded = body.encode("utf-8")[-256:]
    return encoded.decode("utf-8", errors="ignore")


def _canonical_memory_record(entry: WorkspaceMemoryEntry) -> bytes:
    if entry.record_version == 3:
        from fleet_rlm.files.memory_models import format_workspace_memory_v3_record

        return format_workspace_memory_v3_record(
            entry.learning,
            entry.category,
            memory_id=entry.memory_id,
            created_at=entry.timestamp,
            updated_at=entry.updated_at or entry.timestamp,
            source=entry.source,
            supersedes_id=entry.supersedes_id,
        ).encode("utf-8")
    return (f"- [{entry.timestamp}] **{entry.category}** <!-- id:{entry.memory_id} -->: {entry.learning}\n").encode()


def _relevant_recent_workspace_memory_digest(store: DaytonaWorkspaceMemoryStore, *, request: str) -> str:
    """Compose scored relevant entries plus newest complete memory records."""
    fallback_result = store.read_tail(byte_budget=WORKSPACE_MEMORY_INJECTION_TAIL_BYTES)
    recent_lines = parse_workspace_memory_lines(fallback_result.content)
    recent_entries = [line.entry for line in recent_lines if line.entry is not None]
    # With one populated page, repeated matching composition costs one tail read
    # plus one indexed list read (2 mounted-agent calls); no stale query cache.
    fallback = "".join(line.raw for line in recent_lines if line.entry is not None)
    query = _injection_query(request)
    if not query:
        return fallback
    try:
        from fleet_rlm.files.memory_tools import search_workspace_memory_entries

        scored, _warnings = search_workspace_memory_entries(store, normalized_query=_injection_query(request))
    except Exception:
        return fallback
    if not scored:
        return fallback
    selected: list[bytes] = []
    seen: set[str] = set()
    used = 0
    for item in scored[:_INJECTION_RELEVANT_LIMIT]:
        entry = item.entry
        record = _canonical_memory_record(entry)
        if entry.memory_id in seen or used + len(record) > WORKSPACE_MEMORY_INJECTION_TAIL_BYTES:
            continue
        seen.add(entry.memory_id)
        selected.append(record)
        used += len(record)
    # Relevant scores order first; the latest canonical records then follow in
    # store order, deduplicated by stable id. Both segments are deterministic.
    for entry in recent_entries[-_INJECTION_RECENT_COUNT:]:
        record = _canonical_memory_record(entry)
        if entry.memory_id in seen or used + len(record) > WORKSPACE_MEMORY_INJECTION_TAIL_BYTES:
            continue
        seen.add(entry.memory_id)
        selected.append(record)
        used += len(record)
    return b"".join(selected).decode("utf-8") if selected else fallback


def read_workspace_memory_injection_digest(
    store: DaytonaWorkspaceMemoryStore,
    *,
    request: str = "",
) -> str:
    """Return the <= 4 KiB query-sensitive tolerant memory digest for one Turn.

    The digest contains whole canonical records only: relevant matches first,
    then newest active records, deduplicated by id. Search/storage failures
    degrade to the same recency-only digest already allowed by preparation.
    """
    # Query-sensitive composition is intentionally not cached.
    return _relevant_recent_workspace_memory_digest(store, request=request)


class DaytonaWorkspaceMemoryStore:
    """Use the mounted Workspace Volume's canonical ``memory/MEMORIES.md`` file only."""

    def __init__(
        self,
        sandbox: Any,
        *,
        volume_paths: VolumePaths,
        max_upload_bytes: int,
    ) -> None:
        if max_upload_bytes < 1:
            raise ValueError("Workspace Memory capacity must be positive")
        expected_file = volume_paths.memory_dir / _MEMORY_NAME
        if volume_paths.memory_file != expected_file:
            raise ValueError("Workspace Memory must use the configured volume root")
        self._sandbox = sandbox
        self._volume_root = as_posix(volume_paths.root)
        self._memory_file_parent = as_posix(expected_file.parent)
        self._max_file_bytes = int(max_upload_bytes)

    @property
    def volume_root_posix(self) -> str:
        return self._volume_root

    def read_tail(self, *, byte_budget: int) -> WorkspaceMemoryReadResult:
        if type(byte_budget) is not int or not 0 < byte_budget <= WORKSPACE_MEMORY_BYTE_BUDGET:
            raise WorkspaceMemoryStoreUnavailableError()
        self._ensure_migrated()
        try:
            payload = self._read_tail_payload(byte_budget=byte_budget)
            content, truncated, total_bytes, _remote_bytes = self._checked_tail_payload(
                payload,
                byte_budget=byte_budget,
            )
            lines = parse_workspace_memory_lines(content)
            filtered = "".join(line.raw for line in lines if line.entry is not None)
            return WorkspaceMemoryReadResult(
                content=filtered,
                truncated=truncated,
                bytes_returned=len(filtered.encode("utf-8")),
                byte_budget=byte_budget,
                total_bytes=total_bytes,
                warnings=count_workspace_memory_warnings(lines),
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
        if not data or len(data) > self._max_file_bytes - len(_HEADER_BYTES):
            raise WorkspaceMemoryStoreFullError()
        self._ensure_migrated()
        try:
            with _memory_file_parent_append_lock(self._memory_file_parent):
                payload = self._run(
                    root=self._memory_file_parent,
                    operation="memory_append",
                    max_bytes=self._max_file_bytes,
                    total_file_bytes=self._max_file_bytes,
                    content=data,
                )
            entry = payload.get("entry")
            if not isinstance(entry, dict):
                raise ValueError("invalid memory response")
            total_bytes = entry.get("byte_size")
            if type(total_bytes) is not int or not len(data) <= total_bytes <= self._max_file_bytes:
                raise ValueError("invalid memory response")
            invalidate_workspace_memory_digest(self._volume_root)
            return WorkspaceMemoryAppendResult(entry_bytes=len(data), total_bytes=total_bytes)
        except Exception as exc:
            if isinstance(exc, WorkspaceMemoryStoreFullError):
                raise
            if isinstance(exc, ValueError) and "maximum size" in str(exc):
                raise WorkspaceMemoryStoreFullError() from exc
            raise WorkspaceMemoryStoreUnavailableError() from exc

    def list_entries(
        self,
        *,
        after: str | None = None,
        limit: int,
        category: str | None = None,
    ) -> WorkspaceMemoryListResult:
        if type(limit) is not int or not 1 <= limit <= WORKSPACE_MEMORY_MAX_LIST_LIMIT:
            raise WorkspaceMemoryStoreUnavailableError()
        if after is not None:
            normalize_workspace_memory_id(after)
        if category is not None:
            category = normalize_workspace_memory_category(category)
        self._ensure_migrated()
        content = self._read_full_content()
        lines = parse_workspace_memory_lines(content)
        entries = [line.entry for line in lines if line.entry is not None]
        warnings = count_workspace_memory_warnings(lines)
        if len({entry.memory_id for entry in entries}) != len(entries):
            # A cursor must name one physical row. Human edits can still forge
            # duplicate ids, so fail closed rather than skip or target an
            # arbitrary row while paging or mutating.
            raise WorkspaceMemoryStoreUnavailableError()
        if after is not None:
            matches = [index for index, entry in enumerate(entries) if entry.memory_id == after]
            if not matches:
                raise WorkspaceMemoryEntryNotFoundError(after)
            entries = entries[matches[0] + 1 :]
        if category is not None:
            entries = [entry for entry in entries if entry.category == category]
        page = tuple(entries[:limit])
        truncated = len(entries) > limit
        next_cursor = page[-1].memory_id if truncated and page else None
        return WorkspaceMemoryListResult(
            entries=page,
            truncated=truncated,
            next_cursor=next_cursor,
            warnings=warnings,
        )

    def delete_entry(self, memory_id: str) -> bool:
        normalize_workspace_memory_id(memory_id)
        self._ensure_migrated()
        try:
            with _memory_file_parent_append_lock(self._memory_file_parent):
                self._run(
                    root=self._memory_file_parent,
                    operation="memory_delete",
                    allow_missing=False,
                    max_bytes=self._max_file_bytes,
                    total_file_bytes=self._max_file_bytes,
                    memory_id=memory_id,
                )
        except FileNotFoundError:
            return False
        except Exception as exc:
            if isinstance(exc, WorkspaceMemoryStoreUnavailableError):
                raise
            raise WorkspaceMemoryStoreUnavailableError() from exc
        invalidate_workspace_memory_digest(self._volume_root)
        return True

    def edit_entry(
        self,
        memory_id: str,
        key_learning: str,
        *,
        category: str | None = None,
    ) -> str:
        normalize_workspace_memory_id(memory_id)
        learning = normalize_workspace_memory_learning(key_learning)
        normalized_category = normalize_workspace_memory_category(category) if category is not None else None
        self._ensure_migrated()
        request = json.dumps(
            {"learning": learning, "category": normalized_category},
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            with _memory_file_parent_append_lock(self._memory_file_parent):
                payload = self._run(
                    root=self._memory_file_parent,
                    operation="memory_edit",
                    allow_missing=False,
                    max_bytes=self._max_file_bytes,
                    total_file_bytes=self._max_file_bytes,
                    memory_id=memory_id,
                    content=request,
                )
        except FileNotFoundError as exc:
            raise WorkspaceMemoryEntryNotFoundError(memory_id) from exc
        except ValueError as exc:
            if str(exc) == "workspace memory record is invalid":
                raise WorkspaceMemoryRecordError from exc
            raise WorkspaceMemoryStoreUnavailableError() from exc
        except Exception as exc:
            if isinstance(exc, WorkspaceMemoryStoreUnavailableError):
                raise
            raise WorkspaceMemoryStoreUnavailableError() from exc
        record = payload.get("record")
        if not isinstance(record, str):
            raise WorkspaceMemoryStoreUnavailableError()
        try:
            validate_workspace_memory_record(record)
            parsed = parse_workspace_memory_lines(record)
            if len(parsed) != 1 or parsed[0].entry is None or parsed[0].entry.memory_id != memory_id:
                raise WorkspaceMemoryRecordError
        except WorkspaceMemoryRecordError as exc:
            raise WorkspaceMemoryStoreUnavailableError() from exc
        invalidate_workspace_memory_digest(self._volume_root)
        return record

    # -- internals -------------------------------------------------------------

    def _ensure_migrated(self) -> None:
        """Migrate the legacy root store exactly once per process (never loses content)."""
        if _memory_dir_is_marked_migrated(self._memory_file_parent):
            return
        try:
            with _memory_file_parent_append_lock(self._memory_file_parent):
                if _memory_dir_is_marked_migrated(self._memory_file_parent):
                    return
                self._migrate_legacy_store()
                _mark_memory_dir_migrated(self._memory_file_parent)
        except Exception as exc:
            if isinstance(
                exc,
                (WorkspaceMemoryStoreUnavailableError, WorkspaceMemoryStoreFullError),
            ):
                raise
            raise WorkspaceMemoryStoreUnavailableError() from exc

    def _migrate_legacy_store(self) -> None:
        """Move a legacy root ``MEMORIES.md`` into ``memory/MEMORIES.md`` once.

        The legacy file is removed only after the new store (header + legacy
        bytes) is durably published; any conflict, unsafe type, undecodable
        content, or over-cap legacy file fails closed with both files intact.
        """
        try:
            legacy_stat = self._run(
                root=self._volume_root,
                operation="stat",
                relative=_MEMORY_NAME,
                allow_missing=True,
            )
        except FileNotFoundError:
            legacy_stat = {"entry": None}
        legacy_entry = legacy_stat.get("entry")
        if legacy_entry is None:
            return
        if (
            not isinstance(legacy_entry, dict)
            or legacy_entry.get("kind") != "file"
            or legacy_entry.get("is_regular_file") is False
        ):
            raise WorkspaceMemoryStoreUnavailableError()
        try:
            new_stat = self._run(
                root=self._volume_root,
                operation="stat",
                relative=f"{_MEMORY_DIR_NAME}/{_MEMORY_NAME}",
                allow_missing=True,
            )
            new_entry = new_stat.get("entry")
        except FileNotFoundError:
            new_entry = None
        if new_entry is not None:
            return
        # ``read`` (not ``tail_read``): migration must copy every byte of the
        # legacy body, including a torn final line; ``tail_read`` is record-
        # oriented and trims unterminated tails by design.
        legacy = self._run(
            root=self._volume_root,
            operation="read",
            relative=_MEMORY_NAME,
            max_bytes=max(self._max_file_bytes - len(_HEADER_BYTES), 1),
        )
        content = legacy.get("content")
        if not isinstance(content, str):
            raise WorkspaceMemoryStoreUnavailableError()
        body = content.encode("utf-8")
        if body and not body.endswith(b"\n"):
            body += b"\n"
        self._run(
            root=self._memory_file_parent,
            operation="write",
            relative=_MEMORY_NAME,
            overwrite=False,
            max_bytes=self._max_file_bytes,
            total_file_bytes=self._max_file_bytes,
            content=_HEADER_BYTES + body,
        )
        self._run(
            root=self._volume_root,
            operation="unlink",
            relative=_MEMORY_NAME,
        )
        invalidate_workspace_memory_digest(self._volume_root)

    def _read_full_content(self) -> str:
        """Read the entire store body (bounded by the file cap), untransformed."""
        try:
            payload = self._read_tail_payload(byte_budget=self._max_file_bytes)
            content, _truncated, _total_bytes, _remote_bytes = self._checked_tail_payload(
                payload,
                byte_budget=self._max_file_bytes,
            )
            # ``tail_read`` also marks a complete-cap file as truncated when it
            # drops an unterminated final line. The payload checker has already
            # rejected a true over-cap response, so retain the complete records
            # and let the tolerant parser skip that malformed tail.
            return content
        except Exception as exc:
            if isinstance(exc, WorkspaceMemoryStoreUnavailableError):
                raise
            raise WorkspaceMemoryStoreUnavailableError() from exc

    def _read_tail_payload(self, *, byte_budget: int) -> dict[str, object]:
        try:
            return self._run(
                root=self._memory_file_parent,
                operation="tail_read",
                max_bytes=byte_budget,
                total_file_bytes=self._max_file_bytes,
            )
        except FileNotFoundError:
            # A missing ``memory/`` root is an empty store (the agent's
            # allow_missing only covers a missing file below an existing root).
            return {"ok": True, "content": "", "truncated": False, "bytes_returned": 0, "total_bytes": 0}

    def _checked_tail_payload(
        self,
        payload: dict[str, object],
        *,
        byte_budget: int,
    ) -> tuple[str, bool, int, int]:
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
            or total_bytes > self._max_file_bytes
            or bytes_returned != len(content.encode("utf-8"))
        ):
            raise ValueError("invalid memory response")
        return content, truncated, total_bytes, bytes_returned

    def _run(
        self,
        *,
        root: str,
        operation: str,
        relative: str = _MEMORY_NAME,
        allow_missing: bool = True,
        max_bytes: int = 1,
        total_file_bytes: int = 1,
        overwrite: bool = False,
        content: bytes = b"",
        memory_id: str = "",
    ) -> dict[str, object]:
        try:
            return run_workspace_agent(
                self._sandbox,
                volume_root=self._volume_root,
                root=root,
                operation=operation,
                relative=relative,
                allow_missing=allow_missing,
                max_bytes=max_bytes,
                total_file_bytes=total_file_bytes,
                limit=0,
                overwrite=overwrite,
                content_b64=base64.b64encode(content).decode("ascii"),
                memory_id=memory_id,
            )
        except WorkspaceAgentStorageError as exc:
            raise WorkspaceMemoryStoreUnavailableError() from exc
