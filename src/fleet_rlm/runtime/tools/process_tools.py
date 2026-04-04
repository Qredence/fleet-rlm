"""Process execution and workspace I/O sandbox tool builders."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fleet_rlm.runtime.agent.tool_delegation import _sync_compatible_tool_callable

from .sandbox_common import (
    _aexecute_submit_ctx,
    _SandboxToolContext,
)

if TYPE_CHECKING:
    from ..agent.chat_agent import RLMReActChatAgent


_UNSUPPORTED_PROVIDER_ERROR: dict[str, Any] = {
    "status": "error",
    "error": "This tool requires a Daytona sandbox session and is not available with the current provider.",
}


def _is_daytona_interpreter(agent: RLMReActChatAgent) -> bool:
    """Return True when *agent* is backed by a Daytona sandbox interpreter."""
    try:
        from fleet_rlm.integrations.providers.daytona.interpreter import (
            DaytonaInterpreter,
        )
    except Exception:  # pragma: no cover - defensive import guard
        return False
    return isinstance(getattr(agent, "interpreter", None), DaytonaInterpreter)


def build_process_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build sandbox execution, workspace I/O, and background-process tools.

    ``workspace_read`` and ``workspace_write`` are available for all sandbox
    providers.  ``run``, ``extract_python_ast``, and the background-process
    helpers (``start_background_process``, ``read_process_logs``,
    ``kill_process``) rely on helpers that are only injected by the Daytona
    interpreter; they are registered only when the active interpreter is a
    :class:`~fleet_rlm.integrations.providers.daytona.interpreter.DaytonaInterpreter`.
    In all other cases, a stub that returns ``{status: "error"}`` is registered
    so callers receive a stable payload shape rather than a ``NameError``.
    """
    from dspy import Tool

    ctx = _SandboxToolContext(agent=agent)
    is_daytona = _is_daytona_interpreter(agent)
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
