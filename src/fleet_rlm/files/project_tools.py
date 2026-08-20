"""Browsable durable Project deliverable Tools bound to the Volume projects root.

The model names one Project slug explicitly (``projects/<slug>/``); the backend
sanitizes only. Project Tools share the Session Workspace filesystem machinery
through one ``SessionWorkspaceFS`` bound at ``projects/``, so writes stay
atomic and immediately durable independently of Turn Commit. Scratch belongs in
the Session Workspace; durable deliverables belong in a Project.
"""

from __future__ import annotations

from collections.abc import Mapping

import dspy

from fleet_rlm.files.volume_paths import UnsafePathError, validate_project_slug
from fleet_rlm.files.workspace_models import SessionWorkspaceFS
from fleet_rlm.files.workspace_tools import (
    MAX_WORKSPACE_READ_CHARS,
    WorkspaceLikeConfig,
    WorkspaceToolError,
    workspace_like_event_views,
    workspace_like_tools,
)
from fleet_rlm.files.workspace_validation import normalize_workspace_path
from fleet_rlm.rlm.tool_observer import ToolEventView

MAX_PROJECT_READ_CHARS = MAX_WORKSPACE_READ_CHARS
PROJECT_WORKSPACE_NAMESPACE = "project_workspace"


class ProjectToolError(WorkspaceToolError):
    """Safe, actionable failure returned to generated project-tool callers.

    Subclasses ``WorkspaceToolError`` so the interpreter bridge keeps rendering
    structured ``{"ok": False, "error": code}`` results for project tools.
    """


def _normalize_project_path(path: str, *, allow_root: bool = False) -> str:
    """Return one projects-root-relative path with a validated first-segment slug.

    A redundant leading ``projects/`` segment is tolerated so the canonical
    volume-relative convention (``projects/<slug>/<path>``) and guard-target
    language map onto the same rooted tools. ``"."`` (the projects root) is
    only valid when ``allow_root``.
    """
    if not allow_root and path in {".", "projects"}:
        raise ProjectToolError("invalid_path", "Project path cannot target the projects root")
    normalized = normalize_workspace_path(path, allow_root=allow_root)
    if normalized == "projects" or normalized.startswith("projects/"):
        normalized = normalized.removeprefix("projects").lstrip("/") or "."
    if normalized == ".":
        return normalized
    first = normalized.split("/", 1)[0]
    try:
        validate_project_slug(first)
    except UnsafePathError as exc:
        raise ProjectToolError("invalid_path", f"Project path is invalid: {exc}") from None
    return normalized


def _project_file_path(path: str) -> str:
    """Return one validated ``<slug>/<file...>`` path below the projects root."""
    normalized = _normalize_project_path(path)
    if "/" not in normalized:
        raise ProjectToolError(
            "invalid_path",
            "Project path must name a file inside a project: projects/<slug>/<path>",
        )
    return normalized


class ProjectToolHost:
    """Bind the browsable projects root into stable synchronous tools."""

    def __init__(self, workspace: SessionWorkspaceFS, *, max_file_bytes: int) -> None:
        """Bind a project filesystem adapter and enforce the per-file byte limit."""
        self._workspace = workspace
        self._max_file_bytes = max(1, int(max_file_bytes))

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        """Build the stable Project tool contract."""
        config = WorkspaceLikeConfig(
            namespace=PROJECT_WORKSPACE_NAMESPACE,
            domain="Project",
            error_type=ProjectToolError,
            read_max_chars=MAX_PROJECT_READ_CHARS,
            normalize_list_path=lambda path: _normalize_project_path(path, allow_root=True),
            normalize_file_path=_project_file_path,
            # Project delete normalizes with _normalize_project_path (not _project_file_path):
            # "." (the projects root) is refused by normalization itself.
            normalize_mutation_path=_normalize_project_path,
            # Project edit requires the target to name a file inside a project.
            normalize_edit_path=_project_file_path,
            has_append=False,
            verb="project",
            tool_docs={
                "list": "List immediate entries in one Project or the projects root.",
                "stat": "Return bounded metadata for one Project path.",
                "read": "Read one UTF-8 Project file page without returning more than max_chars.",
                "write": "Write one UTF-8 deliverable immediately under projects/<slug>/.",
                "delete": "Delete one file or empty directory immediately under projects/<slug>/.",
                "edit": "Replace exactly one unique occurrence of old with new in one UTF-8 Project file.",
            },
            tool_descs={
                "list": (
                    "List immediate entries under projects/<slug>/ (or the projects root) only when existing "
                    "durable Project deliverables are relevant; do not explore them for a self-contained request."
                ),
                "stat": "Read bounded metadata for a relevant durable Project deliverable path under projects/<slug>/.",
                "read": (
                    "Read one relevant UTF-8 Project deliverable page with max_chars in 1..10000 using a "
                    "projects/<slug>/<path> target. Continue with next_cursor until eof."
                ),
                "write": (
                    "Write UTF-8 text immediately as a durable deliverable under projects/<slug>/ when the "
                    "result must stay browsable across Sessions; choose a short repo/task-derived slug and "
                    "keep scratch in the Session Workspace. This durability is independent of Turn Commit."
                ),
                "delete": (
                    "Delete one file or one empty directory immediately under projects/<slug>/; non-empty "
                    "directories are refused, and a supplied expected_sha256 guards against deleting "
                    "changed content. This durability is independent of Turn Commit."
                ),
                "edit": (
                    "Replace exactly one unique occurrence of old with new in one UTF-8 Project file under "
                    "projects/<slug>/; the edit fails when old is absent or occurs more than once, and a "
                    "supplied expected_sha256 guards against editing changed content. Read the file first "
                    "and keep old short and unique. This durability is independent of Turn Commit."
                ),
            },
        )
        return workspace_like_tools(self._workspace, max_file_bytes=self._max_file_bytes, config=config)

    def event_views(self) -> Mapping[str, ToolEventView]:
        """Return bounded metadata-only projections for Project Tools."""
        return workspace_like_event_views(
            "project",
            lambda path, allow_root: _normalize_project_path(path, allow_root=allow_root),
            has_append=False,
        )
