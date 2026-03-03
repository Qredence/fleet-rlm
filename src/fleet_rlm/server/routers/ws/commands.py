"""WebSocket command dispatch handler."""

import logging
import uuid
from typing import Any

from fastapi import WebSocket

from fleet_rlm import runners
from fleet_rlm.core.interpreter import ExecutionProfile
from fleet_rlm.db import FleetRepository
from fleet_rlm.db.models import ArtifactKind
from fleet_rlm.db.types import ArtifactCreateRequest, IdentityUpsertResult

from .helpers import _now_iso, _sanitize_for_log
from .lifecycle import PersistenceRequiredError

logger = logging.getLogger(__name__)


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


def _is_artifact_tracking_command(command: str) -> bool:
    return command in {"save_buffer", "load_volume", "write_to_file"}


def _append_session_artifact(
    *,
    session_record: dict[str, Any],
    command: str,
    args: dict[str, Any],
    result: dict[str, Any],
) -> str:
    manifest = session_record.get("manifest")
    if not isinstance(manifest, dict):
        manifest = {}
        session_record["manifest"] = manifest

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
        manifest["artifacts"] = artifacts

    artifact_uri = str(
        result.get("saved_path") or args.get("path") or result.get("alias") or ""
    )
    artifacts.append(
        {
            "timestamp": _now_iso(),
            "command": command,
            "path": artifact_uri or None,
        }
    )
    return artifact_uri


async def _persist_artifact_metadata(
    *,
    repository: FleetRepository,
    identity_rows: IdentityUpsertResult,
    session_record: dict[str, Any],
    command: str,
    args: dict[str, Any],
    artifact_uri: str,
) -> None:
    run_id_raw = session_record.get("last_run_db_id")
    if not run_id_raw:
        return

    run_id = uuid.UUID(str(run_id_raw))
    step_id = session_record.get("last_step_db_id")
    step_uuid = uuid.UUID(str(step_id)) if step_id else None
    await repository.store_artifact(
        ArtifactCreateRequest(
            tenant_id=identity_rows.tenant_id,
            run_id=run_id,
            step_id=step_uuid,
            kind=ArtifactKind.FILE,
            uri=artifact_uri or "memory://unknown",
            metadata_json={
                "command": command,
                "args": args,
            },
        )
    )


def _normalize_command_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return {"status": "ok", **result}
    return {"status": "ok", "value": result}


def _message_id_from_args(args: dict[str, Any] | None) -> str | None:
    if not isinstance(args, dict):
        return None
    return str(args.get("message_id", "")).strip() or None


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


async def _try_handle_resolve_hitl(
    *,
    websocket: WebSocket,
    command: str,
    args: dict[str, Any],
) -> bool:
    if command != "resolve_hitl":
        return False

    message_id = str(args.get("message_id", "")).strip()
    action_label = str(args.get("action_label", "")).strip()
    if not message_id or not action_label:
        await websocket.send_json(
            _command_response(
                command=command,
                result={
                    "status": "error",
                    "error": "resolve_hitl requires message_id and action_label",
                    "message_id": message_id or None,
                },
            )
        )
        return True

    hitl_event_id = str(uuid.uuid4())
    await websocket.send_json(
        {
            "type": "event",
            "data": {
                "kind": "hitl_resolved",
                "text": action_label,
                "payload": {
                    "message_id": message_id,
                    "resolution": action_label,
                    "source": "command",
                },
                "version": 1,
                "event_id": hitl_event_id,
            },
        }
    )
    await websocket.send_json(
        _command_response(
            command=command,
            result={
                "status": "ok",
                "message_id": message_id,
                "resolution": action_label,
            },
        )
    )
    return True


async def _handle_command(
    websocket: WebSocket,
    agent: "runners.RLMReActChatAgent",
    payload: dict[str, Any],
    session_record: dict[str, Any] | None,
    *,
    repository: FleetRepository | None = None,
    identity_rows: IdentityUpsertResult | None = None,
    persistence_required: bool = False,
) -> None:
    """Dispatch a command message to the agent and return the result."""
    command, args = _extract_command_and_args(payload)

    if not command:
        await websocket.send_json(
            {"type": "error", "message": "Command name cannot be empty"}
        )
        return
    if args is None:
        await _send_command_args_error(websocket=websocket, command=command)
        return

    if await _try_handle_resolve_hitl(websocket=websocket, command=command, args=args):
        return

    try:
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = await agent.execute_command(command, args)
        normalized_result = _normalize_command_result(result)

        # Track likely artifact writes as session metadata.
        if (
            session_record is not None
            and isinstance(result, dict)
            and _is_artifact_tracking_command(command)
        ):
            artifact_uri = _append_session_artifact(
                session_record=session_record,
                command=command,
                args=args,
                result=result,
            )
            if repository is not None and identity_rows is not None:
                try:
                    await _persist_artifact_metadata(
                        repository=repository,
                        identity_rows=identity_rows,
                        session_record=session_record,
                        command=command,
                        args=args,
                        artifact_uri=artifact_uri,
                    )
                except Exception as exc:
                    if persistence_required:
                        raise PersistenceRequiredError(
                            "artifact_persist_failed",
                            f"Failed to persist artifact metadata: {exc}",
                        ) from exc
                    logger.warning(
                        "Failed to persist artifact metadata: %s",
                        _sanitize_for_log(exc),
                    )

        await websocket.send_json(
            _command_response(command=command, result=normalized_result)
        )
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
