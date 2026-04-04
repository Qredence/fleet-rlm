"""Shared helpers for sandbox-oriented tool builders."""

from __future__ import annotations

import json
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
    except ImportError:
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
    except ImportError:
        return None

    interpreter = getattr(ctx.agent, "interpreter", None)
    if not isinstance(interpreter, DaytonaInterpreter):
        return None
    try:
        return interpreter._ensure_session_sync()
    except Exception:
        logger.debug("Daytona session unavailable for workspace probe", exc_info=True)
        return None


def _is_daytona_interpreter(ctx: _SandboxToolContext) -> bool:
    try:
        from fleet_rlm.integrations.providers.daytona.interpreter import (
            DaytonaInterpreter,
        )
    except ImportError:
        return False

    return isinstance(getattr(ctx.agent, "interpreter", None), DaytonaInterpreter)


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
    from .sandbox_delegate_tools import build_rlm_delegate_tools
    from .sandbox_memory_tools import build_memory_intelligence_tools
    from .sandbox_storage_tools import build_storage_tools

    tools: list[Any] = []
    ctx = _SandboxToolContext(agent=agent)
    tools.extend(build_rlm_delegate_tools(agent))
    tools.extend(build_memory_intelligence_tools(agent))
    tools.extend(build_storage_tools(agent))
    if _is_daytona_interpreter(ctx):
        tools.extend(build_snapshot_tools(agent))
        tools.extend(build_lsp_tools(agent))
    return tools


# ---------------------------------------------------------------------------
# Snapshot tools (formerly infra_tools.py)
# ---------------------------------------------------------------------------

_infra_logger = logging.getLogger(__name__ + ".infra")


def build_snapshot_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Return ``dspy.Tool`` wrappers for Daytona snapshot management."""
    from dspy import Tool

    def list_snapshots(limit: int = 20) -> str:
        """List available Daytona snapshots with name, state, and image.

        Args:
            limit: Maximum number of snapshots to return (default 20).
        """
        from fleet_rlm.integrations.providers.daytona.snapshots import alist_snapshots
        from fleet_rlm.integrations.providers.daytona.runtime_helpers import (
            _run_async_compat,
        )

        try:
            items = _run_async_compat(alist_snapshots)
            return json.dumps(items[:limit], indent=2)
        except Exception as exc:
            return f"Error listing snapshots: {exc}"

    def resolve_snapshot(name: str = "fleet-rlm-base") -> str:
        """Check whether a named snapshot exists and is ACTIVE.

        Args:
            name: Snapshot name to resolve (default 'fleet-rlm-base').

        Returns:
            The snapshot name if active, or an explanation if unavailable.
        """
        from fleet_rlm.integrations.providers.daytona.snapshots import aresolve_snapshot
        from fleet_rlm.integrations.providers.daytona.runtime_helpers import (
            _run_async_compat,
        )

        try:
            result = _run_async_compat(aresolve_snapshot, name)
            if result:
                return f"Snapshot '{result}' is ACTIVE and ready."
            return f"Snapshot '{name}' is not available or not in ACTIVE state."
        except Exception as exc:
            return f"Error resolving snapshot: {exc}"

    return [
        Tool(
            list_snapshots,
            name="list_snapshots",
            desc="List available Daytona snapshots with name, state, and image info.",
        ),
        Tool(
            resolve_snapshot,
            name="resolve_snapshot",
            desc="Check if a named Daytona snapshot is ACTIVE and usable for sandbox creation.",
        ),
    ]


# ---------------------------------------------------------------------------
# LSP tools (formerly infra_tools.py)
# ---------------------------------------------------------------------------


def build_lsp_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Return ``dspy.Tool`` wrappers for Daytona LSP code intelligence."""
    from dspy import Tool

    def lsp_completions(file_path: str, line: int, character: int) -> str:
        """Get code completions at a specific position in a file.

        Uses the Daytona sandbox's native LSP server for Python code intelligence.

        Args:
            file_path: Path to the file in the sandbox workspace.
            line: Zero-based line number.
            character: Zero-based character offset.
        """
        from fleet_rlm.integrations.providers.daytona.runtime_helpers import (
            _run_async_compat,
        )

        async def _get_completions() -> str:
            session = getattr(agent, "_session", None) or getattr(
                agent, "interpreter", None
            )
            if session is None:
                return "No active sandbox session for LSP."
            sess = getattr(session, "_session", session)
            if not hasattr(sess, "create_lsp_server"):
                return "Current session does not support LSP."
            lsp = sess.create_lsp_server(language="python")
            try:
                await lsp.start()
                await lsp.did_open(file_path)
                items = await lsp.completions(file_path, line, character)
                result = []
                for item in getattr(items, "items", items) or []:
                    label = getattr(item, "label", str(item))
                    kind = getattr(item, "kind", "")
                    result.append(f"{label} ({kind})" if kind else label)
                return json.dumps(result[:30]) if result else "No completions found."
            finally:
                await lsp.stop()

        try:
            return _run_async_compat(_get_completions)
        except Exception as exc:
            return f"LSP error: {exc}"

    def lsp_document_symbols(file_path: str) -> str:
        """List symbols (functions, classes, variables) in a file.

        Uses the Daytona sandbox's native LSP server for symbol discovery.

        Args:
            file_path: Path to the file in the sandbox workspace.
        """
        from fleet_rlm.integrations.providers.daytona.runtime_helpers import (
            _run_async_compat,
        )

        async def _get_symbols() -> str:
            session = getattr(agent, "_session", None) or getattr(
                agent, "interpreter", None
            )
            if session is None:
                return "No active sandbox session for LSP."
            sess = getattr(session, "_session", session)
            if not hasattr(sess, "create_lsp_server"):
                return "Current session does not support LSP."
            lsp = sess.create_lsp_server(language="python")
            try:
                await lsp.start()
                await lsp.did_open(file_path)
                symbols = await lsp.document_symbols(file_path)
                result = []
                for sym in symbols or []:
                    name = getattr(sym, "name", str(sym))
                    kind = getattr(sym, "kind", "")
                    result.append(f"{name} ({kind})" if kind else name)
                return json.dumps(result[:50]) if result else "No symbols found."
            finally:
                await lsp.stop()

        try:
            return _run_async_compat(_get_symbols)
        except Exception as exc:
            return f"LSP error: {exc}"

    return [
        Tool(
            lsp_completions,
            name="lsp_completions",
            desc="Get code completions at a file:line:character position using sandbox LSP.",
        ),
        Tool(
            lsp_document_symbols,
            name="lsp_document_symbols",
            desc="List functions, classes, and variables in a file using sandbox LSP.",
        ),
    ]
