"""Daytona volume operations — readiness polling, layout, listing, and serialization."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import shutil
import sqlite3
import tempfile
import textwrap
import time as _time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from fleet_rlm.runtime.execution.storage_paths import mounted_storage_roots
from fleet_rlm.utils.async_compat import _run_async_compat, _run_sync_in_thread
from fleet_rlm.utils.volume_tree import entry_name, stable_tree_id

from .config import (
    build_daytona_client as _build_daytona_client,
)
from .config import (
    resolve_daytona_config,
)
from .errors import DaytonaDiagnosticError, VolumeNotReadyError


def _iter_scaffold_skill_markdown() -> Iterator[tuple[str, str]]:
    from fleet_rlm.skills.catalog import iter_scaffold_skill_markdown

    yield from iter_scaffold_skill_markdown()


def seed_system_skills(mounted_root: str) -> None:
    """Seed bundled scaffold skills into the volume's skills/system/ directory."""
    from fleet_rlm.skills.sync import seed_system_skills as _seed_system_skills

    _seed_system_skills(mounted_root)


def _run_remote_python(sandbox: Sandbox, code: str, *, error_prefix: str) -> None:
    """Run a small administrative Python snippet inside the Daytona sandbox."""
    from .session_runtime import _run_admin_code as _run_code

    _run_code(
        sandbox=sandbox,
        code=code,
        phase="sandbox_create",
        error_prefix=error_prefix,
    )


def _init_remote_memory_db(sandbox: Sandbox, mounted_root: str) -> None:
    code = memory_db_bootstrap_script(mounted_root)
    _run_remote_python(sandbox, code, error_prefix="Daytona memory DB init failure")


def seed_remote_system_skills(sandbox: Sandbox, mounted_root: str) -> None:
    """Seed bundled scaffold skills into a remote Daytona volume."""

    dest_dir = PurePosixPath(mounted_root) / "skills" / "system"
    try:
        entries = _run_async_compat(sandbox.fs.list_files, str(dest_dir))
    except Exception:
        entries = []
    existing = {entry_name(str(getattr(entry, "name", "") or getattr(entry, "path", "") or entry)) for entry in entries}
    for skill_name, instructions in _iter_scaffold_skill_markdown():
        dest_name = f"{skill_name}.md"
        if dest_name in existing:
            continue
        _run_async_compat(
            sandbox.fs.upload_file,
            instructions.encode("utf-8"),
            str(dest_dir / dest_name),
        )
        logger.debug("seed_remote_system_skills: seeded %s", skill_name)


if TYPE_CHECKING:
    from daytona import Daytona, Sandbox

logger = logging.getLogger(__name__)

DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH = PurePosixPath("/home/daytona/memory")

_REMOTE_DIRECTORY_MODE = "755"
_VOLUME_READY_STATES = frozenset({"ready"})
_VOLUME_ERROR_STATES = frozenset({"error", "failed", "deleted"})

# Canonical VFS roots — the only first-level path components allowed in volume
# tree and file operations. Any other component is an authorization error.
VFS_CANONICAL_ROOTS: frozenset[str] = frozenset(
    {
        "/memory",
        "/artifacts",
        "/buffers",
        "/meta",
        "/memories",
        "/knowledge",
        "/skills",
        "/sessions",
        "/logs",
        "/uploads",
    }
)


def ensure_remote_directory(fs: Any, remote_path: PurePosixPath) -> None:
    """Ensure a remote Daytona directory exists."""
    directory = str(remote_path)
    if directory and directory not in {".", "/"}:
        fs.create_folder(directory, _REMOTE_DIRECTORY_MODE)


async def aensure_remote_directory(fs: Any, remote_path: PurePosixPath) -> None:
    """Async wrapper — runs blocking SDK call in a thread."""
    await _run_sync_in_thread(ensure_remote_directory, fs, remote_path)


def canonicalize_volume_state_token(value: Any) -> str:
    """Normalize raw Daytona SDK volume state values."""
    candidates: list[str] = []

    if isinstance(value, str):
        candidates.append(value)
    else:
        state_value = getattr(value, "value", None)
        if state_value not in (None, ""):
            candidates.append(str(state_value))
        state_name = getattr(value, "name", None)
        if state_name not in (None, ""):
            candidates.append(str(state_name))
        if value not in (None, ""):
            candidates.append(str(value))

    for candidate in candidates:
        normalized = candidate.strip().lower()
        if not normalized:
            continue
        normalized = normalized.replace("-", "_").replace(" ", "_")
        if "." in normalized:
            normalized = normalized.rsplit(".", 1)[-1]
        if normalized:
            return normalized
    return ""


def volume_state_details(volume: Any) -> tuple[str, str]:
    """Return raw and normalized SDK volume state strings."""
    raw_state_value = getattr(volume, "state", None)
    raw_state = str(raw_state_value or "").strip()
    normalized_state = canonicalize_volume_state_token(raw_state_value)
    return raw_state, normalized_state


def volume_state_missing(volume: Any, *, raw_state: str, normalized_state: str) -> bool:
    """Return whether the SDK response omitted a usable state token."""
    if raw_state or normalized_state:
        return False
    return bool(getattr(volume, "id", None))


def raise_if_volume_error(
    volume_name: str,
    *,
    raw_state: str,
    normalized_state: str,
) -> None:
    """Raise diagnostics when a volume is in a terminal error state."""
    if normalized_state in _VOLUME_ERROR_STATES:
        message = f"Volume '{volume_name}' is in error state '{normalized_state}'"
        if raw_state and raw_state != normalized_state:
            message = f"Volume '{volume_name}' is in error state '{normalized_state}' (raw='{raw_state}')"
        raise DaytonaDiagnosticError(
            message,
            category="sandbox_create_clone_error",
            phase="sandbox_create",
        )


def await_volume_ready(
    client: Daytona,
    volume_name: str,
    volume: Any,
    *,
    timeout: float = 60.0,
) -> Any:
    """Poll until a Daytona volume reaches ready state."""
    raw_state, state = volume_state_details(volume)

    if volume_state_missing(volume, raw_state=raw_state, normalized_state=state):
        return volume
    if state in _VOLUME_READY_STATES:
        return volume
    raise_if_volume_error(
        volume_name,
        raw_state=raw_state,
        normalized_state=state,
    )

    deadline = _time.monotonic() + timeout
    interval = 1.0

    while _time.monotonic() < deadline:
        logger.debug(
            (
                "Volume '%s' not ready "
                "(raw_state=%s, normalized_state=%s, state_type=%s, state_repr=%r), "
                "polling in %.1fs"
            ),
            volume_name,
            raw_state or "<empty>",
            state or "<empty>",
            type(getattr(volume, "state", None)).__name__,
            getattr(volume, "state", None),
            interval,
        )
        _time.sleep(interval)
        interval = min(interval * 2, 10.0)

        volume = client.volume.get(volume_name)
        raw_state, state = volume_state_details(volume)

        if volume_state_missing(volume, raw_state=raw_state, normalized_state=state):
            return volume
        if state in _VOLUME_READY_STATES:
            return volume
        raise_if_volume_error(
            volume_name,
            raw_state=raw_state,
            normalized_state=state,
        )

    raise VolumeNotReadyError(
        volume_name=volume_name,
        volume_state=state or raw_state or "unknown",
        raw_volume_state=raw_state or None,
        timeout_seconds=timeout,
    )


def ensure_daytona_volume_layout(
    *,
    sandbox: Sandbox,
    mounted_root: str = str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
) -> None:
    """Ensure canonical durable directories exist on a mounted Daytona volume."""
    roots = mounted_storage_roots(mounted_root)
    try:
        for path in (
            roots.memory_root,
            roots.artifacts_root,
            roots.buffers_root,
            roots.meta_root,
            roots.memories_root,
            PurePosixPath(roots.knowledge_root) / "ingested",
            PurePosixPath(roots.knowledge_root) / "summaries",
            PurePosixPath(roots.skills_root) / "system",
            PurePosixPath(roots.skills_root) / "user",
            roots.sessions_root,
            roots.logs_root,
            roots.uploads_root,
        ):
            ensure_remote_directory(sandbox.fs, PurePosixPath(path))
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona volume layout create failure: {exc}",
            category="sandbox_create_clone_error",
            phase="sandbox_create",
        ) from exc

    # Branch on APP_ENV (or explicit override) instead of host filesystem
    # existence: checking ``Path(mounted_root).exists()`` is a leaky heuristic
    # that silently changes behavior if a host happens to have
    # ``/home/daytona/memory``. Local dev seeds via the host-mounted path when
    # present; cloud (or force-remote) initializes the memory DB and skills
    # inside the sandbox via the SDK.
    use_local_path = (
        os.getenv("APP_ENV", "local").strip().lower() == "local"
        and os.getenv("FLEET_VOLUME_LAYOUT_LOCAL", "").strip() != "1"
    )
    if use_local_path and Path(mounted_root).exists():
        try:
            init_memory_db(mounted_root)
        except Exception as exc:
            logger.warning("ensure_daytona_volume_layout: core.db init failed (non-fatal): %s", exc)

        try:
            seed_system_skills(mounted_root)
        except Exception as exc:
            logger.warning("ensure_daytona_volume_layout: skill seeding failed (non-fatal): %s", exc)
        return

    try:
        _init_remote_memory_db(sandbox, mounted_root)
    except Exception as exc:
        logger.warning("ensure_daytona_volume_layout: remote core.db init failed (non-fatal): %s", exc)

    try:
        seed_remote_system_skills(sandbox, mounted_root)
    except Exception as exc:
        logger.warning("ensure_daytona_volume_layout: remote skill seeding failed (non-fatal): %s", exc)


async def aensure_daytona_volume_layout(
    *,
    sandbox: Sandbox,
    mounted_root: str = str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
) -> None:
    """Async wrapper — runs blocking volume layout setup in a thread."""
    await _run_sync_in_thread(ensure_daytona_volume_layout, sandbox=sandbox, mounted_root=mounted_root)


@contextmanager
def _mounted_daytona_volume(volume_name: str) -> Iterator[Any]:
    from daytona import CreateSandboxFromSnapshotParams, VolumeMount

    client = _build_daytona_client(resolve_daytona_config())
    volume = client.volume.get(volume_name, create=True)
    volume = await_volume_ready(client, volume_name, volume)
    sandbox = client.create(
        CreateSandboxFromSnapshotParams(
            language="python",
            volumes=[
                VolumeMount(
                    volume_id=volume.id,
                    mount_path=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
                )
            ],
        )
    )
    ensure_daytona_volume_layout(
        sandbox=sandbox,
        mounted_root=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
    )
    try:
        yield sandbox
    finally:
        with suppress(Exception):
            sandbox.delete()
        with suppress(Exception):
            client.close()


def _serialize_daytona_volume(volume: Any) -> dict[str, Any]:
    state = getattr(volume, "state", None)
    state_str = ""
    if state is not None:
        if hasattr(state, "name"):
            state_str = str(state.name)
        elif hasattr(state, "value"):
            state_str = str(state.value)
        else:
            state_str = str(state)

    created_at = getattr(volume, "created_at", None)
    created_at_value = (
        created_at.isoformat()
        if hasattr(created_at, "isoformat")
        else str(created_at)
        if created_at is not None
        else None
    )
    return {
        "id": str(getattr(volume, "id", "") or ""),
        "name": str(getattr(volume, "name", "") or ""),
        "state": state_str,
        "created_at": created_at_value,
    }


def list_daytona_volumes(*, limit: int = 100) -> list[dict[str, Any]]:
    """List Daytona persistent volumes.

    The installed SDK ``VolumeService.list()`` takes no parameters and
    returns ``list[Volume]``.
    """
    client = _build_daytona_client(resolve_daytona_config())
    try:
        volumes = client.volume.list()
    finally:
        with suppress(Exception):
            client.close()
    return [_serialize_daytona_volume(volume) for volume in volumes]


async def alist_daytona_volumes(*, limit: int = 100) -> list[dict[str, Any]]:
    return await _run_sync_in_thread(list_daytona_volumes, limit=limit)


# =========================================================================
# Volume file-browser operations (merged from file_browser.py)
# =========================================================================


# Byte threshold above which content is considered binary (non-text).
# Determined by scanning the first 8 KiB for NUL bytes or a high ratio of
# non-printable, non-whitespace bytes.
_BINARY_SAMPLE_BYTES = 8192
_BINARY_NUL_THRESHOLD = 1  # any NUL byte → binary
_BINARY_NONTEXT_RATIO = 0.30  # >30 % non-text bytes → binary


def _detect_binary_content(data: bytes) -> bool:
    """Return True when *data* appears to be non-text binary content."""
    sample = data[:_BINARY_SAMPLE_BYTES]
    if not sample:
        return False
    if sample.count(0) >= _BINARY_NUL_THRESHOLD:
        return True
    non_text = sum(1 for byte in sample if byte < 0x09 or (0x0E <= byte <= 0x1F and byte != 0x1B))
    return non_text / len(sample) > _BINARY_NONTEXT_RATIO


def _check_vfs_root_allowed(display_path: str) -> None:
    """Raise ValueError when *display_path* is outside the canonical VFS roots."""
    pure = PurePosixPath(display_path)
    if pure == PurePosixPath("/"):
        return  # root listing is allowed; callers filter children themselves
    parts = pure.parts
    if len(parts) < 2:
        return
    root = f"/{parts[1]}"
    if root not in VFS_CANONICAL_ROOTS:
        raise ValueError(
            f"Volume path outside canonical roots: {display_path!r}. Allowed roots: {sorted(VFS_CANONICAL_ROOTS)}"
        )


def _is_allowed_root_child(parent_display_path: str, child_name: str) -> bool:
    """Return whether a direct child should be visible from the VFS root."""
    if PurePosixPath(parent_display_path) != PurePosixPath("/"):
        return True
    return str(PurePosixPath("/") / child_name) in VFS_CANONICAL_ROOTS


@dataclass(frozen=True)
class _ResolvedDaytonaPath:
    display_path: str
    mounted_path: PurePosixPath


def _resolve_daytona_path(
    path: str,
    *,
    default_path: str = "/",
    check_root: bool = False,
) -> _ResolvedDaytonaPath:
    candidate = (path or default_path).strip() or default_path

    # Reject URL-encoded traversal sequences before path parsing.
    # Covers %2e%2e, %2E%2E, mixed-case, and slash variants.
    lowered = candidate.lower()
    if "%2e%2e" in lowered or "%2f" in lowered or "%5c" in lowered:
        raise ValueError(f"Path traversal not allowed: {candidate!r}")

    pure_path = PurePosixPath("/", candidate.lstrip("/"))
    if ".." in pure_path.parts:
        raise ValueError(f"Path traversal not allowed: {candidate!r}")

    if check_root:
        _check_vfs_root_allowed(str(pure_path))

    mounted_path = DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH.joinpath(*pure_path.parts[1:])
    return _ResolvedDaytonaPath(
        display_path=str(pure_path),
        mounted_path=mounted_path,
    )


def _child_daytona_path(
    parent: _ResolvedDaytonaPath,
    name: str,
) -> _ResolvedDaytonaPath:
    return _ResolvedDaytonaPath(
        display_path=str(PurePosixPath(parent.display_path) / name),
        mounted_path=parent.mounted_path / name,
    )


def _entry_modified_iso(entry: Any) -> str | None:
    mod_time = getattr(entry, "mod_time", None)
    if hasattr(mod_time, "isoformat"):
        return mod_time.isoformat()
    if mod_time is None:
        return None
    return str(mod_time)


def list_daytona_volume_tree(
    volume_name: str,
    root_path: str = "/",
    max_depth: int = 4,
    max_entries: int = 200,
) -> dict[str, Any]:
    """Adapt Daytona sandbox.fs listings to the runtime volume tree schema."""
    max_depth = max(1, min(max_depth, 10))
    max_entries = max(1, min(max_entries, 1000))
    root = _resolve_daytona_path(root_path, default_path="/", check_root=True)

    counters: dict[str, int] = {"files": 0, "dirs": 0}
    truncated = False
    entries_returned = 0

    def _walk(
        sandbox: Any,
        location: _ResolvedDaytonaPath,
        depth: int,
    ) -> list[dict[str, Any]]:
        nonlocal entries_returned, truncated
        nodes: list[dict[str, Any]] = []
        entries = sandbox.fs.list_files(str(location.mounted_path))

        for entry in entries:
            if entries_returned >= max_entries:
                truncated = True
                break
            name = entry_name(getattr(entry, "name", "") or getattr(entry, "path", ""))
            if not name:
                continue
            if not _is_allowed_root_child(location.display_path, name):
                continue

            child = _child_daytona_path(location, name)
            is_dir = bool(getattr(entry, "is_dir", False))
            modified_iso = _entry_modified_iso(entry)
            entries_returned += 1

            if is_dir:
                counters["dirs"] += 1
                children: list[dict[str, Any]] = []
                if depth + 1 < max_depth:
                    children = _walk(sandbox, child, depth + 1)
                else:
                    truncated = True
                nodes.append(
                    {
                        "id": stable_tree_id(child.display_path),
                        "name": name,
                        "path": child.display_path,
                        "type": "directory",
                        "children": children,
                        "modified_at": modified_iso,
                    }
                )
                continue

            counters["files"] += 1
            nodes.append(
                {
                    "id": stable_tree_id(child.display_path),
                    "name": name,
                    "path": child.display_path,
                    "type": "file",
                    "size": getattr(entry, "size", None),
                    "modified_at": modified_iso,
                }
            )
        return nodes

    with _mounted_daytona_volume(volume_name) as sandbox:
        children = _walk(sandbox, root, 0)

    root_node: dict[str, Any] = {
        "id": stable_tree_id(f"daytona-volume:{volume_name}:{root.display_path}"),
        "name": volume_name,
        "path": root.display_path,
        "type": "volume",
        "children": children,
    }
    return {
        "volume_name": volume_name,
        "root_path": root.display_path,
        "allowed_roots": sorted(VFS_CANONICAL_ROOTS),
        "nodes": [root_node],
        "total_files": counters["files"],
        "total_dirs": counters["dirs"],
        "truncated": truncated,
        "max_depth": max_depth,
        "max_entries": max_entries,
        "entries_returned": entries_returned,
    }


async def alist_daytona_volume_tree(
    volume_name: str,
    root_path: str = "/",
    max_depth: int = 4,
    max_entries: int = 200,
) -> dict[str, Any]:
    if max_entries == 200:
        return await _run_sync_in_thread(
            list_daytona_volume_tree,
            volume_name,
            root_path,
            max_depth,
        )
    return await _run_sync_in_thread(
        list_daytona_volume_tree,
        volume_name,
        root_path,
        max_depth,
        max_entries,
    )


def read_daytona_volume_file_text(
    volume_name: str,
    path: str,
    max_bytes: int = 200_000,
) -> dict[str, Any]:
    """Adapt Daytona sandbox.fs file downloads to the runtime preview schema."""
    if not path:
        raise ValueError("path is required")

    max_bytes = max(1, min(max_bytes, 1_000_000))
    resolved_path = _resolve_daytona_path(path, check_root=True)

    with _mounted_daytona_volume(volume_name) as sandbox:
        raw = sandbox.fs.download_file(str(resolved_path.mounted_path))

    raw_bytes = b"" if raw is None else raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    size = len(raw_bytes)
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    mime = mimetypes.guess_type(resolved_path.display_path)[0] or "text/plain"

    # Detect binary content; return a hash-only payload for non-text files.
    if _detect_binary_content(raw_bytes):
        return {
            "path": resolved_path.display_path,
            "mime": mime,
            "size": size,
            "sha256": sha256,
            "encoding": "binary",
            "content": "",
            "binary": True,
            "truncated": False,
        }

    truncated = size > max_bytes
    preview_bytes = raw_bytes[:max_bytes] if truncated else raw_bytes
    decoded = preview_bytes.decode("utf-8", errors="replace")
    encoding = "utf-8-lossy" if "\ufffd" in decoded else "utf-8"

    return {
        "path": resolved_path.display_path,
        "mime": mime,
        "size": size,
        "sha256": sha256,
        "encoding": encoding,
        "content": decoded,
        "binary": False,
        "truncated": truncated,
    }


async def aread_daytona_volume_file_text(
    volume_name: str,
    path: str,
    max_bytes: int = 200_000,
) -> dict[str, Any]:
    return await _run_sync_in_thread(
        read_daytona_volume_file_text,
        volume_name,
        path,
        max_bytes,
    )


# =========================================================================
# SQLite memory DB (merged from memory_db.py)
# =========================================================================


MEMORY_SCHEMA_VERSION = 2

_MEMORY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS memory (
    key                TEXT PRIMARY KEY,
    value              TEXT NOT NULL,
    scope              TEXT NOT NULL DEFAULT 'core',
    writer_agent_depth INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_MEMORY_METADATA_COLUMNS: dict[str, str] = {
    "scope": "scope TEXT NOT NULL DEFAULT 'core'",
    "writer_agent_depth": "writer_agent_depth INTEGER NOT NULL DEFAULT 0",
    "created_at": "created_at TEXT NOT NULL DEFAULT ''",
    "updated_at": "updated_at TEXT NOT NULL DEFAULT ''",
}

_MIGRATION_DESCRIPTIONS: dict[int, str] = {
    1: "create memory key/value table",
    2: "add memory metadata columns and indexes",
}


def configure_memory_connection(conn: sqlite3.Connection) -> None:
    """Apply SQLite runtime settings that are safe on Daytona-mounted volumes."""
    conn.execute("PRAGMA busy_timeout = 5000")


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _ensure_memory_columns(conn: sqlite3.Connection) -> None:
    existing = _table_columns(conn, "memory")
    for column, ddl in _MEMORY_METADATA_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE memory ADD COLUMN {ddl}")
            existing.add(column)
    conn.execute(
        """
        UPDATE memory
        SET updated_at = COALESCE(NULLIF(updated_at, ''), NULLIF(created_at, ''), CURRENT_TIMESTAMP)
        WHERE updated_at = ''
        """
    )


def apply_memory_migrations(conn: sqlite3.Connection) -> None:
    """Apply all idempotent memory DB migrations to an open connection."""
    configure_memory_connection(conn)
    conn.execute(_SCHEMA_MIGRATIONS_DDL)
    conn.execute(_MEMORY_TABLE_DDL)
    _ensure_memory_columns(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory(scope)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_updated_at ON memory(updated_at)")
    conn.execute(f"PRAGMA user_version = {MEMORY_SCHEMA_VERSION}")
    for version, description in _MIGRATION_DESCRIPTIONS.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO schema_migrations(version, description)
            VALUES (?, ?)
            """,
            (version, description),
        )
    conn.commit()


def init_memory_db(volume_mount_path: str) -> None:
    """Ensure ``memories/core.db`` exists with the canonical schema."""
    memories_dir = Path(volume_mount_path) / "memories"
    if not memories_dir.exists():
        logger.warning("memory_db: memories directory not found at %s", memories_dir)
        return

    db_path = memories_dir / "core.db"
    try:
        with tempfile.TemporaryDirectory(prefix="fleet-rlm-memory-db-") as tmp_dir_name:
            tmp_db = Path(tmp_dir_name) / "core.db"
            if db_path.exists():
                shutil.copyfile(db_path, tmp_db)

            conn = sqlite3.connect(str(tmp_db))
            try:
                apply_memory_migrations(conn)
            finally:
                conn.close()

            shutil.copyfile(tmp_db, db_path)
        logger.info("memory_db: initialized/migrated core.db at %s", db_path)
    except Exception as exc:
        logger.warning("memory_db: could not initialize core.db at %s: %s", db_path, exc)


def memory_db_bootstrap_script(mounted_root: str) -> str:
    """Return a remote Python bootstrap script using the canonical migrations."""
    return textwrap.dedent(
        f"""
        from pathlib import Path
        import shutil
        import sqlite3

        MEMORY_SCHEMA_VERSION = {MEMORY_SCHEMA_VERSION}
        MEMORY_TABLE_DDL = {_MEMORY_TABLE_DDL!r}
        SCHEMA_MIGRATIONS_DDL = {_SCHEMA_MIGRATIONS_DDL!r}
        MEMORY_METADATA_COLUMNS = {_MEMORY_METADATA_COLUMNS!r}
        MIGRATION_DESCRIPTIONS = {_MIGRATION_DESCRIPTIONS!r}

        def table_columns(conn, table_name):
            return {{str(row[1]) for row in conn.execute(f"PRAGMA table_info({{table_name}})").fetchall()}}

        def apply_migrations(conn):
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute(SCHEMA_MIGRATIONS_DDL)
            conn.execute(MEMORY_TABLE_DDL)
            existing = table_columns(conn, "memory")
            for column, ddl in MEMORY_METADATA_COLUMNS.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE memory ADD COLUMN {{ddl}}")
                    existing.add(column)
            conn.execute(
                '''
                UPDATE memory
                SET updated_at = COALESCE(NULLIF(updated_at, ''), NULLIF(created_at, ''), CURRENT_TIMESTAMP)
                WHERE updated_at = ''
                '''
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory(scope)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_updated_at ON memory(updated_at)")
            conn.execute(f"PRAGMA user_version = {{MEMORY_SCHEMA_VERSION}}")
            for version, description in MIGRATION_DESCRIPTIONS.items():
                conn.execute(
                    '''
                    INSERT OR IGNORE INTO schema_migrations(version, description)
                    VALUES (?, ?)
                    ''',
                    (version, description),
                )
            conn.commit()

        root = Path({mounted_root!r})
        memories_dir = root / "memories"
        memories_dir.mkdir(parents=True, exist_ok=True)
        db_path = memories_dir / "core.db"
        tmp_dir = Path("/tmp/fleet-rlm-memory-bootstrap")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_db = tmp_dir / "core.db"
        if db_path.exists():
            shutil.copyfile(db_path, tmp_db)
        elif tmp_db.exists():
            tmp_db.unlink()
        conn = sqlite3.connect(str(tmp_db))
        try:
            apply_migrations(conn)
        finally:
            conn.close()
        shutil.copyfile(tmp_db, db_path)
        """
    )


async def ainit_memory_db(volume_mount_path: str) -> None:
    """Async wrapper around :func:`init_memory_db` (runs in a thread)."""
    import asyncio

    await asyncio.to_thread(init_memory_db, volume_mount_path)


__all__ = [
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    "VFS_CANONICAL_ROOTS",
    "aensure_daytona_volume_layout",
    "aensure_remote_directory",
    "alist_daytona_volumes",
    "await_volume_ready",
    "canonicalize_volume_state_token",
    "raise_if_volume_error",
    "seed_remote_system_skills",
    "seed_system_skills",
    "volume_state_details",
    "volume_state_missing",
    # From file_browser merge
    "alist_daytona_volume_tree",
    "aread_daytona_volume_file_text",
    "list_daytona_volume_tree",
    "read_daytona_volume_file_text",
    # From memory_db merge
    "MEMORY_SCHEMA_VERSION",
    "ainit_memory_db",
    "apply_memory_migrations",
    "configure_memory_connection",
    "init_memory_db",
    "memory_db_bootstrap_script",
]
