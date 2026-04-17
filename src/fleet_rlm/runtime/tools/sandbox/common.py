"""Shared helpers for sandbox-oriented tool builders."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, cast

from fleet_rlm.runtime.agent.tool_delegation import _sync_compatible_tool_callable
from fleet_rlm.runtime.execution.interpreter_protocol import RLMInterpreterProtocol
from fleet_rlm.runtime.execution.storage_paths import (
    RuntimeStorageRoots,
    runtime_storage_roots,
)
from fleet_rlm.utils.volume_tree import resolve_mounted_volume_path

from ..shared import aexecute_submit, execute_submit

if TYPE_CHECKING:
    from ...agent.chat_agent import RLMReActChatAgent
    from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxSession


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
    interpreter = getattr(ctx.agent, "interpreter", None)
    if not getattr(interpreter, "_volume", None):
        return
    reload_fn = getattr(interpreter, "reload", None)
    if callable(reload_fn):
        try:
            reload_fn()
        except Exception:
            logger.warning("Best-effort volume reload failed", exc_info=True)


def _commit_volume_best_effort(ctx: _SandboxToolContext) -> None:
    interpreter = getattr(ctx.agent, "interpreter", None)
    if not getattr(interpreter, "_volume", None):
        return
    commit_fn = getattr(interpreter, "commit", None)
    if callable(commit_fn):
        try:
            commit_fn()
        except Exception as exc:
            logger.exception("Best-effort volume commit failed: %s", exc)


async def _aget_daytona_session(
    ctx: _SandboxToolContext,
) -> DaytonaSandboxSession | None:
    try:
        from fleet_rlm.integrations.daytona.interpreter import (
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
        from fleet_rlm.integrations.daytona.interpreter import (
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
        from fleet_rlm.integrations.daytona.interpreter import (
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
    from .delegate import build_rlm_delegate_tools
    from .memory import build_memory_intelligence_tools
    from .storage import build_storage_tools

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
        from fleet_rlm.integrations.daytona.runtime import alist_snapshots
        from fleet_rlm.integrations.daytona.async_compat import _run_async_compat

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
        from fleet_rlm.integrations.daytona.runtime import aresolve_snapshot
        from fleet_rlm.integrations.daytona.async_compat import _run_async_compat

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
        from fleet_rlm.integrations.daytona.async_compat import _run_async_compat

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
        from fleet_rlm.integrations.daytona.async_compat import _run_async_compat

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


# ---------------------------------------------------------------------------
# Buffer tools (formerly buffer_tools.py)
# ---------------------------------------------------------------------------


def build_buffer_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build sandbox buffer and volume-load tools bound to *agent*."""
    from dspy import Tool

    ctx = _SandboxToolContext(agent=agent)
    tools: list[Any] = []

    async def read_buffer(name: str) -> dict[str, Any]:
        """Read the full contents of a sandbox buffer."""
        result = await _aexecute_submit_ctx(
            ctx,
            "SUBMIT(items=get_buffer(name))",
            variables={"name": name},
        )
        items = result.get("items", [])
        return {"status": "ok", "name": name, "items": items, "count": len(items)}

    async def clear_buffer(name: str = "") -> dict[str, Any]:
        """Clear one sandbox buffer (or all buffers when name is empty)."""
        if name:
            code = 'clear_buffer(name)\nSUBMIT(status="ok", scope="single", name=name)'
            variables: dict[str, Any] = {"name": name}
        else:
            code = 'clear_buffer()\nSUBMIT(status="ok", scope="all")'
            variables = {}
        return await _aexecute_submit_ctx(ctx, code, variables=variables)

    async def save_buffer_to_volume(name: str, path: str) -> dict[str, Any]:
        """Persist a sandbox buffer to persistent storage as JSON."""
        roots = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=roots.buffers_root,
            allowed_root=roots.allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session(ctx)
        if daytona_session is not None:
            result = await _aexecute_submit_ctx(
                ctx,
                "SUBMIT(items=get_buffer(name))",
                variables={"name": name},
            )
            items = result.get("items", [])
            payload = json.dumps(items, indent=2, ensure_ascii=False, default=str)
            try:
                saved_path = await _adaytona_write_text(
                    daytona_session,
                    resolved_path,
                    payload,
                )
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            return {"status": "ok", "saved_path": saved_path, "item_count": len(items)}

        code = """
import json
items = get_buffer(name)
payload = json.dumps(items, indent=2, ensure_ascii=False, default=str)
saved_path = save_to_volume(path, payload)
SUBMIT(status="ok", saved_path=saved_path, item_count=len(items))
"""
        result = await _aexecute_submit_ctx(
            ctx,
            code,
            variables={"name": name, "path": resolved_path},
        )
        if result.get("status") == "ok":
            _commit_volume_best_effort(ctx)
        return result

    async def load_text_from_volume(path: str, alias: str = "active") -> dict[str, Any]:
        """Load text from persistent storage into host-side document memory."""
        roots = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=roots.artifacts_root,
            allowed_root=roots.allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session(ctx)
        if daytona_session is not None:
            try:
                text = await _adaytona_read_text(daytona_session, resolved_path)
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            return _document_load_result(
                ctx,
                alias=alias,
                path=resolved_path,
                text=text,
            )

        _reload_volume_best_effort(ctx)

        result = await _aexecute_submit_ctx(
            ctx,
            'text = load_from_volume(path)\nSUBMIT(status="ok", text=text)',
            variables={"path": resolved_path},
        )
        text = str(result.get("text", ""))
        if text.startswith("[error:"):
            return {"status": "error", "error": text, "path": resolved_path}
        return _document_load_result(
            ctx,
            alias=alias,
            path=resolved_path,
            text=text,
        )

    async def process_document(path: str, alias: str = "active") -> dict[str, Any]:
        """Load a durable volume-backed document and register it for analysis."""
        loaded = await load_text_from_volume(path, alias=alias)
        if loaded.get("status") != "ok":
            return loaded
        text = ctx.agent.documents.get(alias, "")
        return {
            "status": "ok",
            "alias": alias,
            "path": loaded.get("path", path),
            "chars": len(text),
            "lines": len(text.splitlines()),
            "hint": "Preferred over workspace_read for durable document analysis. Use load_document for host, URL, or transient Daytona workspace files.",
        }

    tools.extend(
        [
            Tool(
                _sync_compatible_tool_callable(read_buffer),
                name="read_buffer",
                desc="Read the full contents of a sandbox buffer",
            ),
            Tool(
                _sync_compatible_tool_callable(clear_buffer),
                name="clear_buffer",
                desc="Clear one sandbox buffer (or all buffers when name is empty)",
            ),
            Tool(
                _sync_compatible_tool_callable(save_buffer_to_volume),
                name="save_buffer_to_volume",
                desc="Persist a sandbox buffer to durable mounted-volume storage as JSON",
            ),
            Tool(
                _sync_compatible_tool_callable(load_text_from_volume),
                name="load_text_from_volume",
                desc="Load text from the durable mounted volume (artifacts by default) into host-side document memory",
            ),
            Tool(
                _sync_compatible_tool_callable(process_document),
                name="process_document",
                desc="Load a durable volume-backed document into agent memory and register it for downstream analysis",
            ),
        ]
    )

    return tools


# ---------------------------------------------------------------------------
# Process tools (formerly process_tools.py)
# ---------------------------------------------------------------------------

_UNSUPPORTED_PROVIDER_ERROR: dict[str, Any] = {
    "status": "error",
    "error": "This tool requires a Daytona sandbox session and is not available with the current provider.",
}


def build_process_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build sandbox execution, workspace I/O, and background-process tools.

    ``workspace_read`` and ``workspace_write`` are available for all sandbox
    providers.  ``run``, ``extract_python_ast``, and the background-process
    helpers (``start_background_process``, ``read_process_logs``,
    ``kill_process``) rely on helpers that are only injected by the Daytona
    interpreter; they are registered only when the active interpreter is a
    :class:`~fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter`.
    In all other cases, a stub that returns ``{status: "error"}`` is registered
    so callers receive a stable payload shape rather than a ``NameError``.
    """
    from dspy import Tool

    ctx = _SandboxToolContext(agent=agent)
    is_daytona = _is_daytona_interpreter(ctx)
    tools: list[Any] = []

    # ------------------------------------------------------------------
    # Universal tools – available for all sandbox providers
    # ------------------------------------------------------------------

    async def workspace_write(path: str, content: str) -> dict[str, Any]:
        """Write content to a file in the workspace directory."""
        code = f"""
content_value = {content!r}
saved_path = workspace_write({path!r}, content_value)
if str(saved_path).startswith("[error:"):
    SUBMIT(status="error", result=saved_path, error=saved_path, path={path!r})
SUBMIT(status="ok", result=saved_path, path=saved_path, chars=len(content_value))
""".strip()
        return await _aexecute_submit_ctx(ctx, code)

    async def workspace_read(path: str) -> dict[str, Any]:
        """Read content from a file in the workspace directory."""
        code = f"""
content = workspace_read({path!r})
if str(content).startswith("[error:"):
    SUBMIT(status="error", result=content, error=content, path={path!r})
SUBMIT(status="ok", result=content, path={path!r}, content=content, chars=len(content))
""".strip()
        return await _aexecute_submit_ctx(ctx, code)

    tools.extend(
        [
            Tool(
                _sync_compatible_tool_callable(workspace_write),
                name="workspace_write",
                desc="Write content to a file in the workspace directory",
            ),
            Tool(
                _sync_compatible_tool_callable(workspace_read),
                name="workspace_read",
                desc="Read raw content from a transient file in the live workspace. Low-level helper; use load_document or process_document to ingest documents for analysis.",
            ),
        ]
    )

    # ------------------------------------------------------------------
    # Daytona-only tools – only registered when backed by DaytonaInterpreter
    # ------------------------------------------------------------------

    if is_daytona:

        async def run(command: str) -> dict[str, Any]:
            """Execute a bash command in the sandbox environment."""
            code = f"""
result = run({command!r})
status = "ok" if bool(result.get("ok")) else "error"
SUBMIT(
    status=status,
    result=result,
    exit_code=result.get("exit_code"),
    stdout=result.get("stdout", ""),
    stderr=result.get("stderr", ""),
    ok=bool(result.get("ok")),
)
""".strip()
            return await _aexecute_submit_ctx(ctx, code)

        async def extract_python_ast(path: str) -> dict[str, Any]:
            """Extract structural AST JSON mapping (Classes, Methods, Functions, Docstrings) of a Python file"""
            code = f"""
ast_json = extract_python_ast({path!r})
is_error = str(ast_json).startswith("File not found.") or str(ast_json).startswith("AST Parse Error:")
if is_error:
    SUBMIT(status="error", result=ast_json, error=ast_json, path={path!r})
SUBMIT(status="ok", result=ast_json, path={path!r}, ast=ast_json)
""".strip()
            return await _aexecute_submit_ctx(ctx, code)

        async def start_background_process(
            process_id: str, command: str
        ) -> dict[str, Any]:
            """Start a non-blocking background process (daemon) in the sandbox."""
            code = f"""
message = start_background_process({process_id!r}, {command!r})
status = "error" if "already running" in str(message).lower() else "ok"
SUBMIT(status=status, result=message, process_id={process_id!r}, message=message)
""".strip()
            return await _aexecute_submit_ctx(ctx, code)

        async def read_process_logs(process_id: str, tail: int = 50) -> dict[str, Any]:
            """Read the live stdout/stderr logs of an active background process."""
            code = f"""
logs = read_process_logs({process_id!r}, tail={tail})
status = "error" if "is not running" in str(logs).lower() else "ok"
SUBMIT(status=status, result=logs, process_id={process_id!r}, logs=logs)
""".strip()
            return await _aexecute_submit_ctx(ctx, code)

        async def kill_process(process_id: str) -> dict[str, Any]:
            """Terminate a running background process by its ID."""
            code = f"""
message = kill_process({process_id!r})
status = "error" if "is not running" in str(message).lower() else "ok"
SUBMIT(status=status, result=message, process_id={process_id!r}, message=message)
""".strip()
            return await _aexecute_submit_ctx(ctx, code)

        tools.extend(
            [
                Tool(
                    _sync_compatible_tool_callable(run),
                    name="run",
                    desc="Execute a bash command in the sandbox environment",
                ),
                Tool(
                    _sync_compatible_tool_callable(extract_python_ast),
                    name="extract_python_ast",
                    desc="Extract structural AST JSON mapping (Classes, Methods, Functions, Docstrings) of a Python file",
                ),
                Tool(
                    _sync_compatible_tool_callable(start_background_process),
                    name="start_background_process",
                    desc="Start a non-blocking background process (like a live webserver or watch compiler) by passing an arbitrary process ID and the shell command.",
                ),
                Tool(
                    _sync_compatible_tool_callable(read_process_logs),
                    name="read_process_logs",
                    desc="Read the latest stdout/stderr logs of an active background process.",
                ),
                Tool(
                    _sync_compatible_tool_callable(kill_process),
                    name="kill_process",
                    desc="Terminate a running background process.",
                ),
            ]
        )
    else:
        # Register stubs that return a stable error payload so callers are never
        # left with a raw NameError from the sandbox execution environment.

        def _unsupported_run(command: str) -> dict[str, Any]:  # noqa: ARG001
            """Execute a bash command in the sandbox environment (Daytona only)."""
            return _UNSUPPORTED_PROVIDER_ERROR

        def _unsupported_extract_python_ast(path: str) -> dict[str, Any]:  # noqa: ARG001
            """Extract AST of a Python file (Daytona only)."""
            return _UNSUPPORTED_PROVIDER_ERROR

        def _unsupported_start_background_process(
            process_id: str,
            command: str,  # noqa: ARG001
        ) -> dict[str, Any]:
            """Start a background process (Daytona only)."""
            return _UNSUPPORTED_PROVIDER_ERROR

        def _unsupported_read_process_logs(
            process_id: str,
            tail: int = 50,  # noqa: ARG001
        ) -> dict[str, Any]:
            """Read background process logs (Daytona only)."""
            return _UNSUPPORTED_PROVIDER_ERROR

        def _unsupported_kill_process(
            process_id: str,  # noqa: ARG001
        ) -> dict[str, Any]:
            """Terminate a background process (Daytona only)."""
            return _UNSUPPORTED_PROVIDER_ERROR

        tools.extend(
            [
                Tool(
                    _unsupported_run,
                    name="run",
                    desc="Execute a bash command in the sandbox environment",
                ),
                Tool(
                    _unsupported_extract_python_ast,
                    name="extract_python_ast",
                    desc="Extract structural AST JSON mapping (Classes, Methods, Functions, Docstrings) of a Python file",
                ),
                Tool(
                    _unsupported_start_background_process,
                    name="start_background_process",
                    desc="Start a non-blocking background process (like a live webserver or watch compiler) by passing an arbitrary process ID and the shell command.",
                ),
                Tool(
                    _unsupported_read_process_logs,
                    name="read_process_logs",
                    desc="Read the latest stdout/stderr logs of an active background process.",
                ),
                Tool(
                    _unsupported_kill_process,
                    name="kill_process",
                    desc="Terminate a running background process.",
                ),
            ]
        )

    return tools
