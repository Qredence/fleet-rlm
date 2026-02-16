"""Modal sandbox and volume access handlers for bridge frontends.

This module provides bridge handlers that expose Modal SDK functionality
to the Ink TUI, enabling it to:
- Read/write files in Modal volumes
- List volume contents
- Launch and manage Modal sandboxes
- Access persistent memory at /data/memory
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .protocol import BridgeRPCError


def _get_modal_client():
    """Get or create Modal client."""
    try:
        import modal

        return modal
    except ImportError as exc:
        raise BridgeRPCError(
            code="MODAL_NOT_INSTALLED",
            message="Modal SDK not installed. Run: uv add modal",
        ) from exc


def volume_read(params: dict[str, Any]) -> dict[str, Any]:
    """Read a file from a Modal volume.

    Args:
        params: Dictionary containing:
            - volume_name: Name of the Modal volume
            - path: Path within the volume (relative to /)
            - encoding: Optional encoding (default: utf-8)

    Returns:
        Dictionary with file content
    """
    modal = _get_modal_client()

    volume_name = str(params.get("volume_name", "")).strip()
    path = str(params.get("path", "")).strip()
    encoding = str(params.get("encoding", "utf-8")).strip() or "utf-8"

    if not volume_name:
        raise BridgeRPCError(
            code="INVALID_ARGS",
            message="`volume_name` is required.",
        )
    if not path:
        raise BridgeRPCError(
            code="INVALID_ARGS",
            message="`path` is required.",
        )

    try:
        volume = modal.Volume.from_name(volume_name, version=2)

        # Read file content using vol.read_file
        full_path = f"/{path.lstrip('/')}"
        try:
            content_bytes = b"".join(volume.read_file(full_path))
            content = content_bytes.decode(encoding)
        except UnicodeDecodeError:
            # Return as base64 if not decodable
            import base64

            content = base64.b64encode(content_bytes).decode("ascii")
            encoding = "base64"

        return {
            "content": content,
            "path": full_path,
            "volume_name": volume_name,
            "encoding": encoding,
        }
    except Exception as exc:
        raise BridgeRPCError(
            code="VOLUME_READ_ERROR",
            message=f"Failed to read from volume: {exc}",
        ) from exc


def volume_write(params: dict[str, Any]) -> dict[str, Any]:
    """Write a file to a Modal volume.

    Args:
        params: Dictionary containing:
            - volume_name: Name of the Modal volume
            - path: Path within the volume (relative to /)
            - content: Content to write
            - encoding: Optional encoding (default: utf-8)

    Returns:
        Dictionary with write status
    """
    modal = _get_modal_client()

    volume_name = str(params.get("volume_name", "")).strip()
    path = str(params.get("path", "")).strip()
    content = str(params.get("content", ""))
    encoding = str(params.get("encoding", "utf-8")).strip() or "utf-8"

    if not volume_name:
        raise BridgeRPCError(
            code="INVALID_ARGS",
            message="`volume_name` is required.",
        )
    if not path:
        raise BridgeRPCError(
            code="INVALID_ARGS",
            message="`path` is required.",
        )

    try:
        volume = modal.Volume.from_name(volume_name, version=2)

        # Write file content using vol.write_file
        full_path = f"/{path.lstrip('/')}"
        content_bytes = content.encode(encoding)

        with volume.batch_write() as batch:
            batch.put_file_contents(full_path, content_bytes)

        return {
            "ok": True,
            "path": full_path,
            "volume_name": volume_name,
            "bytes_written": len(content_bytes),
        }
    except Exception as exc:
        raise BridgeRPCError(
            code="VOLUME_WRITE_ERROR",
            message=f"Failed to write to volume: {exc}",
        ) from exc


def volume_list(params: dict[str, Any]) -> dict[str, Any]:
    """List files in a Modal volume.

    Args:
        params: Dictionary containing:
            - volume_name: Name of the Modal volume
            - prefix: Optional path prefix to filter
            - recursive: Whether to list recursively (default: False)

    Returns:
        Dictionary with list of files
    """
    modal = _get_modal_client()

    volume_name = str(params.get("volume_name", "")).strip()
    prefix = str(params.get("prefix", "")).strip()
    recursive = bool(params.get("recursive", False))

    if not volume_name:
        raise BridgeRPCError(
            code="INVALID_ARGS",
            message="`volume_name` is required.",
        )

    try:
        volume = modal.Volume.from_name(volume_name, version=2)

        # List files using vol.listdir
        prefix_path = f"/{prefix.lstrip('/')}"
        entries = volume.listdir(prefix_path, recursive=recursive)

        files = []
        for entry in entries:
            files.append(
                {
                    "path": entry.path,
                    "type": "directory" if entry.is_directory else "file",
                    "size": entry.size if not entry.is_directory else None,
                    "mtime": entry.mtime,
                }
            )

        return {
            "files": files,
            "count": len(files),
            "volume_name": volume_name,
            "prefix": prefix,
        }
    except Exception as exc:
        raise BridgeRPCError(
            code="VOLUME_LIST_ERROR",
            message=f"Failed to list volume: {exc}",
        ) from exc


def volume_delete(params: dict[str, Any]) -> dict[str, Any]:
    """Delete a file or directory from a Modal volume.

    Args:
        params: Dictionary containing:
            - volume_name: Name of the Modal volume
            - path: Path within the volume to delete
            - recursive: Whether to delete recursively (for directories)

    Returns:
        Dictionary with deletion status
    """
    modal = _get_modal_client()

    volume_name = str(params.get("volume_name", "")).strip()
    path = str(params.get("path", "")).strip()
    recursive = bool(params.get("recursive", False))

    if not volume_name:
        raise BridgeRPCError(
            code="INVALID_ARGS",
            message="`volume_name` is required.",
        )
    if not path:
        raise BridgeRPCError(
            code="INVALID_ARGS",
            message="`path` is required.",
        )

    try:
        volume = modal.Volume.from_name(volume_name, version=2)

        full_path = f"/{path.lstrip('/')}"
        with volume.batch_write() as batch:
            batch.remove_file(full_path, recursive=recursive)

        return {
            "ok": True,
            "path": full_path,
            "volume_name": volume_name,
        }
    except Exception as exc:
        raise BridgeRPCError(
            code="VOLUME_DELETE_ERROR",
            message=f"Failed to delete from volume: {exc}",
        ) from exc


def memory_read(params: dict[str, Any]) -> dict[str, Any]:
    """Read from persistent memory at /data/memory.

    This is a convenience method for reading from the default
    Modal volume's memory location.

    Args:
        params: Dictionary containing:
            - key: Memory key (filename without .json extension)
            - volume_name: Optional volume name (uses default if not provided)

    Returns:
        Dictionary with memory content
    """
    from fleet_rlm.utils.modal import get_default_volume_name

    key = str(params.get("key", "")).strip()
    volume_name = (
        str(params.get("volume_name", "")).strip() or get_default_volume_name()
    )

    if not key:
        raise BridgeRPCError(
            code="INVALID_ARGS",
            message="`key` is required.",
        )

    # Sanitize key for filesystem safety
    safe_key = "".join(c for c in key if c.isalnum() or c in "-_").lower()
    if not safe_key:
        safe_key = "unnamed"

    path = f"memory/{safe_key}.json"

    # Delegate to volume_read
    return volume_read(
        {
            "volume_name": volume_name,
            "path": path,
            "encoding": "utf-8",
        }
    )


def memory_write(params: dict[str, Any]) -> dict[str, Any]:
    """Write to persistent memory at /data/memory.

    This is a convenience method for writing to the default
    Modal volume's memory location.

    Args:
        params: Dictionary containing:
            - key: Memory key (filename without .json extension)
            - data: Data to store (JSON serializable)
            - volume_name: Optional volume name (uses default if not provided)

    Returns:
        Dictionary with write status
    """
    from fleet_rlm.utils.modal import get_default_volume_name

    key = str(params.get("key", "")).strip()
    data = params.get("data")
    volume_name = (
        str(params.get("volume_name", "")).strip() or get_default_volume_name()
    )

    if not key:
        raise BridgeRPCError(
            code="INVALID_ARGS",
            message="`key` is required.",
        )
    if data is None:
        raise BridgeRPCError(
            code="INVALID_ARGS",
            message="`data` is required.",
        )

    # Sanitize key for filesystem safety
    safe_key = "".join(c for c in key if c.isalnum() or c in "-_").lower()
    if not safe_key:
        safe_key = "unnamed"

    path = f"memory/{safe_key}.json"
    content = json.dumps(data, ensure_ascii=False, indent=2)

    # Delegate to volume_write
    result = volume_write(
        {
            "volume_name": volume_name,
            "path": path,
            "content": content,
            "encoding": "utf-8",
        }
    )

    return {
        **result,
        "key": safe_key,
    }


def memory_list(params: dict[str, Any]) -> dict[str, Any]:
    """List all memory keys in persistent memory.

    Args:
        params: Dictionary containing:
            - volume_name: Optional volume name (uses default if not provided)
            - prefix: Optional prefix to filter keys

    Returns:
        Dictionary with list of memory keys
    """
    from fleet_rlm.utils.modal import get_default_volume_name

    volume_name = (
        str(params.get("volume_name", "")).strip() or get_default_volume_name()
    )
    prefix = str(params.get("prefix", "")).strip()

    result = volume_list(
        {
            "volume_name": volume_name,
            "prefix": "memory/",
            "recursive": False,
        }
    )

    keys = []
    for entry in result.get("files", []):
        if entry["type"] == "file" and entry["path"].endswith(".json"):
            # Extract key from path (memory/key.json -> key)
            key = Path(entry["path"]).stem
            if not prefix or key.startswith(prefix):
                keys.append(
                    {
                        "key": key,
                        "mtime": entry["mtime"],
                        "size": entry["size"],
                    }
                )

    return {
        "keys": keys,
        "count": len(keys),
        "volume_name": volume_name,
    }


def sandbox_list(params: dict[str, Any]) -> dict[str, Any]:
    """List active Modal sandboxes.

    Args:
        params: Dictionary containing:
            - app_name: Optional app name to filter by
            - tag: Optional tag to filter by

    Returns:
        Dictionary with list of sandboxes
    """
    modal = _get_modal_client()

    app_name = str(params.get("app_name", "")).strip() or None
    tag = str(params.get("tag", "")).strip() or None

    try:
        sandboxes = []
        for sb in modal.Sandbox.list():
            if app_name and sb.app_name != app_name:
                continue
            if tag and tag not in sb.tags:
                continue

            sandboxes.append(
                {
                    "id": sb.object_id,
                    "app_name": sb.app_name,
                    "tags": sb.tags,
                    "status": sb.status,
                    "created_at": sb.created_at.isoformat() if sb.created_at else None,
                }
            )

        return {
            "sandboxes": sandboxes,
            "count": len(sandboxes),
        }
    except Exception as exc:
        raise BridgeRPCError(
            code="SANDBOX_LIST_ERROR",
            message=f"Failed to list sandboxes: {exc}",
        ) from exc


def sandbox_exec(params: dict[str, Any]) -> dict[str, Any]:
    """Execute a command in a Modal sandbox.

    Note: This creates a new sandbox with the command and returns the result.
    For interactive sessions, use the existing interpreter mechanisms.

    Args:
        params: Dictionary containing:
            - command: Command to execute
            - image: Optional image name (default: python:3.11-slim)
            - volume_name: Optional volume to mount
            - timeout: Timeout in seconds (default: 60)
            - app_name: Optional app name

    Returns:
        Dictionary with execution result
    """
    modal = _get_modal_client()

    command = str(params.get("command", "")).strip()
    image = str(params.get("image", "python:3.11-slim")).strip()
    volume_name = str(params.get("volume_name", "")).strip() or None
    timeout = int(params.get("timeout", 60))
    app_name = str(params.get("app_name", "")).strip() or "bridge-exec"

    if not command:
        raise BridgeRPCError(
            code="INVALID_ARGS",
            message="`command` is required.",
        )

    try:
        # Ensure app exists
        app = modal.App.lookup(app_name, create_if_missing=True)

        # Prepare volume mount if specified
        volumes = {}
        if volume_name:
            vol = modal.Volume.from_name(volume_name, version=2)
            volumes = {"/data": vol}

        # Create and run sandbox
        sb = modal.Sandbox.create(
            app=app,
            image=modal.Image.from_registry(image),
            command=["sh", "-c", command],
            volumes=volumes,
            timeout=timeout,
        )

        # Wait for completion
        sb.wait()

        # Get output
        stdout = sb.stdout.read()
        stderr = sb.stderr.read()
        returncode = sb.returncode

        return {
            "ok": returncode == 0,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "sandbox_id": sb.object_id,
        }
    except Exception as exc:
        raise BridgeRPCError(
            code="SANDBOX_EXEC_ERROR",
            message=f"Failed to execute in sandbox: {exc}",
        ) from exc


def volume_info(params: dict[str, Any]) -> dict[str, Any]:
    """Get information about a Modal volume.

    Args:
        params: Dictionary containing:
            - volume_name: Name of the Modal volume

    Returns:
        Dictionary with volume information
    """
    modal = _get_modal_client()

    volume_name = str(params.get("volume_name", "")).strip()

    if not volume_name:
        raise BridgeRPCError(
            code="INVALID_ARGS",
            message="`volume_name` is required.",
        )

    try:
        volume = modal.Volume.from_name(volume_name, version=2)

        # Get volume stats by listing root
        try:
            entries = volume.listdir("/")
            file_count = sum(1 for e in entries if not e.is_directory)
            dir_count = sum(1 for e in entries if e.is_directory)
        except Exception:
            file_count = 0
            dir_count = 0

        return {
            "name": volume_name,
            "version": 2,
            "exists": True,
            "file_count": file_count,
            "dir_count": dir_count,
        }
    except Exception as exc:
        raise BridgeRPCError(
            code="VOLUME_INFO_ERROR",
            message=f"Failed to get volume info: {exc}",
        ) from exc
