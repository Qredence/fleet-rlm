from __future__ import annotations

from fleet_rlm.api.routers.ws.connection_loop import _build_routing_preview_event
from fleet_rlm.api.schemas import WSMessage


class _PreviewAgent:
    def preview_routing(self, **kwargs: object) -> dict[str, object]:
        self.last_kwargs = kwargs
        turn_context = kwargs.get("turn_context")
        estimated = getattr(turn_context, "estimated_chars", 0) if turn_context is not None else 0
        if estimated >= 32000:
            return {
                "routing_decision": "large_context_rlm",
                "estimated_chars": estimated,
                "threshold_chars": 32000,
            }
        return {"routing_decision": "url_document_rlm", "source_url": "https://example.com"}


def test_routing_preview_passes_turn_context_and_skills(tmp_path) -> None:
    large_file = tmp_path / "large.txt"
    large_file.write_text("x" * 33000)
    agent = _PreviewAgent()
    msg = WSMessage(
        type="message",
        content=f"Summarize the large document at {large_file} — analyze themes with long context handling.",
        execution_mode="auto",
        context_paths=[str(large_file)],
    )

    event = _build_routing_preview_event(agent, msg)

    assert event is not None
    assert agent.last_kwargs["turn_context"] is not None
    assert event.payload["routing_decision"] == "large_context_rlm"
    assert "selected_skills" in event.payload
