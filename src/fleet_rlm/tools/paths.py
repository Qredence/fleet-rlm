"""Shared POSIX path safety helpers for fleet_rlm.tools."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote

ENCODED_TRAVERSAL_TOKENS = ("%2e%2e", "%2f", "%5c")
DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:")


class PathSafetyError(ValueError):
    """Raised when a requested path violates filesystem policy."""


class FilesystemSafetyError(PathSafetyError):
    """Raised when a requested Daytona path violates filesystem policy."""


def reject_encoded_traversal_tokens(raw: str) -> None:
    lowered = raw.lower()
    if any(token in lowered for token in ENCODED_TRAVERSAL_TOKENS):
        raise FilesystemSafetyError("Path traversal is not allowed.")


def reject_backslash_paths(raw: str) -> None:
    if "\\" in raw:
        raise FilesystemSafetyError("Backslash paths are not allowed.")


def reject_host_drive_paths(raw: str) -> None:
    if DRIVE_PATH_RE.match(raw):
        raise FilesystemSafetyError("Host drive paths are not allowed.")


def validate_relative_posix_path(
    path: str,
    *,
    empty_message: str = "Path must not be empty.",
    traversal_message: str = "Path traversal is not allowed.",
    absolute_message: str = "Path must stay inside the approved root.",
    backslash_message: str = "Backslash paths are not allowed.",
) -> PurePosixPath:
    """Validate a relative POSIX path segment under an approved root."""
    raw = str(path or "").strip()
    if not raw:
        raise PathSafetyError(empty_message)
    try:
        reject_encoded_traversal_tokens(raw)
        reject_backslash_paths(raw)
    except PathSafetyError as exc:
        raise PathSafetyError(traversal_message if "traversal" in str(exc) else backslash_message) from exc
    decoded = unquote(raw)
    if "\\" in decoded:
        raise PathSafetyError(backslash_message)
    candidate = PurePosixPath(decoded)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise PathSafetyError(absolute_message if candidate.is_absolute() else traversal_message)
    return candidate


def safe_join_daytona_path(path: str, *, base: str) -> tuple[str, str]:
    """Resolve *path* under *base* and reject traversal or host-style escapes."""
    raw = str(path or ".").strip() or "."
    reject_encoded_traversal_tokens(raw)
    reject_backslash_paths(raw)
    reject_host_drive_paths(raw)

    decoded = unquote(raw)
    reject_backslash_paths(decoded)
    candidate = PurePosixPath(decoded)
    if ".." in candidate.parts:
        raise FilesystemSafetyError("Path traversal is not allowed.")

    normalized_base = str(PurePosixPath(base)).rstrip("/") or "/"
    if candidate.is_absolute():
        candidate_text = str(candidate)
        if candidate_text != normalized_base and not candidate_text.startswith(f"{normalized_base}/"):
            raise FilesystemSafetyError("Absolute paths must stay inside the selected Daytona root.")
        resolved = candidate_text
    else:
        resolved = str(PurePosixPath(normalized_base) / candidate)

    if resolved != normalized_base and not resolved.startswith(f"{normalized_base}/"):
        raise FilesystemSafetyError("Resolved path escapes the selected Daytona root.")
    return resolved, resolved


def assert_no_detectable_symlink_escape(session: Any | None, *, path: str, base: str) -> None:
    """Best-effort guard against symlink escapes when metadata is available."""
    from fleet_rlm.utils.async_compat import _run_async_compat

    if session is None:
        return
    info: Any | None = None
    method = getattr(session, "get_file_info", None)
    if callable(method):
        try:
            info = method(path)
        except Exception:
            info = None
    if info is None:
        fs = getattr(getattr(session, "sandbox", None), "fs", None)
        method = getattr(fs, "get_file_info", None)
        if callable(method):
            try:
                info = _run_async_compat(method, path)
            except Exception:
                info = None
    if info is None:
        return
    normalized_base = str(PurePosixPath(base)).rstrip("/") or "/"
    for attr in ("real_path", "resolved_path", "target_path"):
        real_path = getattr(info, attr, None)
        if not real_path and isinstance(info, dict):
            real_path = info.get(attr)
        if not real_path:
            continue
        resolved = str(PurePosixPath(str(real_path)))
        if resolved != normalized_base and not resolved.startswith(f"{normalized_base}/"):
            raise FilesystemSafetyError("Path resolves outside the selected Daytona root.")


__all__ = [
    "DRIVE_PATH_RE",
    "ENCODED_TRAVERSAL_TOKENS",
    "FilesystemSafetyError",
    "PathSafetyError",
    "assert_no_detectable_symlink_escape",
    "reject_backslash_paths",
    "reject_encoded_traversal_tokens",
    "reject_host_drive_paths",
    "safe_join_daytona_path",
    "validate_relative_posix_path",
]
