"""Daytona-backed artifact read/write helpers."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import uuid
from pathlib import PurePosixPath
from typing import Any

from fleet_rlm.tools.sessions import (
    call_session_method,
    require_interpreter,
    resolve_interpreter_session,
    run_sandbox_fs_call,
)

from .paths import (
    APPROVED_ARTIFACT_CATEGORIES,
    ArtifactPathError,
    ArtifactWriteError,
    artifact_session_root,
    build_artifact_metadata,
    build_artifact_ref,
    parse_artifact_location_from_public_path,
    resolve_artifact_path,
)
from .schemas import ArtifactMetadata, ArtifactRef

DEFAULT_MAX_READ_BYTES = 200_000
MAX_READ_BYTES = 1_000_000
_LARGE_OUTPUT_CATEGORY = "data"
_SESSION_INDEX_NAME = ".artifact-index.json"


def _guess_mime_type(relative_path: str) -> str | None:
    guessed, _encoding = mimetypes.guess_type(relative_path)
    return guessed


def artifact_exists(interpreter: Any, resolved_path: str) -> bool:
    """Return whether an artifact file exists in the Daytona sandbox."""
    bound = require_interpreter(interpreter)
    try:
        run_sandbox_fs_call(bound, resolved_path, "get_file_info")
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _session_index_path(
    session_id: str,
    *,
    volume_mount_path: str | None = None,
) -> str:
    return str(artifact_session_root(session_id, volume_mount_path=volume_mount_path) / _SESSION_INDEX_NAME)


def _load_session_index(
    interpreter: Any,
    *,
    session_id: str,
    volume_mount_path: str | None = None,
) -> dict[str, dict[str, str]]:
    index_path = _session_index_path(session_id, volume_mount_path=volume_mount_path)
    if not artifact_exists(interpreter, index_path):
        return {}
    raw = call_session_method(resolve_interpreter_session(interpreter), "read_file", index_path)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    entries = payload.get("artifacts")
    if not isinstance(entries, dict):
        return {}
    normalized: dict[str, dict[str, str]] = {}
    for artifact_id, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        category = str(entry.get("category") or "").strip()
        relative_path = str(entry.get("relative_path") or "").strip()
        if category and relative_path:
            normalized[str(artifact_id)] = {
                "category": category,
                "relative_path": relative_path,
            }
    return normalized


def _save_session_index(
    interpreter: Any,
    *,
    session_id: str,
    index: dict[str, dict[str, str]],
    volume_mount_path: str | None = None,
) -> None:
    index_path = _session_index_path(session_id, volume_mount_path=volume_mount_path)
    payload = json.dumps({"artifacts": index}, sort_keys=True).encode("utf-8")
    _atomic_daytona_write(interpreter, resolved_path=index_path, content=payload)


def _register_artifact_in_index(
    interpreter: Any,
    *,
    session_id: str,
    ref: ArtifactRef,
    volume_mount_path: str | None = None,
) -> None:
    category, relative_path = parse_artifact_location_from_public_path(ref.path)
    index = _load_session_index(
        interpreter,
        session_id=session_id,
        volume_mount_path=volume_mount_path,
    )
    index[ref.id] = {"category": category, "relative_path": relative_path}
    _save_session_index(
        interpreter,
        session_id=session_id,
        index=index,
        volume_mount_path=volume_mount_path,
    )


def _atomic_daytona_write(interpreter: Any, *, resolved_path: str, content: bytes) -> None:
    bound = require_interpreter(interpreter)
    session = resolve_interpreter_session(bound)
    if session is None:
        raise RuntimeError("No Daytona sandbox session available.")
    tmp_path = f"{resolved_path}.{uuid.uuid4().hex}.tmp"
    try:
        payload_text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArtifactWriteError("Artifact content must be UTF-8 text.") from exc
    write = getattr(session, "write_file", None)
    if not callable(write):
        raise RuntimeError("No Daytona write method available.")
    write(tmp_path, payload_text)
    try:
        run_sandbox_fs_call(bound, tmp_path, "move_files", resolved_path)
    except Exception:
        try:
            run_sandbox_fs_call(bound, tmp_path, "delete_file")
        except Exception:
            pass
        raise


def write_artifact_bytes(
    interpreter: Any,
    *,
    session_id: str,
    category: str,
    relative_path: str,
    content: bytes,
    overwrite: bool = False,
    mime_type: str | None = None,
    title: str | None = None,
    volume_mount_path: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ArtifactMetadata:
    """Write artifact bytes under the approved session artifact root."""
    resolved = str(
        resolve_artifact_path(
            session_id,
            category=category,
            relative_path=relative_path,
            volume_mount_path=volume_mount_path,
        )
    )
    if not overwrite and artifact_exists(interpreter, resolved):
        raise ArtifactWriteError("Artifact already exists. Use update_artifact to modify it.")

    _atomic_daytona_write(interpreter, resolved_path=resolved, content=content)
    checksum = hashlib.sha256(content).hexdigest()
    ref = build_artifact_ref(
        session_id=session_id,
        category=category,
        relative_path=relative_path,
        mime_type=mime_type or _guess_mime_type(relative_path),
        size_bytes=len(content),
        checksum=checksum,
    )
    _register_artifact_in_index(
        interpreter,
        session_id=session_id,
        ref=ref,
        volume_mount_path=volume_mount_path,
    )
    return build_artifact_metadata(ref=ref, title=title, metadata=metadata)


def write_large_tool_output_artifact(
    interpreter: Any,
    *,
    session_id: str,
    tool_name: str,
    content: bytes,
    mime_type: str = "text/plain",
    volume_mount_path: str | None = None,
) -> ArtifactMetadata:
    """Persist a large tool output under the approved session artifact root."""
    safe_tool = "-".join(str(tool_name or "tool").strip().split()) or "tool"
    safe_tool = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in safe_tool).strip("-")
    safe_tool = safe_tool or "tool"
    relative_path = str(PurePosixPath("tool-outputs") / safe_tool / f"{uuid.uuid4().hex}.txt")
    return write_artifact_bytes(
        interpreter,
        session_id=session_id,
        category=_LARGE_OUTPUT_CATEGORY,
        relative_path=relative_path,
        content=content,
        overwrite=False,
        mime_type=mime_type,
        title=f"Large {safe_tool} output",
        volume_mount_path=volume_mount_path,
        metadata={"tool_name": safe_tool, "large_output": True},
    )


def update_artifact_bytes(
    interpreter: Any,
    *,
    session_id: str,
    category: str,
    relative_path: str,
    content: bytes,
    mime_type: str | None = None,
    title: str | None = None,
    volume_mount_path: str | None = None,
) -> ArtifactMetadata:
    """Update an existing artifact under the approved session artifact root."""
    resolved = str(
        resolve_artifact_path(
            session_id,
            category=category,
            relative_path=relative_path,
            volume_mount_path=volume_mount_path,
        )
    )
    if not artifact_exists(interpreter, resolved):
        raise ArtifactWriteError("Artifact not found.")

    return write_artifact_bytes(
        interpreter,
        session_id=session_id,
        category=category,
        relative_path=relative_path,
        content=content,
        overwrite=True,
        mime_type=mime_type,
        title=title,
        volume_mount_path=volume_mount_path,
    )


def _list_directory_entries(interpreter: Any, directory: str) -> list[Any]:
    bound = require_interpreter(interpreter)
    return list(call_session_method(resolve_interpreter_session(bound), "list_files", directory) or [])


def _entry_name(entry: Any) -> str:
    value = getattr(entry, "name", None) or getattr(entry, "path", None) or str(entry)
    return PurePosixPath(str(value).rstrip("/")).name


def _walk_artifact_files(
    interpreter: Any,
    *,
    session_id: str,
    category: str,
    volume_mount_path: str | None = None,
) -> list[tuple[str, str]]:
    """Return (category, relative_path) pairs for files under one category root."""
    category_root = artifact_session_root(session_id, volume_mount_path=volume_mount_path) / category
    root_text = str(category_root)
    found: list[tuple[str, str]] = []

    def walk(directory: str, relative_prefix: PurePosixPath) -> None:
        try:
            entries = _list_directory_entries(interpreter, directory)
        except (FileNotFoundError, OSError):
            return
        for entry in entries:
            name = _entry_name(entry)
            if not name or name.startswith("."):
                continue
            child_relative = relative_prefix / name
            child_absolute = f"{directory.rstrip('/')}/{name}"
            if bool(getattr(entry, "is_dir", False)):
                walk(child_absolute, child_relative)
            else:
                found.append((category, str(child_relative)))

    walk(root_text, PurePosixPath())
    return found


def list_session_artifact_refs(
    interpreter: Any,
    *,
    session_id: str,
    category: str | None = None,
    volume_mount_path: str | None = None,
) -> list[ArtifactRef]:
    """List metadata-only artifact refs for one session."""
    categories = [category] if category is not None else sorted(APPROVED_ARTIFACT_CATEGORIES)
    if category is not None and category not in APPROVED_ARTIFACT_CATEGORIES:
        raise ArtifactPathError(f"Unsupported artifact category: {category!r}")

    refs: list[ArtifactRef] = []
    for item_category in categories:
        for cat, relative_path in _walk_artifact_files(
            interpreter,
            session_id=session_id,
            category=item_category,
            volume_mount_path=volume_mount_path,
        ):
            resolved = str(
                resolve_artifact_path(
                    session_id,
                    category=cat,
                    relative_path=relative_path,
                    volume_mount_path=volume_mount_path,
                )
            )
            size_bytes: int | None = None
            try:
                info = run_sandbox_fs_call(interpreter, resolved, "get_file_info")
                size_bytes = int(getattr(info, "size", 0) or 0)
            except (FileNotFoundError, OSError):
                size_bytes = None
            refs.append(
                build_artifact_ref(
                    session_id=session_id,
                    category=cat,
                    relative_path=relative_path,
                    mime_type=_guess_mime_type(relative_path),
                    size_bytes=size_bytes,
                )
            )
    return refs


def read_artifact_bytes(
    interpreter: Any,
    *,
    session_id: str,
    category: str,
    relative_path: str,
    max_bytes: int = DEFAULT_MAX_READ_BYTES,
    volume_mount_path: str | None = None,
) -> tuple[ArtifactRef, bytes, bool]:
    """Read bounded artifact bytes from the approved session artifact root."""
    resolved = str(
        resolve_artifact_path(
            session_id,
            category=category,
            relative_path=relative_path,
            volume_mount_path=volume_mount_path,
        )
    )
    if not artifact_exists(interpreter, resolved):
        raise ArtifactWriteError("Artifact not found.")

    raw = call_session_method(resolve_interpreter_session(interpreter), "read_file", resolved)
    raw_bytes = b"" if raw is None else raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    bounded_max = max(1, min(int(max_bytes or DEFAULT_MAX_READ_BYTES), MAX_READ_BYTES))
    truncated = len(raw_bytes) > bounded_max
    preview = raw_bytes[:bounded_max] if truncated else raw_bytes
    ref = build_artifact_ref(
        session_id=session_id,
        category=category,
        relative_path=relative_path,
        mime_type=_guess_mime_type(relative_path),
        size_bytes=len(raw_bytes),
        checksum=hashlib.sha256(raw_bytes).hexdigest(),
    )
    return ref, preview, truncated


def resolve_artifact_by_id(
    interpreter: Any,
    *,
    session_id: str,
    artifact_id: str,
    category: str | None = None,
    volume_mount_path: str | None = None,
) -> tuple[str, str]:
    """Resolve an artifact id to (category, relative_path)."""
    safe_id = str(artifact_id or "").strip()
    if not safe_id.startswith("artifact-"):
        raise ArtifactWriteError("Invalid artifact reference.")

    indexed = _load_session_index(
        interpreter,
        session_id=session_id,
        volume_mount_path=volume_mount_path,
    ).get(safe_id)
    if indexed is not None:
        resolved_category = indexed["category"]
        relative = indexed["relative_path"]
        if category is not None and category != resolved_category:
            raise ArtifactWriteError("Artifact not found.")
        return resolved_category, relative

    for ref in list_session_artifact_refs(
        interpreter,
        session_id=session_id,
        category=category,
        volume_mount_path=volume_mount_path,
    ):
        if ref.id == safe_id:
            return parse_artifact_location_from_public_path(ref.path)

    raise ArtifactWriteError("Artifact not found.")


__all__ = [
    "DEFAULT_MAX_READ_BYTES",
    "MAX_READ_BYTES",
    "artifact_exists",
    "list_session_artifact_refs",
    "read_artifact_bytes",
    "resolve_artifact_by_id",
    "update_artifact_bytes",
    "write_artifact_bytes",
    "write_large_tool_output_artifact",
]
