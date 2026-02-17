"""Chat streaming handlers for bridge frontends."""

from __future__ import annotations

from typing import Any

from .protocol import BridgeRPCError


async def submit_chat(runtime: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Submit one chat turn and emit stream events over bridge."""
    try:
        runtime.ensure_agent()
    except Exception as exc:
        # Provide actionable guidance for common setup errors
        error_msg = str(exc)
        if "Modal" in error_msg or "credentials" in error_msg.lower():
            raise BridgeRPCError(
                code="SETUP_ERROR",
                message=f"Modal setup required: {error_msg}. Use /settings to configure credentials or run 'modal setup'.",
            ) from exc
        if "secret" in error_msg.lower():
            raise BridgeRPCError(
                code="SETUP_ERROR",
                message=f"Modal secret missing: {error_msg}. Create secret with: modal secret create LITELLM DSPY_LM_MODEL=... DSPY_LLM_API_KEY=...",
            ) from exc
        raise BridgeRPCError(code="SETUP_ERROR", message=str(exc)) from exc

    message = str(params.get("message", "")).strip()
    if not message:
        raise BridgeRPCError(code="INVALID_ARGS", message="`message` is required.")

    docs_path = params.get("docs_path")
    if isinstance(docs_path, str) and docs_path.strip():
        runtime.agent.load_document(docs_path.strip())

    trace = bool(params.get("trace", runtime.trace_mode != "off"))
    runtime.cancel_requested = False

    assistant_chunks: list[str] = []
    final_payload: dict[str, Any] = {}
    final_text = ""

    def cancel_check() -> bool:
        return runtime.cancel_requested

    async for event in runtime.agent.aiter_chat_turn_stream(
        message=message,
        trace=trace,
        cancel_check=cancel_check,
    ):
        payload = event.payload if isinstance(event.payload, dict) else {}
        runtime.emit_event(
            method="chat.event",
            params={
                "kind": event.kind,
                "text": event.text,
                "payload": payload,
                "timestamp": event.timestamp.isoformat(),
                "flush_tokens": getattr(event, "flush_tokens", False),
            },
        )

        if event.kind == "assistant_token":
            assistant_chunks.append(event.text)
        elif event.kind in {"final", "cancelled"}:
            final_text = str(event.text or "").strip()
            final_payload = dict(payload)

    if not final_text:
        final_text = "".join(assistant_chunks).strip()

    # Flush any remaining tokens before emitting session state
    runtime.flush_token_batch()

    runtime.emit_event(
        method="session.state",
        params={"history_turns": runtime.agent.history_turns()},
    )

    return {"assistant_response": final_text, "payload": final_payload}


def cancel_chat(runtime: Any, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Request cancellation of current chat turn."""
    runtime.cancel_requested = True
    return {"ok": True, "cancel_requested": True}
