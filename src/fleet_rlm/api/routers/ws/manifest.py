"""Session manifest path and volume I/O helpers for websocket chat state."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from fleet_rlm.runtime.execution.profiles import ExecutionProfile

from .helpers import _sanitize_id
from .types import ChatAgentProtocol


def _is_final_output(result: Any) -> bool:
    from dspy.primitives import FinalOutput

    return isinstance(result, FinalOutput)


def _manifest_path(workspace_id: str, user_id: str, session_id: str) -> str:
    safe_session_id = _sanitize_id(session_id, "default-session")
    return (
        f"workspaces/{workspace_id}/users/{user_id}/memory/"
        f"react-session-{safe_session_id}.json"
    )


async def _aget_daytona_session(agent: ChatAgentProtocol) -> Any | None:
    try:
        from fleet_rlm.integrations.providers.daytona.interpreter import (
            DaytonaInterpreter,
        )
    except Exception:
        return None

    interpreter = getattr(agent, "interpreter", None)
    if not isinstance(interpreter, DaytonaInterpreter):
        return None
    return await interpreter._aensure_session()


def _persistent_storage_path(interpreter: Any, path: str) -> str:
    candidate = PurePosixPath(path)
    if candidate.is_absolute():
        return str(candidate)
    mount_root = str(getattr(interpreter, "volume_mount_path", "/data") or "/data")
    return str(PurePosixPath(mount_root) / candidate)


async def load_manifest_from_volume(agent: ChatAgentProtocol, path: str) -> dict:
    """Best-effort manifest load from interpreter volume storage."""
    interpreter = agent.interpreter
    if interpreter is None:
        return {}
    daytona_session = await _aget_daytona_session(agent)
    if daytona_session is not None:
        storage_path = _persistent_storage_path(interpreter, path)
        try:
            text = await daytona_session.aread_file(storage_path)
        except Exception:
            return {}
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    result = await interpreter.aexecute(
        "text = load_from_volume(path)\nSUBMIT(text=text)",
        variables={"path": path},
        execution_profile=ExecutionProfile.MAINTENANCE,
    )
    if not _is_final_output(result):
        return {}
    output = getattr(result, "output", None)
    output = output if isinstance(output, dict) else {}
    text = str(output.get("text", ""))
    if not text or text.startswith("[file not found:") or text.startswith("[error:"):
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
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
