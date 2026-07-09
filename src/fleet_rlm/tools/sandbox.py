"""Controlled sandbox inspection tool primitives."""

from __future__ import annotations

from typing import Any

from fleet_rlm.tools.filesystem import list_files_impl


def inspect_workspace_impl(
    path: str = ".",
    *,
    max_entries: int = 50,
    interpreter: Any | None = None,
) -> dict[str, Any]:
    """Inspect Daytona workspace metadata without reading file bodies."""
    listing = list_files_impl(path, root="workspace", interpreter=interpreter)
    directories = listing.get("directories", [])
    files = listing.get("files", [])
    bounded_max = max(1, min(int(max_entries or 50), 200))
    entries = [*directories, *files]
    return {
        "status": "ok",
        "root": "workspace",
        "path": listing.get("path", ""),
        "directories": len(directories),
        "files": len(files),
        "entries": entries[:bounded_max],
        "total": len(entries),
        "truncated": len(entries) > bounded_max,
    }


__all__ = ["inspect_workspace_impl"]
