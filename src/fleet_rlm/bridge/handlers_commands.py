"""Command listing/execution handlers for bridge frontends."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fleet_rlm import runners
from fleet_rlm.react.commands import COMMAND_DISPATCH

from .protocol import BridgeRPCError


def list_commands(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return available command surfaces for slash palette population."""
    wrappers = ["run-long-context", "check-secret", "check-secret-key"]
    return {
        "tool_commands": sorted(COMMAND_DISPATCH.keys()),
        "wrapper_commands": wrappers,
        "count": len(COMMAND_DISPATCH) + len(wrappers),
    }


async def execute_command(runtime: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Execute tool or wrapper command through runtime and return result."""
    command = str(params.get("command", "")).strip()
    if not command:
        raise BridgeRPCError(code="INVALID_ARGS", message="`command` is required.")

    policy = runtime.command_permissions.get(command, "allow")
    if policy == "deny":
        raise BridgeRPCError(
            code="DENIED",
            message=f"Command denied by permission policy: {command}",
        )

    args = params.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise BridgeRPCError(
            code="INVALID_ARGS",
            message="`args` must be an object.",
        )

    if command == "run-long-context":
        return _run_long_context(runtime, args)
    if command == "check-secret":
        result = runners.check_secret_presence(secret_name=runtime.secret_name)
        return {"command": command, "result": result}
    if command == "check-secret-key":
        key = str(args.get("key", "DSPY_LLM_API_KEY"))
        result = runners.check_secret_key(secret_name=runtime.secret_name, key=key)
        return {"command": command, "result": result}

    runtime.ensure_agent()
    if command not in COMMAND_DISPATCH:
        raise BridgeRPCError(
            code="UNKNOWN_COMMAND",
            message=f"Unknown command: {command}",
            data={"available": sorted(COMMAND_DISPATCH)},
        )

    result = await runtime.agent.execute_command(command, args)
    return {"command": command, "result": result}


def _run_long_context(runtime: Any, args: dict[str, Any]) -> dict[str, Any]:
    docs_path = str(args.get("docs_path", "")).strip()
    query = str(args.get("query", "")).strip()
    mode = str(args.get("mode", "analyze")).strip() or "analyze"
    if not docs_path or not query:
        raise BridgeRPCError(
            code="INVALID_ARGS",
            message="`docs_path` and `query` are required for run-long-context.",
        )

    result = runners.run_long_context(
        docs_path=Path(docs_path),
        query=query,
        mode=mode,
        max_iterations=runtime.config.rlm_settings.max_iterations,
        max_llm_calls=runtime.config.rlm_settings.max_llm_calls,
        verbose=runtime.config.rlm_settings.verbose,
        timeout=runtime.config.interpreter.timeout,
        secret_name=runtime.secret_name,
        volume_name=runtime.volume_name,
    )
    return {"command": "run-long-context", "result": result}
