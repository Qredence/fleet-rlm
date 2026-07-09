"""Safe artifact root helpers for Daytona-backed runtime artifacts."""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from fleet_rlm.tools.paths import PathSafetyError, validate_relative_posix_path
from fleet_rlm.tools.sessions import (
    call_session_method,
    require_interpreter,
    resolve_interpreter_session,
    run_sandbox_fs_call,
)
from fleet_rlm.utils.identity import sanitize_id

from .schemas import ArtifactMetadata, ArtifactRef

APPROVED_ARTIFACT_CATEGORIES = frozenset({"plans", "reports", "data"})
DEFAULT_ARTIFACT_VOLUME_ROOT = PurePosixPath("/home/daytona/memory")
_DEFAULT_MAX_READ_BYTES = 200_000
_MAX_READ_BYTES = 1_000_000
_LARGE_OUTPUT_CATEGORY = "data"


class ArtifactPathError(ValueError):
    """Raised when an artifact path escapes the approved root."""


class ArtifactWriteError(ValueError):
    """Raised when an artifact write or update violates policy."""


def _validate_relative_artifact_path(path: str) -> PurePosixPath:
    try:
        return validate_relative_posix_path(
            path,
            empty_message="Artifact path must not be empty.",
            traversal_message="Artifact path traversal is not allowed.",
            absolute_message="Artifact path must stay inside the artifact root.",
            backslash_message="Backslash artifact paths are not allowed.",
        )
    except PathSafetyError as exc:
        raise ArtifactPathError(str(exc)) from exc


def artifact_session_root(
    session_id: str,
    *,
    volume_mount_path: str | None = None,
) -> PurePosixPath:
    """Return the approved artifact root for one runtime session."""
    safe_session = sanitize_id(session_id, "session")
    volume_root = PurePosixPath(str(volume_mount_path or DEFAULT_ARTIFACT_VOLUME_ROOT))
    return volume_root / "artifacts" / "sessions" / safe_session


def artifact_public_relative_path(
    session_id: str,
    *,
    category: str,
    relative_path: str,
) -> str:
    """Return the safe public relative path for one artifact under the volume root."""
    if category not in APPROVED_ARTIFACT_CATEGORIES:
        raise ArtifactPathError(f"Unsupported artifact category: {category!r}")
    safe_relative = _validate_relative_artifact_path(relative_path)
    safe_session = sanitize_id(session_id, "session")
    return str(PurePosixPath("artifacts") / "sessions" / safe_session / category / safe_relative)


def resolve_artifact_path(
    session_id: str,
    *,
    category: str,
    relative_path: str,
    volume_mount_path: str | None = None,
) -> PurePosixPath:
    """Resolve one artifact path under an approved session/category root."""
    if category not in APPROVED_ARTIFACT_CATEGORIES:
        raise ArtifactPathError(f"Unsupported artifact category: {category!r}")
    safe_relative = _validate_relative_artifact_path(relative_path)
    root = artifact_session_root(session_id, volume_mount_path=volume_mount_path)
    return root / category / safe_relative


def _artifact_id_for_public_path(public_path: str) -> str:
    digest = hashlib.sha256(public_path.encode("utf-8")).hexdigest()[:16]
    return f"artifact-{digest}"


def build_artifact_ref(
    *,
    session_id: str,
    category: str,
    relative_path: str,
    volume_mount_path: str | None = None,
    mime_type: str | None = None,
    size_bytes: int | None = None,
    checksum: str | None = None,
) -> ArtifactRef:
    """Build a stable artifact reference without writing file content."""
    _ = volume_mount_path
    public_path = artifact_public_relative_path(
        session_id,
        category=category,
        relative_path=relative_path,
    )
    return ArtifactRef(
        id=_artifact_id_for_public_path(public_path),
        session_id=sanitize_id(session_id, "session"),
        category=category,
        path=public_path,
        uri=f"daytona://{public_path}",
        mime_type=mime_type,
        size_bytes=size_bytes,
        checksum=checksum,
    )


def build_artifact_metadata(
    *,
    ref: ArtifactRef,
    title: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ArtifactMetadata:
    """Build artifact metadata for storage or event payloads."""
    return ArtifactMetadata(
        ref=ref,
        title=title,
        created_at=datetime.now(UTC).isoformat(),
        metadata=dict(metadata or {}),
    )


def _guess_mime_type(relative_path: str) -> str | None:
    guessed, _encoding = mimetypes.guess_type(relative_path)
    return guessed


def artifact_exists(interpreter: Any, resolved_path: str) -> bool:
    """Return whether an artifact file exists in the Daytona sandbox."""
    bound = require_interpreter(interpreter)
    try:
        run_sandbox_fs_call(bound, resolved_path, "get_file_info")
        return True
    except Exception:
        return False


def _atomic_daytona_write(interpreter: Any, *, resolved_path: str, content: bytes) -> None:
    bound = require_interpreter(interpreter)
    session = resolve_interpreter_session(bound)
    if session is None:
        raise RuntimeError("No Daytona sandbox session available.")
    tmp_path = f"{resolved_path}.{uuid.uuid4().hex}.tmp"
    payload_text = content.decode("utf-8")
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
    return build_artifact_metadata(ref=ref, title=title)


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
    metadata = write_artifact_bytes(
        interpreter,
        session_id=session_id,
        category=_LARGE_OUTPUT_CATEGORY,
        relative_path=relative_path,
        content=content,
        overwrite=False,
        mime_type=mime_type,
        title=f"Large {safe_tool} output",
        volume_mount_path=volume_mount_path,
    )
    metadata.metadata["tool_name"] = safe_tool
    metadata.metadata["large_output"] = True
    return metadata


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
        except Exception:
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
            except Exception:
                size_bytes = None
            refs.append(
                build_artifact_ref(
                    session_id=session_id,
                    category=cat,
                    relative_path=relative_path,
                    volume_mount_path=volume_mount_path,
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
    max_bytes: int = _DEFAULT_MAX_READ_BYTES,
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
    bounded_max = max(1, min(int(max_bytes or _DEFAULT_MAX_READ_BYTES), _MAX_READ_BYTES))
    truncated = len(raw_bytes) > bounded_max
    preview = raw_bytes[:bounded_max] if truncated else raw_bytes
    ref = build_artifact_ref(
        session_id=session_id,
        category=category,
        relative_path=relative_path,
        volume_mount_path=volume_mount_path,
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

    for ref in list_session_artifact_refs(
        interpreter,
        session_id=session_id,
        category=category,
        volume_mount_path=volume_mount_path,
    ):
        if ref.id == safe_id:
            public_path = PurePosixPath(ref.path)
            parts = public_path.parts
            # artifacts/sessions/<session>/<category>/<relative...>
            if len(parts) < 5 or parts[0] != "artifacts" or parts[1] != "sessions":
                raise ArtifactWriteError("Invalid artifact reference.")
            resolved_category = parts[3]
            relative = str(PurePosixPath(*parts[4:]))
            return resolved_category, relative

    raise ArtifactWriteError("Artifact not found.")


__all__ = [
    "APPROVED_ARTIFACT_CATEGORIES",
    "ArtifactPathError",
    "ArtifactWriteError",
    "artifact_exists",
    "artifact_public_relative_path",
    "artifact_session_root",
    "build_artifact_metadata",
    "build_artifact_ref",
    "list_session_artifact_refs",
    "read_artifact_bytes",
    "resolve_artifact_by_id",
    "resolve_artifact_path",
    "update_artifact_bytes",
    "write_artifact_bytes",
    "write_large_tool_output_artifact",
]
