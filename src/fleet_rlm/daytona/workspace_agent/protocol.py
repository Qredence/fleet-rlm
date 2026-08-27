"""Protocol and source-generation seam for the mounted Workspace Agent.

This module only builds and validates the bounded wire artifact.  The remote
``runtime.py`` file is loaded as text and is never imported as host behavior.
Host transport/session ownership lives in :mod:`workspace_agent.client`.
"""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from typing import Any

from fleet_rlm.daytona.interpreter import DEFAULT_EXECUTION_TIMEOUT_S
from fleet_rlm.files.workspace_models import WorkspaceConflictError

WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S = DEFAULT_EXECUTION_TIMEOUT_S
WORKSPACE_AGENT_PROTOCOL_VERSION = "fleet.workspace-agent/v1"
WORKSPACE_AGENT_INSTALL_PATH = "/home/daytona/fleet_rlm_workspace_agent_v1.py"
WORKSPACE_AGENT_MODULE_NAME = "fleet_rlm_workspace_agent_v1"
WORKSPACE_AGENT_REQUEST_MAX_BYTES = 16 * 1024 * 1024
WORKSPACE_AGENT_RESPONSE_MAX_BYTES = 16 * 1024 * 1024
WORKSPACE_AGENT_SUPPORTED_OPERATIONS = (
    "list",
    "stat",
    "tail_read",
    "read",
    "read_page",
    "append",
    "memory_migrate",
    "memory_append",
    "memory_edit",
    "memory_delete",
    "unlink",
    "delete",
    "patch",
    "write",
)


class WorkspaceAgentStorageError(OSError):
    """Remote mounted-volume mutation failure."""


class WorkspaceAgentProtocolError(RuntimeError):
    """Installed Workspace Agent is absent, incompatible, or tampered with."""


_PATH_ERRORS = {
    "not_found": FileNotFoundError,
    "is_directory": IsADirectoryError,
    "not_directory": NotADirectoryError,
}
_VALUE_ERRORS = {
    "read_bound": "workspace file exceeds read bound",
    "too_large": "workspace file exceeds maximum size",
    "invalid_record": "workspace memory record is invalid",
    "invalid_utf8": "workspace file is not valid UTF-8",
    "cursor": "workspace cursor is invalid",
}
_PROTOCOL_ERRORS = frozenset({"protocol_mismatch", "request_invalid"})
_RUNTIME_NAME = "runtime.py"
_RUNTIME_SOURCE: str | None = None


def workspace_agent_runtime_source() -> str:
    """Load the packaged remote-agent source as text only."""
    global _RUNTIME_SOURCE
    if _RUNTIME_SOURCE is None:
        _RUNTIME_SOURCE = files("fleet_rlm.daytona.workspace_agent").joinpath(_RUNTIME_NAME).read_text(encoding="utf-8")
    return _RUNTIME_SOURCE


# Historical private name retained for source/test compatibility.
def _workspace_agent_runtime_source() -> str:
    return workspace_agent_runtime_source()


def build_workspace_agent_code(
    *,
    volume_root: str,
    root: str,
    operation: str,
    relative: str,
    allow_missing: bool,
    max_bytes: int,
    limit: int,
    overwrite: bool,
    content_b64: str,
    after: str = "",
    offset: int = 0,
    max_chars: int = 0,
    total_file_bytes: int = 0,
    checksum: bool = False,
    memory_id: str = "",
    expected_sha256: str = "",
) -> str:
    """Build the full fallback source plus one bounded operation request."""
    request = {
        "protocol_version": WORKSPACE_AGENT_PROTOCOL_VERSION,
        "volume_root": volume_root,
        "root": root,
        "relative": relative,
        "allow_missing": allow_missing,
        "operation": operation,
        "max_bytes": int(max_bytes),
        "limit": int(limit),
        "overwrite": overwrite,
        "content_b64": content_b64,
        "after": after,
        "offset": int(offset),
        "max_chars": int(max_chars),
        "total_file_bytes": int(total_file_bytes),
        "checksum": checksum,
        "memory_id": memory_id,
        "expected_sha256": expected_sha256,
    }
    encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > WORKSPACE_AGENT_REQUEST_MAX_BYTES:
        raise WorkspaceAgentProtocolError("Workspace Agent request exceeds its bound")
    return (
        workspace_agent_runtime_source()
        + "\n\nprint(json.dumps(handle(json.loads("
        + repr(encoded)
        + ")), ensure_ascii=False, separators=(',', ':')))\n"
    )


def _workspace_agent_runtime_checksum() -> str:
    """Return the SHA-256 digest of the packaged remote source."""
    return hashlib.sha256(workspace_agent_runtime_source().encode("utf-8")).hexdigest()


def build_installed_workspace_agent_source() -> str:
    """Return a syntax-checked source artifact suitable for upload."""
    source = workspace_agent_runtime_source()
    try:
        compile(source, _RUNTIME_NAME, "exec")
    except SyntaxError as exc:
        raise WorkspaceAgentProtocolError("Workspace Agent runtime is invalid") from exc
    return source


def build_workspace_agent_request_code(
    arguments: dict[str, object],
    *,
    install_path: str = WORKSPACE_AGENT_INSTALL_PATH,
) -> str:
    """Build a compact request shim for an installed Workspace Agent."""
    request = {"protocol_version": WORKSPACE_AGENT_PROTOCOL_VERSION, **arguments}
    encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > WORKSPACE_AGENT_REQUEST_MAX_BYTES:
        raise WorkspaceAgentProtocolError("Workspace Agent request exceeds its bound")
    return (
        "import importlib.util as _u, json as _j\n"
        f"_s = _u.spec_from_file_location({WORKSPACE_AGENT_MODULE_NAME!r}, {install_path!r})\n"
        "if _s is None or _s.loader is None: raise RuntimeError('workspace agent unavailable')\n"
        "_m = _u.module_from_spec(_s); _s.loader.exec_module(_m)\n"
        f"try:\n    _v = _m.handle(_j.loads({encoded!r}))\n"
        "except SystemExit:\n    pass\n"
        "else:\n    print(_j.dumps(_v, ensure_ascii=False, separators=(',', ':')))\n"
    )


def decode_workspace_agent_response(response: Any, relative: str) -> dict[str, object]:
    """Decode one provider response and map errors to stable host exceptions."""
    if int(getattr(response, "exit_code", 1)) != 0:
        raise ValueError("workspace path is unsafe")
    try:
        payload = json.loads(str(getattr(response, "result", "")))
    except (TypeError, ValueError) as exc:
        raise ValueError("workspace path is unsafe") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        _raise_workspace_error(payload if isinstance(payload, dict) else {}, relative)
    return payload


def _raise_workspace_error(payload: dict[str, object], relative: str) -> None:
    error = str(payload.get("error") or "")
    if error == "conflict":
        detail = payload.get("detail")
        if isinstance(detail, str) and detail:
            raise WorkspaceConflictError(relative, detail=detail)
        raise FileExistsError(relative)
    path_error = _PATH_ERRORS.get(error)
    if path_error is not None:
        raise path_error(relative)
    value_error = _VALUE_ERRORS.get(error)
    if value_error is not None:
        raise ValueError(value_error)
    if error in _PROTOCOL_ERRORS:
        raise WorkspaceAgentProtocolError(error)
    if error == "unsupported_storage":
        raise WorkspaceAgentStorageError(str(payload.get("errno") or "unknown"))
    raise ValueError("workspace path is unsafe")


# Public aliases make the protocol artifact seam explicit while preserving the
# old private helper names used by deterministic tests.
workspace_agent_runtime_checksum = _workspace_agent_runtime_checksum

__all__ = [
    "WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S",
    "WORKSPACE_AGENT_INSTALL_PATH",
    "WORKSPACE_AGENT_MODULE_NAME",
    "WORKSPACE_AGENT_PROTOCOL_VERSION",
    "WORKSPACE_AGENT_REQUEST_MAX_BYTES",
    "WORKSPACE_AGENT_RESPONSE_MAX_BYTES",
    "WORKSPACE_AGENT_SUPPORTED_OPERATIONS",
    "WorkspaceAgentProtocolError",
    "WorkspaceAgentStorageError",
    "_workspace_agent_runtime_checksum",
    "_workspace_agent_runtime_source",
    "build_installed_workspace_agent_source",
    "build_workspace_agent_code",
    "build_workspace_agent_request_code",
    "decode_workspace_agent_response",
    "workspace_agent_runtime_checksum",
    "workspace_agent_runtime_source",
]
