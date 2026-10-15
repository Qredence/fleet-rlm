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
    assert len(submitted_tasks) == 1
    assert submitted_tasks[0]["task"] == "summarize the file"
    assert len(submitted_tasks[0]["idempotency_key"]) == 32
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


def test_rich_cli_renders_versioned_event_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    output = io.StringIO()
    monkeypatch.setattr(cli, "_CONSOLE", Console(file=output, width=100))

    cli._render_event(
        {
            "version": 1,
            "event": "turn_complete",
            "seq": 4,
            "payload": {"answer": "Versioned result", "route": "hybrid"},
        }
    )

    rendered = output.getvalue()
    assert "Answer (route=hybrid)" in rendered
    assert "Versioned result" in rendered


def test_memory_and_trace_commands_use_session_history_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/memory"):
            return httpx.Response(200, json={"memories": [{"memory_id": "m-1", "content": "Use cents."}]})
        return httpx.Response(200, json={"turns": [{"task": "prior", "answer": "ok"}]})

    output = io.StringIO()
    monkeypatch.setattr(cli, "_CONSOLE", Console(file=output, width=100))
    client = httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(handle_request))
    cli._handle_command(client, "/memory invoice", "session-1")
    cli._handle_command(client, "/trace", "session-1")

    assert requests[0].url.path == "/sessions/session-1/memory"
    assert requests[0].url.params["q"] == "invoice"
    assert requests[1].url.path == "/sessions/session-1/history"
    assert "Use cents." in output.getvalue()
    assert "Trace unavailable" in output.getvalue()
    client.close()


def test_trace_command_shows_trace_id_when_saved_turn_has_one(monkeypatch: pytest.MonkeyPatch) -> None:
    output = io.StringIO()
    monkeypatch.setattr(cli, "_CONSOLE", Console(file=output, width=100))
    client = httpx.Client(
        base_url="http://fleet.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"turns": [{"task": "prior", "trace_id": "trace-7"}]})
        ),
    )

    cli._handle_command(client, "/trace", "session-1")

    assert "trace-7" in output.getvalue()
    client.close()


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"event": "turn_cancelled", "error": "Cancelled by client."}, "Turn cancelled"),
        ({"event": "budget_update", "lm_calls": 3, "max_llm_calls": 3}, "budget full; no calls remain"),
        ({"event": "turn_complete", "answer": "Done", "route": "hybrid"}, "Turn persisted"),
    ],
)
def test_terminal_statuses_are_rendered_truthfully(
    monkeypatch: pytest.MonkeyPatch, event: dict[str, Any], expected: str
) -> None:
    output = io.StringIO()
    monkeypatch.setattr(cli, "_CONSOLE", Console(file=output, width=100))

    cli._render_event(event)

    assert expected in output.getvalue()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/mo", "/model"),
        ("/model ", "sub"),
        ("/mode r", "rlm"),
        ("/config ", "mode"),
        ("/settings mode f", "flex"),
        ("/skills p", "push"),
        ("/opt", "/optimize"),
    ],
)
def test_command_and_argument_completion(text: str, expected: str) -> None:
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    completions = cli._COMMAND_COMPLETER.get_completions(Document(text), CompleteEvent())
    assert expected in [item.text for item in completions]


@pytest.mark.parametrize(("choice", "selected"), [("2", True), ("", False), ("0", False), ("oops", False)])
def test_interactive_model_choice_and_settings(monkeypatch, choice: str, selected: bool) -> None:
    output = io.StringIO()
    console = Console(file=output, width=100)
    monkeypatch.setattr(cli, "_CONSOLE", console)
    monkeypatch.setattr(console, "input", lambda _prompt: choice)
    writes = []

    def handle(request):
        if request.method == "PUT":
            writes.append(json.loads(request.content))
            return httpx.Response(200, json=writes[-1])
        if request.url.path == "/models":
            assert request.url.params["scope"] == "system.ai"
            return httpx.Response(200, json={"models": ["openai/system.ai.alpha", "openai/system.ai.beta"]})
        return httpx.Response(200, json={"mode": "rlm", "models": {"root": "openai/system.ai.alpha"}})

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(handle)) as client:
        cli._handle_command(client, "/settings", "s1")
        cli._handle_command(client, "/model sub", "s1")
    assert writes == ([{"role": "sub", "model": "openai/system.ai.beta"}] if selected else [])
    assert "Session configuration" in output.getvalue()
    assert "openai/system.ai.beta" in output.getvalue()


@pytest.mark.parametrize("outcome", ["complete", "error", "cancelled", "disconnect", "interrupt", "network", "http"])
def test_free_form_tasks_stream_live_and_recover_from_errors(monkeypatch, outcome: str) -> None:
    output = io.StringIO()
    console = Console(file=output, width=120)
    monkeypatch.setattr(cli, "_CONSOLE", console)
    inputs = iter(["Analyze '分析' and explain why 17 * 19 differs from 17 + 19?", "/exit"])
    monkeypatch.setattr(console, "input", lambda _prompt: next(inputs))
    submitted = []

    class Stream(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            yield b'data: nonsense\n\ndata: []\n\ndata:{"event":"tool_start","payload":{"tool":"streaming_probe"}}\n\n'
            assert "streaming_probe" in output.getvalue()  # Rendered before the response ends.
            if outcome == "interrupt":
                raise KeyboardInterrupt
            if outcome == "network":
                raise httpx.ReadError("disconnected")
            if outcome == "disconnect":
                return
            event = {
                "event": {"complete": "turn_complete", "error": "error", "cancelled": "turn_cancelled"}[outcome],
                "payload": {"answer": "323, not 36", "error": "stopped", "route": "rlm"},
            }
            yield f"data: {json.dumps(event)}\n\n".encode()

        def close(self):
            self.closed = True

    stream = Stream()

    def handle(request):
        submitted.append(json.loads(request.content))
        return httpx.Response(503 if outcome == "http" else 200, stream=stream)

    real_client = httpx.Client
    monkeypatch.setattr(
        cli.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs)
    )
    cli.run_interactive_cli("http://fleet.test", "s1")
    assert submitted[0]["task"].startswith("Analyze '分析'")
    assert stream.closed
    expected = {
        "complete": "323, not 36",
        "error": "Turn failed",
        "cancelled": "Turn cancelled",
        "disconnect": "without a final outcome",
        "interrupt": "server cancels on disconnect",
        "network": "Turn stream failed",
        "http": "HTTP 503",
    }[outcome]
    assert expected in output.getvalue()
    if outcome != "complete":
        assert "Turn persisted" not in output.getvalue()


def test_activity_and_literal_multiline_answer_are_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    output = io.StringIO()
    monkeypatch.setattr(cli, "_CONSOLE", Console(file=output, width=120))
    for event in (
        {"event": "triage_decision", "mode": "hybrid", "route": "deep_rlm", "summary": "Context requires code."},
        {"event": "lm_start", "model": "test-model", "call_id": "lm-17", "agent_id": "child-2"},
        {"event": "tool_start", "tool": "lookup", "inputs": '{"path": "files/a.csv"}'},
        {"event": "tool_end", "tool": "lookup", "call_id": "tool-3", "ok": True, "output": "323", "duration_ms": 17},
        {"event": "repl_output", "error": True, "error_type": "TimeoutError", "duration_ms": 23},
        {"event": "turn_complete", "answer": "A=7 [literal]\n6\nD", "learned_skill_note": "Separate note."},
    ):
        cli._render_event(event)
    text = output.getvalue()
    assert "Decision summary · not private reasoning" in text
    assert "Context requires code." in text
    assert "Model running: test-model" in text and "agent=child-2" in text
    assert "Tool arguments" in text and "files/a.csv" in text
    assert "Tool result" in text and "323" in text and "17 ms" in text
    assert "Execution failed (TimeoutError)" in text
    rows = [line.strip("│ ") for line in text.splitlines()]
    start = rows.index("A=7 [literal]")
    assert rows[start : start + 3] == ["A=7 [literal]", "6", "D"]
    assert "Learned note · not part of answer" in text
