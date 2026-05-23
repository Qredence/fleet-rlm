"""WebSocket command dispatch helpers."""

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

from fastapi import WebSocket

from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.runtime.execution.interpreter_protocol import ExecutionProfile
from fleet_rlm.utils.logging import sanitize_for_log as _sanitize_for_log

from ...runtime_services.chat_runtime import ChatAgentProtocol
from .artifacts import track_command_artifact_if_needed

logger = logging.getLogger(__name__)

_ALLOWED_COMMAND_SCHEMAS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "resolve_hitl": (
        frozenset({"message_id", "resolution"}),
        frozenset({"comment"}),
    ),
}


def _command_response(
    *,
    command: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "command_result",
        "command": command,
        "result": result,
        "version": 1,
        "event_id": str(uuid.uuid4()),
    }


def _extract_command_and_args(
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    command = str(payload.get("command", "")).strip()
    raw_args = payload.get("args", {})
    args = raw_args if isinstance(raw_args, dict) else None
    return command, args


def _normalize_command_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return {"status": "ok", **result}
    return {"status": "ok", "value": result}


def _message_id_from_args(args: dict[str, Any] | None) -> str | None:
    if not isinstance(args, dict):
        return None
    return str(args.get("message_id", "")).strip() or None


def _validate_command_args(command: str, args: dict[str, Any]) -> dict[str, Any] | None:
    schema = _ALLOWED_COMMAND_SCHEMAS.get(command)
    if schema is None:
        return {
            "status": "error",
            "error": f"Unsupported websocket command: {command}",
            "code": "unsupported_command",
            "message_id": _message_id_from_args(args),
        }

    required, optional = schema
    keys = frozenset(args)
    missing = sorted(required - keys)
    unknown = sorted(keys - required - optional)
    if missing:
        return {
            "status": "error",
            "error": f"Missing required args for {command}: {', '.join(missing)}",
            "code": "invalid_command_args",
            "message_id": _message_id_from_args(args),
        }
    if unknown:
        return {
            "status": "error",
            "error": f"Unknown args for {command}: {', '.join(unknown)}",
            "code": "invalid_command_args",
            "message_id": _message_id_from_args(args),
        }
    return None


async def _send_command_args_error(
    *,
    websocket: WebSocket,
    command: str,
) -> None:
    await websocket.send_json(
        _command_response(
            command=command,
            result={
                "status": "error",
                "error": "Command args must be a JSON object",
                "message_id": None,
            },
        )
    )


async def _handle_command(
    websocket: WebSocket,
    agent: ChatAgentProtocol,
    payload: dict[str, Any],
    session_record: dict[str, Any] | None,
    *,
    persistence: Any = None,
    identity_rows: IdentityUpsertResult | None = None,
    persistence_required: bool = False,
) -> None:
    """Dispatch a command message to the agent and return the result."""
    command, args = _extract_command_and_args(payload)

    if not command:
        await websocket.send_json({"type": "error", "message": "Command name cannot be empty"})
        return
    if args is None:
        await _send_command_args_error(websocket=websocket, command=command)
        return
    args_error = _validate_command_args(command, args)
    if args_error is not None:
        await websocket.send_json(_command_response(command=command, result=args_error))
        return

    # Explicit allow-list guard: _validate_command_args() already rejects unknown
    # commands above, but we enforce the invariant here too so any future code path
    # that skips that check cannot reach dispatch with an unlisted command.
    if command not in _ALLOWED_COMMAND_SCHEMAS:
        await websocket.send_json(
            _command_response(
                command=command,
                result={
                    "status": "error",
                    "error": f"Unsupported websocket command: {command}",
                    "code": "unsupported_command",
                },
            )
        )
        return

    try:
        if command == "resolve_hitl":
            result = {
                "message_id": str(args["message_id"]),
                "resolution": str(args["resolution"]),
            }
        else:
            interpreter = agent.interpreter
            if interpreter is None:
                result = await agent.execute_command(command, args)
            else:
                with interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
                    result = await agent.execute_command(command, args)
        normalized_result = _normalize_command_result(result)

        await track_command_artifact_if_needed(
            session_record=session_record,
            command=command,
            args=args,
            result=cast(Any, result),
            persistence=persistence,
            identity_rows=identity_rows,
            persistence_required=persistence_required,
        )

        await websocket.send_json(_command_response(command=command, result=normalized_result))
    except (ValueError, FileNotFoundError, KeyError) as exc:
        await websocket.send_json(
            _command_response(
                command=command,
                result={
                    "status": "error",
                    "error": str(exc),
                    "message_id": _message_id_from_args(args),
                },
            )
        )
    except Exception as exc:
        logger.error(
            "Command %s failed: %s",
            _sanitize_for_log(command),
            _sanitize_for_log(exc),
            exc_info=True,
            extra={
                "command": _sanitize_for_log(command),
                "error_type": type(exc).__name__,
            },
        )
        await websocket.send_json(
            _command_response(
                command=command,
                result={
                    "status": "error",
                    "error": f"Internal error: {type(exc).__name__}: {exc}",
                    "message_id": _message_id_from_args(args),
                },
            )
        )


async def handle_command_with_persist(
    *,
    websocket: WebSocket,
    agent: ChatAgentProtocol,
    payload: dict[str, Any],
    session_record: dict[str, Any] | None,
    persistence: Any = None,
    identity_rows: IdentityUpsertResult | None,
    persistence_required: bool,
    local_persist: Callable[..., Awaitable[None]],
) -> None:
    """Dispatch command payload and persist session state afterward."""
    await _handle_command(
        websocket,
        agent,
        payload,
        session_record,
        persistence=persistence,
        identity_rows=identity_rows,
        persistence_required=persistence_required,
    )
    await local_persist(include_volume_save=True)
