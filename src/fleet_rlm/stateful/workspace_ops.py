"""Workspace file operation helpers for StatefulSandboxManager."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .result_adapters import (
    extract_execute_error,
    final_output_dict,
    operation_error,
    operation_unexpected,
)


@dataclass(slots=True)
class WorkspaceOpsContext:
    """Execution context for workspace file operations."""

    interpreter: Any
    workspace_path: str


def _validate_workspace_filename(workspace_path: str, filename: str) -> str:
    """Validate that *filename* stays under *workspace_path* and return the resolved path.

    Raises ``ValueError`` on path-traversal attempts (``../``, absolute paths,
    null bytes, etc.).
    """
    import posixpath

    if not filename or "\x00" in filename:
        raise ValueError(f"Invalid filename: {filename!r}")

    # Reject absolute paths outright.
    if posixpath.isabs(filename):
        raise ValueError(f"Absolute filenames are not allowed: {filename!r}")

    # Normalise and confirm the result stays inside workspace_path.
    resolved = posixpath.normpath(f"{workspace_path}/{filename}")
    workspace_prefix = workspace_path.rstrip("/") + "/"
    if not (resolved == workspace_path or resolved.startswith(workspace_prefix)):
        raise ValueError(f"Path traversal detected: {filename!r} escapes workspace")
    return resolved


def save_workspace_file(
    *,
    ctx: WorkspaceOpsContext,
    filename: str,
    content: str,
) -> dict[str, Any]:
    """Save content to a workspace file and normalize response payload."""
    try:
        file_path = _validate_workspace_filename(ctx.workspace_path, filename)
    except ValueError as exc:
        return operation_error(filename, str(exc))
    code = f"""
import os
try:
    # Ensure workspace directory exists
    os.makedirs("{ctx.workspace_path}", exist_ok=True)

    # Write content to file
    with open("{file_path}", "w") as f:
        f.write(content)

    SUBMIT(status="ok", path="{file_path}", chars=len(content))
except Exception as e:
    SUBMIT(status="error", error=str(e))
"""

    try:
        result = ctx.interpreter.execute(code, variables={"content": content})
    except Exception as exc:
        return operation_error(filename, exc)

    output = final_output_dict(result)
    if isinstance(output, dict):
        if output.get("status") == "ok":
            return {
                "status": "ok",
                "filename": filename,
                "path": file_path,
                "chars": int(output.get("chars", len(content))),
            }
        return operation_error(filename, output.get("error", "Unknown error"))

    stderr = extract_execute_error(result)
    if stderr is not None:
        return operation_error(filename, stderr)
    return operation_unexpected(filename)


def load_workspace_file(
    *,
    ctx: WorkspaceOpsContext,
    filename: str,
) -> dict[str, Any]:
    """Load content from a workspace file and normalize response payload."""
    try:
        file_path = _validate_workspace_filename(ctx.workspace_path, filename)
    except ValueError as exc:
        return operation_error(filename, str(exc))
    code = f"""
try:
    with open("{file_path}", "r") as f:
        content = f.read()
    SUBMIT(status="ok", content=content, chars=len(content))
except FileNotFoundError:
    SUBMIT(status="error", error=f"File not found: {file_path}")
except Exception as e:
    SUBMIT(status="error", error=str(e))
"""

    try:
        result = ctx.interpreter.execute(code)
    except Exception as exc:
        return operation_error(filename, exc)

    output = final_output_dict(result)
    if isinstance(output, dict):
        if output.get("status") == "ok":
            return {
                "status": "ok",
                "filename": filename,
                "content": output.get("content", ""),
                "chars": output.get("chars", 0),
            }
        return operation_error(filename, output.get("error", "Unknown error"))
    return operation_unexpected(filename)


def list_workspace_files(*, ctx: WorkspaceOpsContext) -> list[str]:
    """List files from workspace root, returning an empty list on failure."""
    code = f"""
import os

files = []
try:
    if os.path.isdir("{ctx.workspace_path}"):
        files = os.listdir("{ctx.workspace_path}")
    else:
        files = []
except Exception:
    files = []

SUBMIT(files=files, count=len(files))
"""
    try:
        result = ctx.interpreter.execute(code)
    except Exception:
        return []

    output = final_output_dict(result)
    if isinstance(output, dict):
        files = output.get("files", [])
        if isinstance(files, list):
            return files
    return []


def delete_workspace_file(
    *,
    ctx: WorkspaceOpsContext,
    filename: str,
) -> dict[str, Any]:
    """Delete a workspace file and normalize response payload."""
    try:
        file_path = _validate_workspace_filename(ctx.workspace_path, filename)
    except ValueError as exc:
        return operation_error(filename, str(exc))
    code = f"""
import os

try:
    if os.path.exists("{file_path}"):
        os.remove("{file_path}")
        SUBMIT(status="ok", message=f"Deleted {file_path}")
    else:
        SUBMIT(status="error", error=f"File not found: {file_path}")
except Exception as e:
    SUBMIT(status="error", error=str(e))
"""
    try:
        result = ctx.interpreter.execute(code)
    except Exception as exc:
        return operation_error(filename, exc)

    output = final_output_dict(result)
    if isinstance(output, dict):
        return {
            "status": output.get("status", "error"),
            "filename": filename,
            "message": output.get("message", ""),
            "error": output.get("error", ""),
        }
    return operation_unexpected(filename)
