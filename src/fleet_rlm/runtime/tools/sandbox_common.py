"""Shared helpers for sandbox-oriented tool builders."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, cast

from fleet_rlm.runtime.execution.interpreter_protocol import RLMInterpreterProtocol
from fleet_rlm.runtime.execution.storage_paths import (
    RuntimeStorageRoots,
    runtime_storage_roots,
)

from .shared import aexecute_submit, execute_submit
from .modal_volumes import resolve_mounted_volume_path

if TYPE_CHECKING:
    from ..agent.chat_agent import RLMReActChatAgent
    from fleet_rlm.integrations.providers.daytona.runtime import DaytonaSandboxSession


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


def _persistent_roots(ctx: _SandboxToolContext) -> RuntimeStorageRoots:
    """Return canonical durable storage roots for the active backend."""
    return runtime_storage_roots(cast(RLMInterpreterProtocol, ctx.agent.interpreter))


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


async def _aget_daytona_session(
    ctx: _SandboxToolContext,
) -> DaytonaSandboxSession | None:
    try:
        from fleet_rlm.integrations.providers.daytona.interpreter import (
            DaytonaInterpreter,
        )
    except Exception:
        return None

    interpreter = ctx.agent.interpreter
    if not isinstance(interpreter, DaytonaInterpreter):
        return None
    try:
        return await interpreter._aensure_session()
    except Exception:
        logger.debug("Daytona session unavailable for workspace probe", exc_info=True)
        return None


def _get_daytona_session_sync(
    ctx: _SandboxToolContext,
) -> DaytonaSandboxSession | None:
    try:
        from fleet_rlm.integrations.providers.daytona.interpreter import (
            DaytonaInterpreter,
        )
    except Exception:
        return None

    interpreter = getattr(ctx.agent, "interpreter", None)
    if not isinstance(interpreter, DaytonaInterpreter):
        return None
    try:
        return interpreter._ensure_session_sync()
    except Exception:
        logger.debug("Daytona session unavailable for workspace probe", exc_info=True)
        return None


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


async def _adaytona_read_text(daytona_session: DaytonaSandboxSession, path: str) -> str:
    return await daytona_session.aread_file(path)


async def _adaytona_write_text(
    daytona_session: DaytonaSandboxSession,
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


async def _adaytona_list_items(
    daytona_session: DaytonaSandboxSession, path: str
) -> list[dict[str, str]]:
    entries = await daytona_session.alist_files(path)
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


def _document_load_result(
    ctx: _SandboxToolContext,
    *,
    alias: str,
    path: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx.agent._set_document(alias, text)
    ctx.agent.active_alias = alias
    response = {
        "status": "ok",
        "alias": alias,
        "path": path,
        "chars": len(text),
        "lines": len(text.splitlines()),
    }
    if metadata and metadata.get("source_type") != "text":
        response.update(metadata)
    return response


def _load_daytona_workspace_text_sync(
    ctx: _SandboxToolContext,
    *,
    path: str,
) -> tuple[str, str] | None:
    raw_path = str(path or "").strip()
    if not raw_path:
        return None

    candidate = PurePosixPath(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None

    session = _get_daytona_session_sync(ctx)
    if session is None:
        return None

    workspace_path = str(getattr(session, "workspace_path", "") or "").strip()
    if not workspace_path:
        return None

    resolved_path = str(PurePosixPath(workspace_path) / candidate)
    parent_path = str(PurePosixPath(resolved_path).parent)
    file_name = PurePosixPath(resolved_path).name

    try:
        entries = session.list_files(parent_path)
    except Exception as exc:
        if _is_daytona_missing_file_error(exc):
            return None
        raise

    has_file = False
    for entry in entries:
        if str(getattr(entry, "name", "") or "") != file_name:
            continue
        if bool(getattr(entry, "is_dir", False)):
            return None
        has_file = True
        break

    if not has_file:
        return None

    try:
        text = str(session.read_file(resolved_path))
    except Exception as exc:
        if _is_daytona_missing_file_error(exc):
            return None
        raise
    return resolved_path, text


# ---------------------------------------------------------------------------
# Aggregate sandbox tool builder (merged from sandbox.py)
# ---------------------------------------------------------------------------


def build_sandbox_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build sandbox / buffer / volume tools bound to *agent*.

    Returns a list of ``dspy.Tool`` wrappers ready to be appended to the
    main tool list built by ``build_tool_list``.
    """
    from .infra_tools import build_lsp_tools, build_snapshot_tools
    from .sandbox_delegate_tools import build_rlm_delegate_tools
    from .sandbox_memory_tools import build_memory_intelligence_tools
    from .sandbox_storage_tools import build_storage_tools

    tools: list[Any] = []
    tools.extend(build_rlm_delegate_tools(agent))
    tools.extend(build_memory_intelligence_tools(agent))
    tools.extend(build_storage_tools(agent))
    tools.extend(build_snapshot_tools(agent))
    tools.extend(build_lsp_tools(agent))
    return tools
