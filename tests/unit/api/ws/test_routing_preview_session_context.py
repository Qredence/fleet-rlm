"""Routing preview retains large-context route on pathless follow-ups."""

from __future__ import annotations

from pathlib import Path

from fleet_rlm.api.routers.ws.connection_loop import _build_routing_preview_event
from fleet_rlm.api.schemas import WSMessage


class _AgentWithPersistedContext:
    def __init__(self, *, pdf_path: str) -> None:
        self.loaded_document_paths = [pdf_path]
        self.interpreter = type(
            "Interpreter",
            (),
            {"context_paths": [pdf_path]},
        )()

    def preview_routing(self, **kwargs: object) -> dict[str, object]:
        self.last_kwargs = kwargs
        turn_context = kwargs.get("turn_context")
        estimated = getattr(turn_context, "estimated_chars", 0) if turn_context is not None else 0
        paths = list(getattr(turn_context, "context_paths", []) or [])
        if estimated >= 32000 and paths:
            return {
                "routing_decision": "large_context_rlm",
                "estimated_chars": estimated,
                "threshold_chars": 32000,
            }
        return {"routing_decision": "auto"}


def test_pathless_followup_routing_uses_persisted_session_paths(tmp_path: Path) -> None:
    pdf = tmp_path / "enterprise-2030.pdf"
    pdf.write_bytes(b"x" * 50000)
    agent = _AgentWithPersistedContext(pdf_path=str(pdf))
    msg = WSMessage(
        type="message",
        content="What is the exact quote from Chad Gates, Managing Director, Pronto Software?",
        execution_mode="auto",
        context_paths=None,
    )

    event = _build_routing_preview_event(agent, msg)

    assert event is not None
    assert event.payload["routing_decision"] == "large_context_rlm"
    assert "long-context" in list(event.payload.get("selected_skills") or [])
    assert "chars)" in event.text
    turn_context = agent.last_kwargs["turn_context"]
    assert str(pdf) in list(getattr(turn_context, "context_paths", []) or [])
