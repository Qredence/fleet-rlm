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

from .ws_helpers import _now_iso, _sanitize_for_log
from .ws_lifecycle import PersistenceRequiredError

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
    command = str(payload.get("command", "")).strip()
    args = payload.get("args", {})

    if not command:
        await websocket.send_json(
            {"type": "error", "message": "Command name cannot be empty"}
        )
        return

    if command == "resolve_hitl":
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
            return

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
        return

    try:
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = await agent.execute_command(command, args)
        normalized_result = (
            {"status": "ok", **result}
            if isinstance(result, dict)
            else {"status": "ok", "value": result}
        )

        # Track likely artifact writes as session metadata.
        if session_record is not None and command in {
            "save_buffer",
            "load_volume",
            "write_to_file",
        }:
            manifest = session_record.get("manifest")
            if not isinstance(manifest, dict):
                manifest = {}
                session_record["manifest"] = manifest

            artifacts = manifest.get("artifacts")
            if not isinstance(artifacts, list):
                artifacts = []
                manifest["artifacts"] = artifacts

            artifacts.append(
                {
                    "timestamp": _now_iso(),
                    "command": command,
                    "path": result.get("saved_path")
                    or args.get("path")
                    or result.get("alias"),
                }
            )
            if repository is not None and identity_rows is not None:
                try:
                    run_id_raw = session_record.get("last_run_db_id")
                    if run_id_raw:
                        run_id = uuid.UUID(str(run_id_raw))
                        step_id = session_record.get("last_step_db_id")
                        step_uuid = uuid.UUID(str(step_id)) if step_id else None
                        await repository.store_artifact(
                            ArtifactCreateRequest(
                                tenant_id=identity_rows.tenant_id,
                                run_id=run_id,
                                step_id=step_uuid,
                                kind=ArtifactKind.FILE,
                                uri=str(
                                    result.get("saved_path")
                                    or args.get("path")
                                    or result.get("alias")
                                    or "memory://unknown"
                                ),
                                metadata_json={
                                    "command": command,
                                    "args": args,
                                },
                            )
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
                    "message_id": str(args.get("message_id", "")).strip() or None,
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
                    "message_id": str(args.get("message_id", "")).strip() or None,
                },
            )
        )
