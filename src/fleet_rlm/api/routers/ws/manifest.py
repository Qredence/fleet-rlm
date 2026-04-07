"""Session manifest path and volume I/O helpers for websocket chat state."""

from __future__ import annotations

import json
import logging
import posixpath
from pathlib import PurePosixPath
from typing import Any

from fleet_rlm.runtime.execution.profiles import ExecutionProfile

from ...server_utils import sanitize_id as _sanitize_id
from .types import ChatAgentProtocol

logger = logging.getLogger(__name__)


def _is_final_output(result: Any) -> bool:
    from dspy.primitives import FinalOutput

    return isinstance(result, FinalOutput)


def _manifest_path(workspace_id: str, user_id: str, session_id: str) -> str:
    safe_session_id = _sanitize_id(session_id, "default-session")
    return (
        f"meta/workspaces/{workspace_id}/users/{user_id}/"
        f"react-session-{safe_session_id}.json"
    )


def _legacy_manifest_path(path: str) -> str | None:
    normalized = str(path or "").strip()
    if not normalized.startswith("meta/"):
        return None
    stripped = normalized.removeprefix("meta/")
    parts = PurePosixPath(stripped).parts
    if len(parts) < 5 or parts[0] != "workspaces" or parts[2] != "users":
        return stripped

    user_prefix = PurePosixPath(*parts[:4])
    remainder = parts[4:]
    if not remainder:
        return stripped
    if remainder[0] == "memory":
        return stripped
    if remainder[0].startswith("react-session-"):
        return str(user_prefix / "memory" / PurePosixPath(*remainder))
    return stripped


async def _aget_daytona_session(agent: ChatAgentProtocol) -> Any | None:
    try:
        from fleet_rlm.integrations.daytona.interpreter import (
            DaytonaInterpreter,
        )
    except ImportError:
        return None

    interpreter = getattr(agent, "interpreter", None)
    if not isinstance(interpreter, DaytonaInterpreter):
        return None
    aget_session = getattr(interpreter, "aget_session", None)
    if aget_session is None or not callable(aget_session):
        return None
    return await aget_session()


def _persistent_storage_path(interpreter: Any, path: str) -> str:
    raw_root = str(getattr(interpreter, "volume_mount_path", "/data") or "/data")
    mount_root = posixpath.normpath(raw_root)
    candidate = PurePosixPath(path)
    if candidate.is_absolute():
        resolved = posixpath.normpath(str(candidate))
    else:
        resolved = posixpath.normpath(str(PurePosixPath(mount_root) / candidate))
    if not resolved.startswith(mount_root + "/") and resolved != mount_root:
        raise ValueError(f"Path {path!r} resolves outside volume mount path.")
    return resolved


async def load_manifest_from_volume(agent: ChatAgentProtocol, path: str) -> dict:
    """Best-effort manifest load from interpreter volume storage."""
    interpreter = agent.interpreter
    if interpreter is None:
        return {}
    candidate_paths = [path]
    legacy_path = _legacy_manifest_path(path)
    if legacy_path is not None:
        candidate_paths.append(legacy_path)
    daytona_session = await _aget_daytona_session(agent)
    if daytona_session is not None:
        for candidate_path in candidate_paths:
            storage_path = _persistent_storage_path(interpreter, candidate_path)
            try:
                text = await daytona_session.aread_file(storage_path)
            except Exception:
                logger.debug(
                    "manifest_load_daytona_read_error",
                    extra={"path": storage_path},
                    exc_info=True,
                )
                continue
            if not text:
                continue
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}
    for candidate_path in candidate_paths:
        result = await interpreter.aexecute(
            "text = load_from_volume(path)\nSUBMIT(text=text)",
            variables={"path": candidate_path},
            execution_profile=ExecutionProfile.MAINTENANCE,
        )
        if not _is_final_output(result):
            continue
        output = getattr(result, "output", None)
        output = output if isinstance(output, dict) else {}
        text = str(output.get("text", ""))
        if (
            not text
            or text.startswith("[file not found:")
            or text.startswith("[error:")
        ):
            continue
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def save_manifest_to_volume(
    agent: ChatAgentProtocol,
    path: str,
    manifest: dict[str, Any],
) -> str | None:
    """Best-effort manifest save to interpreter volume storage."""
    interpreter = agent.interpreter
    if interpreter is None:
        return None
    payload = json.dumps(manifest, ensure_ascii=False, default=str)
    daytona_session = await _aget_daytona_session(agent)
    if daytona_session is not None:
        storage_path = _persistent_storage_path(interpreter, path)
        try:
            return await daytona_session.awrite_file(storage_path, payload)
        except Exception:
            logger.warning(
                "manifest_save_daytona_write_error",
                extra={"path": storage_path},
                exc_info=True,
            )
            return None
    result = await interpreter.aexecute(
        "saved_path = save_to_volume(path, payload)\nSUBMIT(saved_path=saved_path)",
        variables={"path": path, "payload": payload},
        execution_profile=ExecutionProfile.MAINTENANCE,
    )
    if not _is_final_output(result):
        return None
    output = getattr(result, "output", None)
    output = output if isinstance(output, dict) else {}
    saved_path = str(output.get("saved_path", ""))
    if saved_path.startswith("["):
        return None
    return saved_path or None
