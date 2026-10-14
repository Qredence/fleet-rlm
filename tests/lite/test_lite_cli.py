from __future__ import annotations

import io
import json
from typing import Any

import httpx
import pytest
from rich.console import Console

import fleet_rlm.cli as cli


def test_rich_cli_renders_help_and_streams_a_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    events = [
        {"event": "module_start", "module": "AdaptiveFleetEngine"},
        {"event": "tool_start", "tool": "read_file"},
        {"event": "tool_end", "ok": True},
        {
            "event": "turn_complete",
            "answer": "# Summary\n\nThe file contains three records.",
            "route": "single_rlm",
            "learned_skill_note": "Use the ledger-audit Skill for reconciliation.",
            "artifacts": ["artifacts/summary.csv"],
            "trace_id": "trace-123",
        },
    ]
    submitted_tasks: list[dict[str, str]] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/session-1/turn"
        submitted_tasks.append(json.loads(request.content))
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    real_client = httpx.Client
    transport = httpx.MockTransport(handle_request)

    def create_client(*args: Any, **kwargs: Any) -> httpx.Client:
        return real_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(cli.httpx, "Client", create_client)

    output = io.StringIO()
    console = Console(file=output, width=100)
    user_inputs = iter(("summarize the file", "/help", "/exit"))

    def read_input(prompt: str) -> str:
        console.print(prompt, end="")
        return next(user_inputs)

    monkeypatch.setattr(console, "input", read_input)
    monkeypatch.setattr(cli, "_CONSOLE", console)

    cli.run_interactive_cli("http://fleet.test", "session-1")

    rendered = output.getvalue()
    assert submitted_tasks == [{"task": "summarize the file"}]
    assert "Fleet-RLM Lite" in rendered
    assert "Session: session-1" in rendered
    assert "→ Module: AdaptiveFleetEngine" in rendered
    assert "⚙ Tool: read_file" in rendered
    assert "Answer (route=single_rlm)" in rendered
    assert "The file contains three records." in rendered
    assert "Use the ledger-audit Skill for reconciliation." in rendered
    assert "artifacts/summary.csv" in rendered
    assert "MLflow trace ID: trace-123" in rendered
    assert "`/skills pull`" in rendered
