"""WebSocket streaming chat endpoint."""

# NOTE: Do NOT add ``from __future__ import annotations`` here.
# FastAPI inspects handler parameter *types* at runtime to detect
# ``WebSocket`` vs query params.  PEP 604 stringified annotations break
# that introspection, causing WebSocket endpoints to reject connections
# with HTTP 403 ("Field required" for a query param named ``websocket``).

import logging

import dspy
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from fleet_rlm import runners

from ..deps import server_state

router = APIRouter(tags=["websocket"])

logger = logging.getLogger(__name__)


def _sanitize_for_log(value: object) -> str:
    """Normalize untrusted values to a single log line."""
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


@router.websocket("/ws/chat")
async def chat_streaming(websocket: WebSocket):
    """Streaming WebSocket endpoint with native DSPy async streaming.

    Protocol:
    - Client sends: {"type": "message", "content": str, "docs_path": str|null}
    - Client sends: {"type": "cancel"} to cancel current turn
    - Client sends: {"type": "command", "command": str, "args": dict}
    - Server sends: {"type": "event", "data": StreamEvent} for each event
    - Server sends: {"type": "command_result", "command": str, "result": dict}
    - Server sends: {"type": "error", "message": str} on error
    """
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
        cancel_flag = {"cancelled": False}

        try:
            while True:
                payload = await websocket.receive_json()
                msg_type = payload.get("type", "message")

                if msg_type == "cancel":
                    cancel_flag["cancelled"] = True
                    continue

                if msg_type == "command":
                    await _handle_command(websocket, agent, payload)
                    continue

                if msg_type != "message":
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": f"Unknown message type: {msg_type}",
                        }
                    )
                    continue

                message = str(payload.get("content", "")).strip()
                docs_path = payload.get("docs_path")
                trace = bool(payload.get("trace", True))
                # trace_mode is accepted for client compatibility but ignored here;
                # backend behavior is driven by the trace boolean.

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
        except Exception as e:
            await websocket.send_json(
                {"type": "error", "message": f"Server error: {str(e)}"}
            )


async def _handle_command(
    websocket: WebSocket,
    agent: "runners.RLMReActChatAgent",
    payload: dict,
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
        result = await agent.execute_command(command, args)
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
