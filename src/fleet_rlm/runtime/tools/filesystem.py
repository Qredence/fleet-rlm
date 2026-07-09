"""Daytona-backed filesystem tool stubs for discover_tools()."""

from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.tools._marker import tool_fn
from fleet_rlm.runtime.tools.host_filesystem import (
    HostFilesystemContext,
    find_host_files,
    read_host_file_slice,
)
from fleet_rlm.tools.filesystem import list_files_impl, read_file_impl, reject_legacy_list_files_pattern
from fleet_rlm.tools.sandbox import inspect_workspace_impl


@tool_fn
def list_files(
    path: str = ".",
    root: str = "workspace",
    pattern: str | None = None,
) -> dict[str, Any]:
    """List files and directories in an approved Daytona workspace or volume root."""
    rejected = reject_legacy_list_files_pattern(pattern)
    if rejected is not None:
        return rejected
    return list_files_impl(path, root=root, interpreter=None)


@tool_fn
def read_file_slice(
    path: str,
    start_line: int = 1,
    num_lines: int = 100,
    end_line: int | None = None,
) -> dict[str, Any]:
    """Read a line range from a host file."""
    resolved_num_lines = (end_line - start_line + 1) if end_line is not None else num_lines
    ctx = HostFilesystemContext(agent=None)
    return read_host_file_slice(ctx, path=path, start_line=start_line, num_lines=resolved_num_lines)


@tool_fn
def find_files(pattern: str, path: str = ".", include: str = "") -> dict[str, Any]:
    """Search host file contents with a regex pattern."""
    ctx = HostFilesystemContext(agent=None)
    return find_host_files(ctx, pattern=pattern, path=path, include=include)


@tool_fn
def read_file(path: str, root: str = "workspace", max_bytes: int = 200_000) -> dict[str, Any]:
    """Read a bounded file preview from an approved Daytona workspace or volume root."""
    return read_file_impl(path, root=root, max_bytes=max_bytes, interpreter=None)


@tool_fn
def inspect_workspace(path: str = ".", max_entries: int = 50) -> dict[str, Any]:
    """Inspect Daytona workspace metadata without reading file bodies."""
    return inspect_workspace_impl(path, max_entries=max_entries, interpreter=None)
