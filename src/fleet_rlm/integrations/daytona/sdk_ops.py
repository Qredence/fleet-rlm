"""Daytona SDK operations layer.

Combines volume CRUD, snapshot CRUD, and sandbox get/fork/resume into a single
thin SDK operations module.  Previously these lived in three separate files:
``volume_runtime``, ``snapshot_runtime``, and ``sandbox_lifecycle``.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Volume operations  (was: volume_runtime.py)
# ---------------------------------------------------------------------------
import dataclasses
import hashlib
import logging
import mimetypes
import re
import shlex
import time
import time as _time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from fleet_rlm.runtime.execution.storage_paths import mounted_storage_roots
from fleet_rlm.utils.volume_tree import entry_name, stable_tree_id

from .async_compat import _run_sync_in_thread
from .config import ResolvedDaytonaConfig, resolve_daytona_config
from .config import (
    build_daytona_client as _build_daytona_client,
)
from .config import (
    classify_daytona_sdk_error as _classify_daytona_sdk_error,
)
from .config import (
    daytona_import_error as _daytona_import_error,
)
from .config import format_daytona_sdk_error as _format_daytona_sdk_error
from .errors import DaytonaDiagnosticError, VolumeNotReadyError
from .memory_db import init_memory_db
from .models import SandboxSpec

logger = logging.getLogger(__name__)

DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH = PurePosixPath("/home/daytona/memory")

_REMOTE_DIRECTORY_MODE = "755"
_VOLUME_READY_STATES = frozenset({"ready"})
_VOLUME_ERROR_STATES = frozenset({"error", "failed", "deleted"})

# Canonical VFS roots — the only first-level path components allowed in volume
# tree and file operations.  Any other component is an authorization error.
VFS_CANONICAL_ROOTS: frozenset[str] = frozenset({"/memory", "/artifacts", "/buffers", "/meta"})

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


def ensure_remote_directory(fs: Any, remote_path: PurePosixPath) -> None:
    """Ensure a remote Daytona directory exists."""
    directory = str(remote_path)
    if directory and directory not in {".", "/"}:
        fs.create_folder(directory, _REMOTE_DIRECTORY_MODE)


aensure_remote_directory = ensure_remote_directory


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
    client: Any,
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
    sandbox: Any,
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

    try:
        init_memory_db(mounted_root)
    except Exception as exc:
        logger.warning("ensure_daytona_volume_layout: core.db init failed (non-fatal): %s", exc)


aensure_daytona_volume_layout = ensure_daytona_volume_layout


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
    """List Daytona persistent volumes with pagination support.

    Uses cursor-based pagination (Daytona 0.180+) when available,
    falling back to unbounded listing for older runners.
    """
    client = _build_daytona_client(resolve_daytona_config())
    try:
        try:
            all_volumes: list[Any] = []
            page = 1
            while True:
                result = client.volume.list(page=page, limit=limit)
                items = getattr(result, "items", result) if result else []
                if not items:
                    break
                all_volumes.extend(items)
                if len(items) < limit:
                    break
                page += 1
            volumes = all_volumes
        except TypeError:
            volumes = client.volume.list()
    finally:
        with suppress(Exception):
            client.close()
    return [_serialize_daytona_volume(volume) for volume in volumes]


async def alist_daytona_volumes(*, limit: int = 100) -> list[dict[str, Any]]:
    return await _run_sync_in_thread(list_daytona_volumes, limit=limit)


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
        "allowed_roots": ["/memory", "/artifacts", "/buffers", "/meta"],
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
    """Adapt Daytona sandbox.fs file downloads to the runtime preview schema.

    Returns a dict with:
    - path, mime, size, sha256, encoding, content, truncated
    - encoding is "utf-8" for clean text, "utf-8-lossy" when UTF-8 decoding
      introduced replacement characters, or "binary" for non-text files.
    - For binary files, content is "" and binary=True is set.
    """
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


# ---------------------------------------------------------------------------
# Snapshot operations  (was: snapshot_runtime.py)
# ---------------------------------------------------------------------------

DEFAULT_SNAPSHOT_PACKAGES: list[str] = [
    "dspy-ai",
    "numpy",
    "pandas",
    "httpx",
    "pydantic",
]

DEFAULT_SNAPSHOT_NAME = "fleet-rlm-base"
DEFAULT_SNAPSHOT_BASE_IMAGE = "python:3.12-slim"
_VALID_PACKAGE_SPEC_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\],<>=!~]*$")


def _snapshot_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "name": snapshot.name,
        "id": snapshot.id,
        "state": str(getattr(snapshot, "state", "unknown")),
        "image_name": getattr(snapshot, "image_name", None),
    }


def build_base_snapshot_image(
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    packages: list[str] | None = None,
) -> Any:
    """Build the default Daytona declarative image used by snapshots and fallback sandboxes."""
    try:
        from daytona import Image as DaytonaImage
    except ImportError as exc:  # pragma: no cover - environment specific
        raise _daytona_import_error(exc) from exc

    packages_to_install = packages if packages is not None else DEFAULT_SNAPSHOT_PACKAGES
    for package in packages_to_install:
        if not package or not _VALID_PACKAGE_SPEC_PATTERN.fullmatch(package):
            msg = f"Invalid package spec for snapshot image install: {package!r}"
            raise ValueError(msg)

    image = DaytonaImage.base(base_image).run_commands("pip install uv")
    if packages_to_install:
        install_command = shlex.join(["uv", "pip", "install", "--system", *packages_to_install])
        image = image.run_commands(install_command)
    return image


def list_snapshots(
    config: ResolvedDaytonaConfig | None = None,
) -> list[dict[str, Any]]:
    """Return a lightweight list of available snapshots."""
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        result = client.snapshot.list()
        items = result.items if hasattr(result, "items") else result
        return [_snapshot_summary(snapshot) for snapshot in items]
    finally:
        with suppress(Exception):
            client.close()


alist_snapshots = list_snapshots


def get_snapshot(
    name: str,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> dict[str, Any] | None:
    """Look up a snapshot by *name*, returning a summary dict or ``None``."""
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snapshot = client.snapshot.get(name)
        return _snapshot_summary(snapshot)
    except Exception as exc:
        if _snapshot_lookup_missing(exc):
            logger.debug("snapshot_lookup_missing", extra={"name": name}, exc_info=True)
            return None
        raise DaytonaDiagnosticError(
            f"Daytona snapshot lookup failure: {_format_daytona_sdk_error(exc)}",
            category="sandbox_snapshot_error",
            phase="snapshot_lookup",
        ) from exc
    finally:
        with suppress(Exception):
            client.close()


aget_snapshot = get_snapshot


def _snapshot_lookup_missing(exc: BaseException) -> bool:
    classification = _classify_daytona_sdk_error(exc)
    if classification.status_code == 404:
        return True
    lowered = classification.message.lower()
    return "snapshot" in lowered and "not found" in lowered


def _snapshot_create_conflict(exc: BaseException) -> bool:
    classification = _classify_daytona_sdk_error(exc)
    lowered = classification.message.lower()
    return classification.status_code in {400, 409} and any(
        token in lowered for token in ("already exists", "conflict", "duplicate")
    )


def _wait_for_snapshot_refresh_target(
    name: str,
    *,
    previous_snapshot_id: str | None,
    config: ResolvedDaytonaConfig,
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """Wait for a refreshed snapshot name to disappear or be replaced."""

    deadline = _time.monotonic() + timeout
    interval = 0.1

    while _time.monotonic() < deadline:
        current = get_snapshot(name, config=config)
        if current is None:
            return None
        current_id = str(current.get("id") or "") or None
        if previous_snapshot_id is None or current_id != previous_snapshot_id:
            return current
        _time.sleep(interval)
        interval = min(interval * 2, 1.0)

    raise DaytonaDiagnosticError(
        f"Timed out waiting for Daytona snapshot '{name}' to finish refreshing",
        category="sandbox_snapshot_error",
        phase="snapshot_refresh",
    )


def create_snapshot(
    name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    packages: list[str] | None = None,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Create a new Daytona snapshot with pre-installed packages."""
    try:
        from daytona.common.snapshot import CreateSnapshotParams
    except ImportError as exc:  # pragma: no cover - environment specific
        raise _daytona_import_error(exc) from exc

    image = build_base_snapshot_image(base_image=base_image, packages=packages)
    params = CreateSnapshotParams(name=name, image=image)
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snapshot = client.snapshot.create(params, on_logs=on_logs, timeout=0)
        logger.info("Snapshot '%s' created (id=%s)", snapshot.name, snapshot.id)
        return _snapshot_summary(snapshot)
    finally:
        with suppress(Exception):
            client.close()


acreate_snapshot = create_snapshot


def delete_snapshot(
    name: str,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> None:
    """Delete a Daytona snapshot by name or id."""
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snapshot = client.snapshot.get(name)
        client.snapshot.delete(snapshot)
    finally:
        with suppress(Exception):
            client.close()


adelete_snapshot = delete_snapshot


def bootstrap_snapshot(
    name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    refresh: bool = False,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Ensure the reusable Fleet Daytona base snapshot exists."""
    cfg = config or resolve_daytona_config()
    existing = get_snapshot(name, config=cfg)
    if existing is not None and not refresh:
        return {**existing, "created": False, "refreshed": False}
    if existing is not None:
        previous_snapshot_id = str(existing.get("id") or "") or None
        delete_snapshot(previous_snapshot_id or str(existing.get("name") or name), config=cfg)
        replacement = _wait_for_snapshot_refresh_target(
            name,
            previous_snapshot_id=previous_snapshot_id,
            config=cfg,
        )
        if replacement is not None:
            return {**replacement, "created": False, "refreshed": True}

    try:
        created = create_snapshot(
            name=name,
            base_image=base_image,
            config=cfg,
            on_logs=on_logs,
        )
    except Exception as exc:
        if _snapshot_create_conflict(exc):
            replacement = get_snapshot(name, config=cfg)
            if replacement is not None:
                return {**replacement, "created": False, "refreshed": existing is not None}
        raise
    return {**created, "created": True, "refreshed": existing is not None}


abootstrap_snapshot = bootstrap_snapshot


def resolve_snapshot(
    preferred_name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> str | None:
    """Return the snapshot name if it exists and is ``ACTIVE``, else ``None``."""
    info = get_snapshot(preferred_name, config=config)
    state = str(info.get("state", "")).upper() if info else ""
    if info and state in ("ACTIVE", "SNAPSHOTSTATE.ACTIVE"):
        return info["name"]
    return None


aresolve_snapshot = resolve_snapshot


def resolve_sandbox_spec_snapshot(
    spec: SandboxSpec,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> SandboxSpec:
    """Resolve a snapshot-backed spec, falling back to the shared declarative image when needed."""
    if not spec.snapshot or spec.uses_declarative_image:
        return spec
    active_snapshot = resolve_snapshot(spec.snapshot, config=config)
    if active_snapshot is not None:
        return spec
    logger.info(
        "Snapshot '%s' not active; falling back to declarative image",
        spec.snapshot,
    )
    return fallback_to_declarative_image(spec)


aresolve_sandbox_spec_snapshot = resolve_sandbox_spec_snapshot


def resolve_default_snapshot(*, image: Any, snapshot: str | None) -> str | None:
    """Choose the runtime default snapshot when neither image nor snapshot is set."""
    if snapshot or image:
        return snapshot
    return DEFAULT_SNAPSHOT_NAME


def fallback_to_declarative_image(spec: SandboxSpec) -> SandboxSpec:
    """Replace a snapshot-based spec with a declarative image build."""
    image = build_base_snapshot_image()
    return dataclasses.replace(spec, image=image, snapshot=None)


def create_sandbox_snapshot(
    session: Any,
    *,
    name: str,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Create a snapshot from the current state of a sandbox session."""
    _experimental_call(
        session.sandbox,
        "_experimental_create_snapshot",
        name=name,
        timeout=timeout,
        category="sandbox_snapshot_error",
        phase="sandbox_snapshot",
    )
    return {
        "name": name,
        "sandbox_id": getattr(session, "sandbox_id", None),
        "status": "created",
    }


acreate_sandbox_snapshot = create_sandbox_snapshot


# ---------------------------------------------------------------------------
# Sandbox lifecycle operations  (was: sandbox_lifecycle.py)
# ---------------------------------------------------------------------------


def _experimental_call(
    sandbox: Any,
    method_name: str,
    *args: Any,
    category: str = "sandbox_experimental_error",
    phase: str = "sandbox_experimental",
    **kwargs: Any,
) -> Any:
    """Safely invoke an experimental Daytona SDK method on *sandbox*."""
    try:
        method = getattr(sandbox, method_name)
        return method(*args, **kwargs)
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona {method_name} failure: {exc}",
            category=category,
            phase=phase,
        ) from exc


def get_sandbox(
    *,
    runtime: Any,
    sandbox_id: str,
    recover: bool = True,
) -> Any:
    """Get an existing sandbox by ID, recovering from archive if needed."""
    try:
        client = runtime._get_client()
        sandbox = client.get(sandbox_id)
        if recover:
            state = getattr(sandbox, "state", None)
            state_value = getattr(state, "value", str(state or ""))
            if str(state_value).lower() in ("archived", "stopped"):
                sandbox.recover(timeout=60)
        return sandbox
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona sandbox resume failure: {_format_daytona_sdk_error(exc)}",
            category="sandbox_resume_error",
            phase="sandbox_resume",
        ) from exc


aget_sandbox = get_sandbox


def resume_workspace_session(
    *,
    runtime: Any,
    sandbox_id: str,
    repo_url: str | None,
    ref: str | None,
    volume_name: str | None = None,
    workspace_path: str,
    context_sources: list[Any] | None = None,
    context_id: str | None = None,
) -> Any:
    resumed_started = time.perf_counter()
    sandbox = get_sandbox(
        runtime=runtime,
        sandbox_id=sandbox_id,
    )
    session = runtime._build_workspace_session(
        sandbox=sandbox,
        repo_url=repo_url,
        resolved_ref=ref,
        volume_name=volume_name,
        workspace_path=workspace_path,
        context_sources=list(context_sources or []),
        timings={"sandbox_resume": int((time.perf_counter() - resumed_started) * 1000)},
        context_id=context_id,
    )
    if volume_name:
        ensure_daytona_volume_layout(
            sandbox=sandbox,
            mounted_root=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
        )
    return session


aresume_workspace_session = resume_workspace_session


def fork_sandbox(
    *,
    runtime: Any,
    session: Any,
    name: str | None = None,
    timeout: float = 60.0,
) -> Any:
    """Fork a sandbox session, creating a copy-on-write clone."""
    forked = _experimental_call(
        session.sandbox,
        "_experimental_fork",
        name=name,
        timeout=timeout,
        category="sandbox_fork_error",
        phase="sandbox_fork",
    )
    return runtime._build_workspace_session(
        sandbox=forked,
        repo_url=session.repo_url,
        resolved_ref=session.ref,
        volume_name=session.volume_name,
        workspace_path=session.workspace_path,
        context_sources=list(session.context_sources),
        timings={"sandbox_fork": 0},
    )


afork_sandbox = fork_sandbox


# ---------------------------------------------------------------------------
# Combined __all__
# ---------------------------------------------------------------------------

__all__ = [
    # Volume
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    "aensure_daytona_volume_layout",
    "aensure_remote_directory",
    "alist_daytona_volume_tree",
    "alist_daytona_volumes",
    "aread_daytona_volume_file_text",
    "await_volume_ready",
    "canonicalize_volume_state_token",
    "ensure_daytona_volume_layout",
    "ensure_remote_directory",
    "list_daytona_volume_tree",
    "list_daytona_volumes",
    "raise_if_volume_error",
    "read_daytona_volume_file_text",
    "volume_state_details",
    "volume_state_missing",
    # Snapshot
    "DEFAULT_SNAPSHOT_BASE_IMAGE",
    "DEFAULT_SNAPSHOT_NAME",
    "DEFAULT_SNAPSHOT_PACKAGES",
    "abootstrap_snapshot",
    "acreate_sandbox_snapshot",
    "acreate_snapshot",
    "adelete_snapshot",
    "aget_snapshot",
    "alist_snapshots",
    "aresolve_sandbox_spec_snapshot",
    "aresolve_snapshot",
    "bootstrap_snapshot",
    "build_base_snapshot_image",
    "create_sandbox_snapshot",
    "create_snapshot",
    "delete_snapshot",
    "fallback_to_declarative_image",
    "get_snapshot",
    "list_snapshots",
    "resolve_default_snapshot",
    "resolve_sandbox_spec_snapshot",
    "resolve_snapshot",
    # Sandbox lifecycle
    "afork_sandbox",
    "aget_sandbox",
    "aresume_workspace_session",
    "fork_sandbox",
    "get_sandbox",
    "resume_workspace_session",
]
