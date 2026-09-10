"""Pinned Daytona SDK capability gates for Workspace Agent substitution."""

from __future__ import annotations

import inspect

from daytona._async.filesystem import AsyncFileSystem


def _parameter_names(method: str) -> set[str]:
    return set(inspect.signature(getattr(AsyncFileSystem, method)).parameters)


def test_pinned_sdk_cannot_replace_bounded_cursor_workspace_reads() -> None:
    """SDK download/list APIs lack Fleet's byte and cursor admission controls."""
    assert _parameter_names("download_file") == {"self", "args"}
    assert _parameter_names("list_files") == {"self", "path", "depth", "request_timeout"}


def test_pinned_sdk_cannot_replace_workspace_cas_or_atomic_publication() -> None:
    """SDK upload/delete APIs expose no checksum/CAS or atomic-replace contract."""
    assert _parameter_names("upload_file") == {"self", "src", "dst", "timeout"}
    assert _parameter_names("delete_file") == {"self", "path", "recursive", "request_timeout"}
    assert not hasattr(AsyncFileSystem, "append_file")
    assert not hasattr(AsyncFileSystem, "patch_file")
