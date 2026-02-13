"""WebSocket streaming chat endpoint."""

# NOTE: Do NOT add ``from __future__ import annotations`` here.
# FastAPI inspects handler parameter *types* at runtime to detect
# ``WebSocket`` vs query params.  PEP 604 stringified annotations break
# that introspection, causing WebSocket endpoints to reject connections
# with HTTP 403 ("Field required" for a query param named ``websocket``).

import json
import logging
import re
import uuid
from datetime import datetime, timezone

import dspy
from dspy.primitives.code_interpreter import FinalOutput
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from fleet_rlm import runners
from fleet_rlm.core.interpreter import ExecutionProfile

from ..deps import server_state, session_key
from ..schemas import WSMessage

router = APIRouter(tags=["websocket"])

logger = logging.getLogger(__name__)


def _sanitize_for_log(value: object) -> str:
    """Normalize untrusted values to a single log line."""
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


def _sanitize_id(value: str, default_value: str) -> str:
    """Restrict workspace/user IDs to a safe path/key subset."""
    candidate = (value or "").strip()
    if not candidate:
        return default_value
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]", "-", candidate)
    return cleaned[:128] or default_value


def _manifest_path(workspace_id: str, user_id: str) -> str:
    return f"workspaces/{workspace_id}/users/{user_id}/memory/react-session.json"


def _volume_load_manifest(agent: "runners.RLMReActChatAgent", path: str) -> dict:
    """Best-effort manifest load from Modal volume; returns empty dict if absent."""
    result = agent.interpreter.execute(
        "text = load_from_volume(path)\nSUBMIT(text=text)",
        variables={"path": path},
        execution_profile=ExecutionProfile.MAINTENANCE,
    )
    if not isinstance(result, FinalOutput):
        return {}
    output = result.output if isinstance(result.output, dict) else {}
    text = str(output.get("text", ""))
    if not text or text.startswith("[file not found:") or text.startswith("[error:"):
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _volume_save_manifest(
    agent: "runners.RLMReActChatAgent", path: str, manifest: dict
) -> str | None:
    """Best-effort manifest save to Modal volume."""
    payload = json.dumps(manifest, ensure_ascii=False, default=str)
    result = agent.interpreter.execute(
        "saved_path = save_to_volume(path, payload)\nSUBMIT(saved_path=saved_path)",
        variables={"path": path, "payload": payload},
        execution_profile=ExecutionProfile.MAINTENANCE,
    )
    if not isinstance(result, FinalOutput):
        return None
    output = result.output if isinstance(result.output, dict) else {}
    saved_path = str(output.get("saved_path", ""))
    if saved_path.startswith("["):
        return None
    return saved_path or None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.websocket("/ws/chat")
async def chat_streaming(websocket: WebSocket):
    """Streaming WebSocket endpoint with native DSPy async streaming."""
    await websocket.accept()

    cfg = server_state.config
    _planner_lm = server_state.planner_lm

    if _planner_lm is None:
        await websocket.send_json(
            {
                "type": "error",
                "message": "Planner LM not configured. Check DSPY_LM_MODEL and DSPY_LLM_API_KEY env vars.",
            }
        )
        await websocket.close()
        return

    agent_context = runners.build_react_chat_agent(
        react_max_iters=cfg.react_max_iters,
        rlm_max_iterations=cfg.rlm_max_iterations,
        rlm_max_llm_calls=cfg.rlm_max_llm_calls,
        timeout=cfg.timeout,
        secret_name=cfg.secret_name,
        volume_name=cfg.volume_name,
        planner_lm=_planner_lm,
    )

    with dspy.context(lm=_planner_lm), agent_context as agent:
        interpreter = getattr(agent, "interpreter", None)
        # Interlocutor path defaults to strict root profile.
        if interpreter is not None:
            try:
                interpreter.default_execution_profile = ExecutionProfile(
                    cfg.ws_default_execution_profile
                )
            except ValueError:
                interpreter.default_execution_profile = (
                    ExecutionProfile.ROOT_INTERLOCUTOR
                )

        cancel_flag = {"cancelled": False}
        active_key: str | None = None
        active_manifest_path: str | None = None
        # Connection-scoped fallback: each WS gets a unique user identity
        # so unauthenticated clients never share session state.
        connection_user_id = f"anon-{uuid.uuid4().hex[:12]}"
        session_record: dict | None = None

        async def persist_session_state(
            *, include_volume_save: bool = True, latest_user_message: str = ""
        ) -> None:
            nonlocal session_record, active_manifest_path
            if session_record is None:
                return
            exported_state = agent.export_session_state()
            manifest = session_record.setdefault("manifest", {})
            logs = manifest.setdefault("logs", [])
            memory = manifest.setdefault("memory", [])
            generated_docs = manifest.setdefault("generated_docs", [])
            artifacts = manifest.setdefault("artifacts", [])
            metadata = manifest.setdefault("metadata", {})

            if latest_user_message:
                logs.append(
                    {
                        "timestamp": _now_iso(),
                        "user_message": latest_user_message,
                        "history_turns": len(exported_state.get("history", [])),
                    }
                )
                # Lightweight conversational memory snapshot.
                memory.append(
                    {
                        "timestamp": _now_iso(),
                        "content": latest_user_message[:400],
                    }
                )

            generated_docs[:] = sorted(list(exported_state.get("documents", {}).keys()))
            metadata["updated_at"] = _now_iso()
            metadata["history_turns"] = len(exported_state.get("history", []))
            metadata["document_count"] = len(exported_state.get("documents", {}))
            metadata["artifact_count"] = len(artifacts)
            manifest["state"] = (
                exported_state  # Persist full state for volume restore (#24)
            )
            session_record["session"]["state"] = exported_state
            session_record["session"]["session_id"] = session_record.get("session_id")
            server_state.sessions[session_record["key"]] = session_record

            if include_volume_save and active_manifest_path and interpreter is not None:
                _volume_save_manifest(agent, active_manifest_path, manifest)

        try:
            while True:
                raw_payload = await websocket.receive_json()
                try:
                    msg = WSMessage(**raw_payload)
                except ValidationError as exc:
                    raw_type = str(raw_payload.get("type", "")).strip()
                    if raw_type:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": f"Unknown message type: {raw_type}",
                            }
                        )
                        continue
                    await websocket.send_json(
                        {"type": "error", "message": f"Invalid payload: {exc}"}
                    )
                    continue

                workspace_id = _sanitize_id(
                    msg.workspace_id, cfg.ws_default_workspace_id
                )
                user_id = _sanitize_id(msg.user_id, connection_user_id)
                sess_id = msg.session_id or str(uuid.uuid4())
                key = session_key(workspace_id, user_id)
                manifest_path = _manifest_path(workspace_id, user_id)

                # Switch/reload session identity if needed.
                if active_key != key:
                    if session_record is not None:
                        await persist_session_state(include_volume_save=True)

                    cached = server_state.sessions.get(key)
                    if cached is None:
                        manifest = (
                            _volume_load_manifest(agent, manifest_path)
                            if interpreter is not None
                            else {}
                        )
                        cached = {
                            "key": key,
                            "workspace_id": workspace_id,
                            "user_id": user_id,
                            "session_id": sess_id,
                            "manifest": manifest if isinstance(manifest, dict) else {},
                            "session": {"state": {}, "session_id": sess_id},
                        }
                    cached["session_id"] = sess_id
                    server_state.sessions[key] = cached
                    active_key = key
                    active_manifest_path = manifest_path
                    session_record = cached
                    restored_state = (
                        cached.get("session", {}).get("state", {})
                        if isinstance(cached.get("session"), dict)
                        else {}
                    )
                    if not restored_state and isinstance(cached.get("manifest"), dict):
                        restored_state = cached["manifest"].get("state", {})
                    if isinstance(restored_state, dict) and restored_state:
                        agent.import_session_state(restored_state)
                    else:
                        # No saved state — reset agent to prevent leaking
                        # prior session's history/docs across boundaries (#23).
                        agent.reset(clear_sandbox_buffers=True)

                msg_type = msg.type

                if msg_type == "cancel":
                    cancel_flag["cancelled"] = True
                    continue

                if msg_type == "command":
                    await _handle_command(
                        websocket, agent, msg.model_dump(), session_record
                    )
                    await persist_session_state(include_volume_save=True)
                    continue

                if msg_type != "message":
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": f"Unknown message type: {msg_type}",
                        }
                    )
                    continue

                message = str(msg.content or "").strip()
                docs_path = msg.docs_path
                trace = bool(msg.trace)

                if not message:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "Message content cannot be empty",
                        }
                    )
                    continue

                cancel_flag["cancelled"] = False

                def cancel_check() -> bool:
                    return cancel_flag["cancelled"]

                if docs_path:
                    agent.load_document(docs_path)

                try:
                    async for event in agent.aiter_chat_turn_stream(
                        message=message, trace=trace, cancel_check=cancel_check
                    ):
                        event_dict = {
                            "kind": event.kind,
                            "text": event.text,
                            "payload": event.payload,
                            "timestamp": event.timestamp.isoformat(),
                        }
                        await websocket.send_json({"type": "event", "data": event_dict})
                        if event.kind == "final":
                            await persist_session_state(
                                include_volume_save=True, latest_user_message=message
                            )
                except Exception as exc:
                    logger.error(
                        "Streaming error: %s",
                        _sanitize_for_log(exc),
                        exc_info=True,
                        extra={"error_type": type(exc).__name__},
                    )
                    await websocket.send_json(
                        {"type": "error", "message": f"Streaming error: {exc}"}
                    )

        except WebSocketDisconnect:
            cancel_flag["cancelled"] = True
            await persist_session_state(include_volume_save=True)
        except Exception as exc:
            await websocket.send_json(
                {"type": "error", "message": f"Server error: {str(exc)}"}
            )
            await persist_session_state(include_volume_save=True)


async def _handle_command(
    websocket: WebSocket,
    agent: "runners.RLMReActChatAgent",
    payload: dict,
    session_record: dict | None,
) -> None:
    """Dispatch a command message to the agent and return the result."""
    command = str(payload.get("command", "")).strip()
    args = payload.get("args", {})

    if not command:
        await websocket.send_json(
            {"type": "error", "message": "Command name cannot be empty"}
        )
        return

    try:
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = await agent.execute_command(command, args)

        # Track likely artifact writes as session metadata.
        if session_record is not None and command in {"save_buffer", "load_volume"}:
            manifest = session_record.setdefault("manifest", {})
            artifacts = manifest.setdefault("artifacts", [])
            artifacts.append(
                {
                    "timestamp": _now_iso(),
                    "command": command,
                    "path": result.get("saved_path")
                    or args.get("path")
                    or result.get("alias"),
                }
            )

        await websocket.send_json(
            {
                "type": "command_result",
                "command": command,
                "result": result,
            }
        )
    except (ValueError, FileNotFoundError, KeyError) as exc:
        await websocket.send_json(
            {
                "type": "command_result",
                "command": command,
                "result": {"status": "error", "error": str(exc)},
            }
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
            {
                "type": "command_result",
                "command": command,
                "result": {
                    "status": "error",
                    "error": f"Internal error: {type(exc).__name__}: {exc}",
                },
            }
        )
