"""Shared helpers for sandbox-oriented tool builders."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, cast

from fleet_rlm.runtime.execution.interpreter_protocol import RLMInterpreterProtocol
from fleet_rlm.runtime.execution.storage_paths import runtime_storage_roots

from .shared import aexecute_submit, execute_submit
from .volume_helpers import resolve_mounted_volume_path

if TYPE_CHECKING:
    from ..agent.chat_agent import RLMReActChatAgent


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _SandboxToolContext:
    """Shared context for sandbox and volume tool operations."""

    agent: RLMReActChatAgent


def _execute_submit_ctx(
    ctx: _SandboxToolContext,
    code: str,
    *,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return execute_submit(ctx.agent, code, variables=variables or {})


async def _aexecute_submit_ctx(
    ctx: _SandboxToolContext,
    code: str,
    *,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await aexecute_submit(ctx.agent, code, variables=variables or {})


def _resolve_path_or_error(
    *,
    path: str,
    default_root: str,
    allowed_root: str = "/data",
) -> tuple[str | None, dict[str, Any] | None]:
    try:
        return (
            resolve_mounted_volume_path(
                path,
                default_root=default_root,
                allowed_root=allowed_root,
            ),
            None,
        )
    except ValueError as exc:
        return None, {"status": "error", "error": str(exc)}


def _persistent_roots(ctx: _SandboxToolContext) -> tuple[str, str, str]:
    """Return the allowed root plus memory/workspace defaults for the backend."""
    roots = runtime_storage_roots(cast(RLMInterpreterProtocol, ctx.agent.interpreter))
    return roots.allowed_root, roots.memory_root, roots.workspace_root


def _reload_volume_best_effort(ctx: _SandboxToolContext) -> None:
    if ctx.agent.interpreter._volume:
        try:
            ctx.agent.interpreter.reload()
        except Exception:
            logger.warning("Best-effort volume reload failed", exc_info=True)


def _commit_volume_best_effort(ctx: _SandboxToolContext) -> None:
    if ctx.agent.interpreter._volume:
        try:
            ctx.agent.interpreter.commit()
        except Exception as exc:
            logger.exception("Best-effort volume commit failed: %s", exc)


async def _aget_daytona_session(ctx: _SandboxToolContext) -> Any | None:
    try:
        from fleet_rlm.integrations.providers.daytona.interpreter import (
            DaytonaInterpreter,
        )
    except Exception:
        return None

    interpreter = ctx.agent.interpreter
    if not isinstance(interpreter, DaytonaInterpreter):
        return None
    return await interpreter._aensure_session()


def _daytona_file_error(*, path: str, exc: Exception) -> dict[str, Any]:
    return {
        "status": "error",
        "error": f"{type(exc).__name__}: {exc}",
        "path": path,
    }


def _is_daytona_missing_file_error(exc: Exception) -> bool:
    if isinstance(exc, (FileNotFoundError, KeyError)):
        return True
    message = str(exc).lower()
    return "no such file" in message or "not found" in message


async def _adaytona_read_text(daytona_session: Any, path: str) -> str:
    return await daytona_session.aread_file(path)


async def _adaytona_write_text(
    daytona_session: Any,
    path: str,
    content: str,
    *,
    append: bool = False,
) -> str:
    payload = content
    if append:
        try:
            payload = await _adaytona_read_text(daytona_session, path) + content
        except Exception as exc:
            if not _is_daytona_missing_file_error(exc):
                raise
    return await daytona_session.awrite_file(path, payload)


async def _adaytona_list_items(daytona_session: Any, path: str) -> list[dict[str, str]]:
    if hasattr(daytona_session, "alist_files"):
        entries = await daytona_session.alist_files(path)
    elif hasattr(daytona_session, "list_files"):
        entries = daytona_session.list_files(path)
        if hasattr(entries, "__await__"):
            entries = await entries
    else:
        list_files = getattr(getattr(daytona_session, "sandbox", None), "fs", None)
        if list_files is None or not hasattr(list_files, "list_files"):
            raise AttributeError(
                f"{type(daytona_session).__name__!r} object has no async Daytona file-listing API"
            )
        entries = list_files.list_files(path)
        if hasattr(entries, "__await__"):
            entries = await entries
    items: list[dict[str, str]] = []
    for entry in entries:
        name = str(getattr(entry, "name", "") or "")
        if not name:
            continue
        items.append(
            {
                "name": name,
                "type": "dir" if bool(getattr(entry, "is_dir", False)) else "file",
            }
        )
    return items


def _buffer_volume_default_path(workspace_root: str) -> str:
    return str(PurePosixPath(workspace_root) / "buffers")
