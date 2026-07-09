"""Daytona-backed artifact tool stubs for discover_tools()."""

from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.tools._marker import tool_fn
from fleet_rlm.tools.artifacts import (
    create_artifact_impl,
    list_artifacts_impl,
    read_artifact_impl,
    update_artifact_impl,
)


@tool_fn
def create_artifact(
    category: str,
    relative_path: str,
    content: str,
    mime_type: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Create a new artifact under the approved session artifact root."""
    return create_artifact_impl(
        session_id="",
        category=category,
        relative_path=relative_path,
        content=content,
        mime_type=mime_type,
        title=title,
        interpreter=None,
    )


@tool_fn
def update_artifact(
    content: str,
    artifact_id: str | None = None,
    category: str | None = None,
    relative_path: str | None = None,
    mime_type: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Update an existing artifact under the approved session artifact root."""
    return update_artifact_impl(
        session_id="",
        content=content,
        artifact_id=artifact_id,
        category=category,
        relative_path=relative_path,
        mime_type=mime_type,
        title=title,
        interpreter=None,
    )


@tool_fn
def list_artifacts(category: str | None = None) -> dict[str, Any]:
    """List safe artifact metadata for the current session."""
    return list_artifacts_impl(session_id="", category=category, interpreter=None)


@tool_fn
def read_artifact(
    artifact_id: str | None = None,
    category: str | None = None,
    relative_path: str | None = None,
    max_bytes: int = 200_000,
) -> dict[str, Any]:
    """Read bounded artifact content from the approved session artifact root."""
    return read_artifact_impl(
        session_id="",
        artifact_id=artifact_id,
        category=category,
        relative_path=relative_path,
        max_bytes=max_bytes,
        interpreter=None,
    )


__all__ = [
    "create_artifact",
    "list_artifacts",
    "read_artifact",
    "update_artifact",
]
