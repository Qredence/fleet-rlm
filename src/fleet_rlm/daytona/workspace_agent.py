"""Stdlib-only remote Session Workspace agent and its host execution adapter."""

from __future__ import annotations

import json
import math
from importlib.resources import files
from typing import Any

from fleet_rlm.daytona.interpreter import DEFAULT_EXECUTION_TIMEOUT_S
from fleet_rlm.files.workspace_models import WorkspaceConflictError

# Bound provider Workspace Agent ``code_run``. Reuses the interpreter execution
# default (same numeric bound as Settings ``rlm_execution_timeout_s``).
# Not a public TOML knob — callers may override via ``timeout_s``.
WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S = DEFAULT_EXECUTION_TIMEOUT_S


class WorkspaceAgentStorageError(OSError):
    """Remote mounted-volume mutation failure."""


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


_WORKSPACE_AGENT_RUNTIME_NAME = "workspace_agent_runtime.py"
_WORKSPACE_AGENT_RUNTIME_SOURCE: str | None = None


def _workspace_agent_runtime_source() -> str:
    """Load packaged remote-agent source text (never import it as host behavior)."""
    global _WORKSPACE_AGENT_RUNTIME_SOURCE
    if _WORKSPACE_AGENT_RUNTIME_SOURCE is None:
        _WORKSPACE_AGENT_RUNTIME_SOURCE = (
            files("fleet_rlm.daytona").joinpath(_WORKSPACE_AGENT_RUNTIME_NAME).read_text(encoding="utf-8")
        )
    return _WORKSPACE_AGENT_RUNTIME_SOURCE


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
    preamble = "\n".join(
        (
            f"volume_root = {volume_root!r}",
            f"root = {root!r}",
            f"relative = {relative!r}",
            f"allow_missing = {allow_missing!r}",
            f"operation = {operation!r}",
            f"max_bytes = {int(max_bytes)!r}",
            f"limit = {int(limit)!r}",
            f"overwrite = {overwrite!r}",
            f"content_b64 = {content_b64!r}",
            f"after = {after!r}",
            f"offset = {int(offset)!r}",
            f"max_chars = {int(max_chars)!r}",
            f"total_file_bytes = {int(total_file_bytes)!r}",
            f"checksum = {checksum!r}",
            f"memory_id = {memory_id!r}",
            f"expected_sha256 = {expected_sha256!r}",
        )
    )
    return preamble + "\n" + _workspace_agent_runtime_source()


def decode_workspace_agent_response(response: Any, relative: str) -> dict[str, object]:
    if int(getattr(response, "exit_code", 1)) != 0:
        raise ValueError("workspace path is unsafe")
    try:
        payload = json.loads(str(getattr(response, "result", "")))
    except (TypeError, ValueError) as exc:
        raise ValueError("workspace path is unsafe") from exc
    if payload.get("ok") is not True:
        _raise_workspace_error(payload, relative)
    return payload


def _provider_code_run_timeout_s(timeout_s: float) -> int:
    """Convert a host timeout into Daytona's integer ``code_run`` timeout."""
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("workspace agent timeout_s must be positive")
    return max(1, math.ceil(timeout_s))


def run_workspace_agent(
    sandbox: Any,
    *,
    timeout_s: float = WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S,
    **arguments: Any,
) -> dict[str, object]:
    relative = str(arguments.get("relative") or "")
    code = build_workspace_agent_code(**arguments)
    response = sandbox.process.code_run(code, timeout=_provider_code_run_timeout_s(timeout_s))
    return decode_workspace_agent_response(response, relative)


async def run_workspace_agent_async(
    sandbox: Any,
    *,
    timeout_s: float = WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S,
    **arguments: Any,
) -> dict[str, object]:
    relative = str(arguments.get("relative") or "")
    code = build_workspace_agent_code(**arguments)
    response = await sandbox.process.code_run(code, timeout=_provider_code_run_timeout_s(timeout_s))
    return decode_workspace_agent_response(response, relative)


def _raise_workspace_error(payload: dict[str, object], relative: str) -> None:
    error = str(payload.get("error") or "")
    if error == "conflict":
        # Conflicts carry a stable detail (checksum_mismatch, not_empty,
        # ambiguous, missing) so tool hosts can render actionable feedback.
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
    if error == "unsupported_storage":
        raise WorkspaceAgentStorageError(str(payload.get("errno") or "unknown"))
    raise ValueError("workspace path is unsafe")
