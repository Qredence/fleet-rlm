"""Native Daytona sandbox filesystem operations."""

from __future__ import annotations

import inspect
from typing import Any


async def _maybe_await(val: Any) -> Any:
    if inspect.isawaitable(val):
        return await val
    return val


def _get_fs(sandbox: Any) -> Any:
    """Extract filesystem client from a Daytona sandbox or return the object itself."""
    return getattr(sandbox, "fs", sandbox)


async def read_file(sandbox: Any, path: str) -> bytes:
    """Download and return the bytes of a file inside the sandbox."""
    fs = _get_fs(sandbox)
    result = fs.download_file(path)
    data = await _maybe_await(result)
    if isinstance(data, str):
        return data.encode("utf-8")
    return bytes(data)


async def write_file(sandbox: Any, path: str, data: bytes) -> None:
    """Upload bytes to a specified file path inside the sandbox."""
    fs = _get_fs(sandbox)
    result = fs.upload_file(data, path)
    await _maybe_await(result)


async def list_files(sandbox: Any, path: str, *, depth: int = 1) -> list[Any]:
    """List directory entries at the given path inside the sandbox."""
    fs = _get_fs(sandbox)
    try:
        entries = await _maybe_await(fs.list_files(path, depth=depth))
    except TypeError:
        entries = await _maybe_await(fs.list_files(path))
    return list(entries or [])


async def delete_file(sandbox: Any, path: str) -> None:
    """Delete a file at the specified path inside the sandbox."""
    fs = _get_fs(sandbox)
    result = fs.delete_file(path)
    await _maybe_await(result)


async def get_file_info(sandbox: Any, path: str) -> Any:
    """Retrieve file metadata for a path inside the sandbox."""
    fs = _get_fs(sandbox)
    result = fs.get_file_info(path)
    return await _maybe_await(result)


async def create_folder(sandbox: Any, path: str, mode: str = "755") -> None:
    """Create a folder at the specified path inside the sandbox."""
    fs = _get_fs(sandbox)
    result = fs.create_folder(path, mode=mode)
    await _maybe_await(result)
