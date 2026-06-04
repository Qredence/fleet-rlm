"""Daytona manifest/volume I/O for runtime services."""

from __future__ import annotations

import json
import logging
import posixpath
import shlex
from pathlib import PurePosixPath
from typing import Any

from fleet_rlm.api.runtime_services.session_paths import (
    session_conversation_path,
    session_scratchpad_path,
    session_workspace_link_path,
)
from fleet_rlm.runtime.execution.interpreter_protocol import ExecutionProfile
from fleet_rlm.utils.identity import sanitize_id as _sanitize_id

logger = logging.getLogger(__name__)


def _is_final_output(result: Any) -> bool:
    from dspy.primitives import FinalOutput

    return isinstance(result, FinalOutput)


def _manifest_path(workspace_id: str, user_id: str, session_id: str) -> str:
    _ = workspace_id, user_id
    conversation_path = session_conversation_path(session_id)
    if conversation_path is not None:
        return conversation_path
    safe_session_id = _sanitize_id(session_id, "default-session")
    return f"meta/workspaces/{workspace_id}/users/{user_id}/react-session-{safe_session_id}.json"


def _get_existing_daytona_session(agent: Any) -> Any | None:
    interpreter = getattr(agent, "interpreter", None)
    workspace = getattr(interpreter, "_workspace", None)
    if workspace is None:
        return None
    return getattr(workspace, "_session", None)


async def _aget_daytona_session(agent: Any, *, allow_create: bool = True) -> Any | None:
    try:
        from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter
    except ImportError:
        return None

    interpreter = getattr(agent, "interpreter", None)
    if not isinstance(interpreter, DaytonaInterpreter):
        return None
    if not allow_create:
        return _get_existing_daytona_session(agent)
    aget_session = getattr(interpreter, "aget_session", None)
    if aget_session is None or not callable(aget_session):
        return None
    return await aget_session()


async def release_idle_daytona_session(agent: Any) -> None:
    """Best-effort release of an already-created Daytona sandbox session."""
    interpreter = getattr(agent, "interpreter", None)
    if interpreter is None:
        return
    if _get_existing_daytona_session(agent) is None:
        return
    release_idle = getattr(interpreter, "arelease_idle_session", None)
    if callable(release_idle):
        try:
            await release_idle()
        except Exception:
            logger.warning("Failed to release idle Daytona session", exc_info=True)


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


def _session_workspace_target(daytona_session: Any, interpreter: Any) -> str:
    return str(
        getattr(daytona_session, "workspace_path", None)
        or getattr(interpreter, "workspace_path", None)
        or getattr(interpreter, "repo_path", None)
        or ""
    ).strip()


def _ensure_session_layout_command(*, scratchpad_path: str, workspace_link_path: str, workspace_target: str) -> str:
    return " ".join(
        [
            "mkdir",
            "-p",
            shlex.quote(scratchpad_path),
            "&&",
            "rm",
            "-rf",
            shlex.quote(workspace_link_path),
            "&&",
            "ln",
            "-s",
            shlex.quote(workspace_target),
            shlex.quote(workspace_link_path),
        ]
    )


async def ensure_session_volume_layout(
    agent: Any,
    session_id: str,
    *,
    allow_session_create: bool = True,
) -> dict[str, str]:
    """Ensure Phase 1 per-session scratchpad and workspace mapping exist on the volume."""
    interpreter = agent.interpreter
    if interpreter is None:
        return {}
    scratchpad_path = session_scratchpad_path(session_id)
    workspace_link_path = session_workspace_link_path(session_id)
    if scratchpad_path is None or workspace_link_path is None:
        return {}
    storage_scratchpad_path = _persistent_storage_path(interpreter, scratchpad_path)
    storage_workspace_link_path = _persistent_storage_path(interpreter, workspace_link_path)
    daytona_session = await _aget_daytona_session(agent, allow_create=allow_session_create)
    if daytona_session is None and not allow_session_create:
        return {
            "scratchpad_path": storage_scratchpad_path,
            "workspace_link_path": storage_workspace_link_path,
        }
    workspace_target = _session_workspace_target(daytona_session, interpreter)
    if not workspace_target:
        return {
            "scratchpad_path": storage_scratchpad_path,
            "workspace_link_path": storage_workspace_link_path,
        }
    if daytona_session is not None:
        process = getattr(getattr(daytona_session, "sandbox", None), "process", None)
        exec_command = getattr(process, "exec", None)
        if callable(exec_command):
            try:
                exec_command(
                    _ensure_session_layout_command(
                        scratchpad_path=storage_scratchpad_path,
                        workspace_link_path=storage_workspace_link_path,
                        workspace_target=workspace_target,
                    )
                )
                return {
                    "scratchpad_path": storage_scratchpad_path,
                    "workspace_link_path": storage_workspace_link_path,
                }
            except Exception as exc:
                logger.warning(
                    "ensure_session_volume_layout: Daytona exec_command failed, falling back to interpreter aexecute: %s",
                    exc,
                )
    await interpreter.aexecute(
        "\n".join(
            [
                "import os",
                "os.makedirs(scratchpad_path, exist_ok=True)",
                "if os.path.isdir(workspace_target):",
                "    if os.path.lexists(workspace_link_path):",
                "        if os.path.isdir(workspace_link_path) and not os.path.islink(workspace_link_path):",
                "            import shutil",
                "            shutil.rmtree(workspace_link_path)",
                "        else:",
                "            os.unlink(workspace_link_path)",
                "    os.symlink(workspace_target, workspace_link_path)",
                "else:",
                "    import warnings",
                "    warnings.warn(f'Workspace target {workspace_target} does not exist, skipping symlink creation')",
                "SUBMIT(scratchpad_path=scratchpad_path, workspace_link_path=workspace_link_path)",
            ]
        ),
        variables={
            "scratchpad_path": storage_scratchpad_path,
            "workspace_link_path": storage_workspace_link_path,
            "workspace_target": workspace_target,
        },
        execution_profile=ExecutionProfile.MAINTENANCE,
    )
    return {
        "scratchpad_path": storage_scratchpad_path,
        "workspace_link_path": storage_workspace_link_path,
    }


def _parse_manifest_text(text: str) -> dict[str, Any]:
    if not text or text.startswith("[file not found:") or text.startswith("[error:"):
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


async def load_manifest_from_volume(
    agent: Any,
    path: str,
    fallback_paths: list[str] | None = None,
    *,
    allow_session_create: bool = True,
) -> dict[str, Any]:
    """Best-effort manifest load from interpreter volume storage."""
    interpreter = agent.interpreter
    if interpreter is None:
        return {}
    candidate_paths = [path, *(fallback_paths or [])]
    daytona_session = await _aget_daytona_session(agent, allow_create=allow_session_create)
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
            parsed = _parse_manifest_text(text)
            if parsed:
                return parsed
        return {}
    if not allow_session_create:
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
        parsed = _parse_manifest_text(str(output.get("text", "")))
        if parsed:
            return parsed
    return {}


async def save_manifest_to_volume(
    agent: Any,
    path: str,
    manifest: dict[str, Any],
    *,
    allow_session_create: bool = True,
) -> str | None:
    """Best-effort manifest save to interpreter volume storage."""
    interpreter = agent.interpreter
    if interpreter is None:
        return None
    payload = json.dumps(manifest, ensure_ascii=False, default=str)
    daytona_session = await _aget_daytona_session(agent, allow_create=allow_session_create)
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
    if not allow_session_create:
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


__all__ = [
    "load_manifest_from_volume",
    "save_manifest_to_volume",
    "ensure_session_volume_layout",
    "release_idle_daytona_session",
    "_manifest_path",
    "_aget_daytona_session",
    "_persistent_storage_path",
    "_is_final_output",
]
