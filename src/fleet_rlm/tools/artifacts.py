"""RLM tool-facing artifact helpers."""

from __future__ import annotations

from typing import Any

from fleet_rlm.artifacts.storage import (
    ArtifactPathError,
    ArtifactWriteError,
    build_artifact_metadata,
    build_artifact_ref,
    list_session_artifact_refs,
    read_artifact_bytes,
    resolve_artifact_by_id,
    update_artifact_bytes,
    write_artifact_bytes,
)

_DEFAULT_MAX_READ_BYTES = 200_000


def _error_payload(message: str) -> dict[str, Any]:
    return {"status": "error", "error": message}


def _require_session_id(session_id: str | None) -> str:
    safe = str(session_id or "").strip()
    if not safe:
        raise ArtifactWriteError("session_id is required for artifact tools.")
    return safe


def create_artifact_ref(**kwargs: Any) -> Any:
    """Build a safe artifact reference; content writes are deferred."""
    return build_artifact_ref(**kwargs)


def create_artifact_impl(
    *,
    session_id: str,
    category: str,
    relative_path: str,
    content: str,
    mime_type: str | None = None,
    title: str | None = None,
    interpreter: Any | None = None,
) -> dict[str, Any]:
    """Create a new artifact under the approved session artifact root."""
    try:
        safe_session_id = _require_session_id(session_id)
        metadata = write_artifact_bytes(
            interpreter,
            session_id=safe_session_id,
            category=category,
            relative_path=relative_path,
            content=content.encode("utf-8"),
            mime_type=mime_type,
            title=title,
        )
        return {"status": "ok", "artifact": metadata.model_dump()}
    except (ArtifactPathError, ArtifactWriteError, RuntimeError, ValueError) as exc:
        return _error_payload(str(exc))


def update_artifact_impl(
    *,
    session_id: str,
    content: str,
    artifact_id: str | None = None,
    category: str | None = None,
    relative_path: str | None = None,
    mime_type: str | None = None,
    title: str | None = None,
    interpreter: Any | None = None,
) -> dict[str, Any]:
    """Update an existing artifact under the approved session artifact root."""
    try:
        safe_session_id = _require_session_id(session_id)
        resolved_category = category
        resolved_relative_path = relative_path
        if artifact_id:
            resolved_category, resolved_relative_path = resolve_artifact_by_id(
                interpreter,
                session_id=safe_session_id,
                artifact_id=artifact_id,
                category=category,
            )
        if not resolved_category or not resolved_relative_path:
            raise ArtifactWriteError("artifact_id or category and relative_path are required.")

        metadata = update_artifact_bytes(
            interpreter,
            session_id=safe_session_id,
            category=resolved_category,
            relative_path=resolved_relative_path,
            content=content.encode("utf-8"),
            mime_type=mime_type,
            title=title,
        )
        return {"status": "ok", "artifact": metadata.model_dump()}
    except (ArtifactPathError, ArtifactWriteError, RuntimeError, ValueError) as exc:
        return _error_payload(str(exc))


def list_artifacts_impl(
    *,
    session_id: str,
    category: str | None = None,
    interpreter: Any | None = None,
) -> dict[str, Any]:
    """List safe artifact metadata for one session."""
    try:
        safe_session_id = _require_session_id(session_id)
        refs = list_session_artifact_refs(
            interpreter,
            session_id=safe_session_id,
            category=category,
        )
        artifacts = [build_artifact_metadata(ref=ref).model_dump() for ref in refs]
        return {"status": "ok", "artifacts": artifacts, "count": len(artifacts)}
    except (ArtifactPathError, ArtifactWriteError, RuntimeError, ValueError) as exc:
        return _error_payload(str(exc))


def read_artifact_impl(
    *,
    session_id: str,
    artifact_id: str | None = None,
    category: str | None = None,
    relative_path: str | None = None,
    max_bytes: int = _DEFAULT_MAX_READ_BYTES,
    interpreter: Any | None = None,
) -> dict[str, Any]:
    """Read bounded artifact content from the approved session artifact root."""
    try:
        safe_session_id = _require_session_id(session_id)
        resolved_category = category
        resolved_relative_path = relative_path
        if artifact_id:
            resolved_category, resolved_relative_path = resolve_artifact_by_id(
                interpreter,
                session_id=safe_session_id,
                artifact_id=artifact_id,
                category=category,
            )
        if not resolved_category or not resolved_relative_path:
            raise ArtifactWriteError("artifact_id or category and relative_path are required.")

        ref, preview, truncated = read_artifact_bytes(
            interpreter,
            session_id=safe_session_id,
            category=resolved_category,
            relative_path=resolved_relative_path,
            max_bytes=max_bytes,
        )
        text = preview.decode("utf-8", errors="replace")
        return {
            "status": "ok",
            "artifact": build_artifact_metadata(ref=ref).model_dump(),
            "content": text,
            "size": ref.size_bytes,
            "returned_bytes": len(preview),
            "truncated": truncated,
            "artifact_backed": True,
            "encoding": "utf-8-lossy" if "\ufffd" in text else "utf-8",
        }
    except (ArtifactPathError, ArtifactWriteError, RuntimeError, ValueError) as exc:
        return _error_payload(str(exc))


def update_artifact_ref(**kwargs: Any) -> dict[str, Any]:
    """Backward-compatible alias for update_artifact_impl."""
    return update_artifact_impl(**kwargs)


__all__ = [
    "build_artifact_metadata",
    "build_artifact_ref",
    "create_artifact_impl",
    "create_artifact_ref",
    "list_artifacts_impl",
    "read_artifact_impl",
    "update_artifact_impl",
    "update_artifact_ref",
]
