from __future__ import annotations

import asyncio
import io
import json
import threading
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from rich.console import Console

import fleet_rlm.tui.app as app
import fleet_rlm.tui.commands as commands
import fleet_rlm.tui.render as render


def use_console(monkeypatch, console):
    for module in (render, commands, app):
        monkeypatch.setattr(module, "CONSOLE", console)


def _capture_console(monkeypatch: pytest.MonkeyPatch, width: int = 100) -> tuple[io.StringIO, Console]:
    output = io.StringIO()
    console = Console(file=output, width=width)
    use_console(monkeypatch, console)
    return output, console


@asynccontextmanager
async def _running_tui(
    transport,
    session_id: str,
    size: tuple[int, int],
    state: commands.SessionState | None = None,
):
    with httpx.Client(base_url="http://fleet.test", transport=transport) as client:
        tui = app.FleetTextualApp(client, session_id, state or commands.SessionState())
        async with tui.run_test(size=size) as pilot:
            yield client, tui, pilot


@pytest.mark.parametrize("size", [(100, 40), (80, 24), (60, 20)])
def test_experiments_actions_are_visible_in_compact_terminals(size) -> None:
    from textual.widgets import Button

    report = {
        "manifest": {
            "evaluation": {
                "baseline": {},
                "candidate": {
                    "component_diffs": [
                        {
                            "component": "long",
                            "before": "before\n" * 100,
                            "after": "after\n" * 100,
                            "diff": "diff\n" * 100,
                        }
                    ]
                },
            }
        }
    }
    transport, _state = _experiment_transport([_experiment_record("layout-check-run")], comparison=report)

    async def scenario():
        async with _running_tui(transport, "layout-check", size) as (_client, tui, pilot):
            tui._open_experiments()
            await pilot.pause()
            panel = tui.screen.query_one("#experiments-panel")
            for button in tui.screen.query("#experiment-actions Button"):
                assert panel.region.contains_region(button.region), button.id
            detail = tui.screen.query_one("#experiment-detail-scroll")
            actions = tui.screen.query_one("#experiment-actions")
            assert detail.region.bottom <= actions.region.y
            assert detail.max_scroll_y > 0
            detail.scroll_end(animate=False)
            await pilot.pause()
            assert detail.scroll_y > 0
            close = tui.screen.query_one("#experiment-close", Button)
            close.focus()
            await pilot.pause()
            assert panel.region.contains_region(close.region)
            await pilot.press("enter")
            assert not isinstance(tui.screen, app.ExperimentsScreen)

    asyncio.run(scenario())


def test_rich_cli_renders_help_and_streams_a_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    events = [
        {"event": "module_start", "module": "AdaptiveFleetEngine"},
        {"event": "tool_start", "tool": "read_file"},
        {"event": "tool_end", "ok": True},
        {
            "event": "turn_complete",
            "answer": "# Summary\n\nThe file contains three records.",
            "route": "flex",
            "learned_skill_note": "Use the ledger-audit Skill for reconciliation.",
            "artifacts": ["artifacts/summary.csv"],
            "trace_id": "trace-123",
        },
    ]
    submitted_tasks: list[dict[str, Any]] = []

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

    monkeypatch.setattr(app.httpx, "Client", create_client)

    output, console = _capture_console(monkeypatch, width=100)
    user_inputs = iter(("/replay on", "summarize the file", "/replay off", "/help", "/exit"))

    def read_input(prompt: str) -> str:
        console.print(prompt, end="")
        return next(user_inputs)

    monkeypatch.setattr(console, "input", read_input)

    app.run_interactive_cli("http://fleet.test", "session-1")

    rendered = output.getvalue()
    assert len(submitted_tasks) == 1
    assert submitted_tasks[0]["task"] == "summarize the file"
    assert len(submitted_tasks[0]["idempotency_key"]) == 32
    assert submitted_tasks[0]["record_replay"] is True
    assert "Replay capture enabled" in rendered and "Replay capture disabled" in rendered
    assert "Fleet-RLM Lite" in rendered
    assert "Session: session-1" in rendered
    assert "→ Module: AdaptiveFleetEngine" in rendered
    assert "⚙ Tool: read_file" in rendered
    assert "Answer (route=flex)" in rendered
    assert "The file contains three records." in rendered
    assert "Use the ledger-audit Skill for reconciliation." in rendered
    assert "artifacts/summary.csv" in rendered
    assert "MLflow trace ID: trace-123" in rendered
    assert "`/skills [pull|push]`" in rendered and "`/exit`" in rendered


def test_rich_cli_renders_versioned_event_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    output, _console = _capture_console(monkeypatch, width=100)

    render.render_event(
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


def test_shared_turn_events_update_state_and_tracker_under_the_supplied_lock(monkeypatch):
    import threading

    state = commands.SessionState()
    tracker = render.TurnTracker()
    lock = threading.Lock()
    original = render.TurnTracker.ingest

    def ingest(self, event):
        assert lock.locked()
        original(self, event)

    monkeypatch.setattr(render.TurnTracker, "ingest", ingest)
    events = [
        {"event": "triage_decision", "turn_id": "turn-1", "payload": {"mode": "rlm", "route": "rlm"}},
        {"event": "turn_complete", "payload": {"answer": "Done", "trace_id": "trace-1"}},
    ]
    body = ': heartbeat\n\ndata: broken\n\ndata: {"payload": []}\n\n' + "".join(
        f"data: {json.dumps(event)}\n\n" for event in events
    )
    response = httpx.Response(200, content=body)
    received = list(app._turn_events(response, state, tracker, lock=lock))
    assert received == [None, None, *events]
    assert (state.mode, state.last_turn_id, state.last_trace_id) == ("rlm", "turn-1", "trace-1")
    assert tracker.terminal_event["answer"] == "Done"
    assert not lock.locked()


def test_shared_turn_stream_cancellation_leaves_outcome_unknown_and_closes_response():
    import threading

    cancelled = threading.Event()
    closed = []
    submitted = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"event":"turn_status"}\n\n'
            yield b'data: {"event":"turn_complete","trace_id":"must-not-consume"}\n\n'

        def close(self):
            closed.append(True)

    def handle(request):
        submitted.append(json.loads(request.content))
        return httpx.Response(200, stream=Stream())

    state = commands.SessionState(record_replay=True)
    tracker = render.TurnTracker()
    with (
        httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(handle)) as client,
        app._request_turn(client, "session-1", "Do work", state) as response,
    ):
        events = app._turn_events(response, state, tracker, cancelled=cancelled.is_set)
        assert next(events) == {"event": "turn_status"}
        cancelled.set()
        assert list(events) == []
    assert tracker.terminal_event is None and not state.last_trace_id
    assert closed == [True]
    assert submitted[0]["task"] == "Do work" and submitted[0]["record_replay"] is True
    assert len(submitted[0]["idempotency_key"]) == 32


def test_memory_and_trace_commands_use_session_history_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/memory"):
            return httpx.Response(200, json={"memories": [{"memory_id": "m-1", "content": "Use cents."}]})
        return httpx.Response(200, json={"turns": [{"task": "prior", "answer": "ok"}]})

    output, _console = _capture_console(monkeypatch, width=100)
    client = httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(handle_request))
    commands.handle_command(client, "/memory invoice", "session-1")
    commands.handle_command(client, "/trace", "session-1")

    assert requests[0].url.path == "/sessions/session-1/memory"
    assert requests[0].url.params["q"] == "invoice"
    assert requests[1].url.path == "/sessions/session-1/history"
    assert "Use cents." in output.getvalue()
    assert "Trace unavailable" in output.getvalue()
    client.close()


def test_trace_command_shows_trace_id_when_saved_turn_has_one(monkeypatch: pytest.MonkeyPatch) -> None:
    output, _console = _capture_console(monkeypatch, width=100)
    client = httpx.Client(
        base_url="http://fleet.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"turns": [{"task": "prior", "trace_id": "trace-7"}]})
        ),
    )

    commands.handle_command(client, "/trace", "session-1")

    assert "trace-7" in output.getvalue()
    client.close()


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"event": "turn_cancelled", "error": "Cancelled by client."}, "Turn cancelled"),
        ({"event": "budget_update", "lm_calls": 3, "max_llm_calls": 3}, "budget full; no calls remain"),
        ({"event": "ungrounded_submit", "figures": ["16995.71"], "rejected": True}, "not seen in sandbox output"),
        ({"event": "skill_selection", "selected": ["tabular-analysis"]}, "Skills preloaded: tabular-analysis"),
        ({"event": "turn_complete", "answer": "Done", "route": "hybrid"}, "Turn persisted"),
    ],
)
def test_terminal_statuses_are_rendered_truthfully(
    monkeypatch: pytest.MonkeyPatch, event: dict[str, Any], expected: str
) -> None:
    output, _console = _capture_console(monkeypatch, width=100)

    render.render_event(event)

    assert expected in output.getvalue()


def test_host_rejection_is_distinct_from_remote_python_errors_in_the_tracker():
    tracker = render.TurnTracker()
    tracker.ingest({"event": "ungrounded_submit", "rejected": True, "figures": ["426880"]})
    tracker.ingest(
        {"event": "repl_output", "execution_id": "host", "error": True, "outcome": "ungrounded_submit_rejected"}
    )
    assert tracker.submit_rejections == 1 and tracker.sandbox_errors == 0
    tracker.ingest({"event": "repl_output", "execution_id": "remote", "error": True, "outcome": "remote_code_error"})
    assert tracker.submit_rejections == tracker.sandbox_errors == 1


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

    completions = app._completer().get_completions(Document(text), CompleteEvent())
    assert expected in [item.text for item in completions]


@pytest.mark.parametrize(("choice", "selected"), [("2", True), ("", False), ("0", False), ("oops", False)])
def test_interactive_model_choice_and_settings(monkeypatch, choice: str, selected: bool) -> None:
    output, console = _capture_console(monkeypatch, width=100)
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
        commands.handle_command(client, "/settings", "s1")
        commands.handle_command(client, "/model sub", "s1")
    assert writes == ([{"role": "sub", "model": "openai/system.ai.beta"}] if selected else [])
    assert "Session configuration" in output.getvalue()
    assert "openai/system.ai.beta" in output.getvalue()


@pytest.mark.parametrize("outcome", ["complete", "error", "cancelled", "disconnect", "interrupt", "network", "http"])
def test_free_form_tasks_stream_live_and_recover_from_errors(monkeypatch, outcome: str) -> None:
    output, console = _capture_console(monkeypatch, width=120)
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
        app.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs)
    )
    app.run_interactive_cli("http://fleet.test", "s1")
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
    output, _console = _capture_console(monkeypatch, width=120)
    for event in (
        {"event": "triage_decision", "mode": "hybrid", "route": "rlm", "summary": "Context requires code."},
        {"event": "lm_start", "model": "test-model", "call_id": "lm-17", "agent_id": "child-2"},
        {"event": "tool_start", "tool": "lookup", "inputs": '{"path": "files/a.csv"}'},
        {"event": "tool_end", "tool": "lookup", "call_id": "tool-3", "ok": True, "output": "323", "duration_ms": 17},
        {"event": "repl_output", "error": True, "error_type": "TimeoutError", "duration_ms": 23},
        {"event": "turn_complete", "answer": "A=7 [literal]\n6\nD", "learned_skill_note": "Separate note."},
    ):
        render.render_event(event)
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


def test_every_command_has_help_and_completion():
    text = commands.help_text()
    for name, command in commands.COMMANDS.items():
        assert command.usage.startswith(name) and f"`{command.usage}`" in text
    assert set(commands.COMMANDS) | {"/help", "/exit", "/quit"} == set(commands.completion_options())


def test_unknown_and_exit_commands(monkeypatch):
    output, _console = _capture_console(monkeypatch, width=100)
    assert commands.handle_command(None, "/nope", "s1") is True
    assert "Unknown command" in output.getvalue()
    assert commands.handle_command(None, "/exit", "s1") is False


def test_fullscreen_optimize_submits_options_to_api_worker() -> None:
    captured: list[dict[str, Any] | str] = []
    state = commands.SessionState(fullscreen=True, optimizer_launcher=captured.append)
    ctx = type("Context", (), {"session_id": "sess_tui", "state": state})()

    commands.COMMANDS["/optimize"].run(ctx, "--max-metric-calls 10")

    assert len(captured) == 1
    assert isinstance(captured[0], dict)
    assert captured[0]["max_metric_calls"] == 10
    assert len(captured[0]["request_id"]) == 36
    assert captured[0]["threads"] == 4
    assert captured[0]["request_id"]


def test_fullscreen_optimize_rejects_session_override_and_mlflow_mismatch() -> None:
    calls: list[Any] = []
    state = commands.SessionState(fullscreen=True, optimizer_launcher=calls.append)
    ctx = type("Context", (), {"session_id": "sess_tui", "state": state})()

    commands.COMMANDS["/optimize"].run(ctx, "--session other-session")
    commands.COMMANDS["/optimize"].run(ctx, "--mlflow databricks")

    assert calls == []


def test_non_tty_optimize_starts_and_polls_api_run(monkeypatch) -> None:
    import time

    requests: list[httpx.Request] = []
    run_id = "sess_cli-run"
    record = {"run_id": run_id, "status": "running", "stage": "search"}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(202, json={"experiment": record})
        record.update(status="completed", stage="completed")
        return httpx.Response(200, json={"experiment": record})

    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    output, _console = _capture_console(monkeypatch, width=100)
    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(handler)) as client:
        ctx = commands.Context(client, "sess_cli")
        commands.COMMANDS["/optimize"].run(ctx, "--max-metric-calls 10")

    assert [request.method for request in requests] == ["POST", "GET"]
    assert requests[0].url.path == "/sessions/sess_cli/experiments"
    assert json.loads(requests[0].content)["max_metric_calls"] == 10
    assert "Optimization completed" in output.getvalue()


def test_optimize_reuses_pending_start_request_until_definitive_outcome() -> None:
    state = commands.SessionState()
    ctx = commands.Context(httpx.Client(base_url="http://fleet.test"), "sess_retry", state)
    first = commands._parse_optimizer_arguments(ctx, "--max-metric-calls 10")[0]
    retry = commands._parse_optimizer_arguments(ctx, "--max-metric-calls 10")[0]
    changed = commands._parse_optimizer_arguments(ctx, "--max-metric-calls 11")[0]

    assert first["request_id"] == retry["request_id"]
    assert changed["request_id"] != first["request_id"]
    commands._finish_optimizer_request(state, changed["request_id"])
    fresh = commands._parse_optimizer_arguments(ctx, "--max-metric-calls 11")[0]
    assert fresh["request_id"] != changed["request_id"]
    ctx.client.close()


def test_turn_tracker_pairs_repl_cells_and_preserves_user_toggle_across_completion() -> None:
    tracker = render.TurnTracker()
    tracker.ingest({"event": "repl_code", "execution_id": "boot-1", "phase": "bootstrap", "code": "SHIM = 1"})
    tracker.ingest({"event": "repl_output", "execution_id": "boot-1", "phase": "bootstrap", "duration_ms": 12})
    tracker.ingest(
        {"event": "repl_code", "execution_id": "task-1", "phase": "task", "namespace": "ns_1", "code": "x = 2 + 2"}
    )
    tracker.ingest(
        {"event": "repl_code", "execution_id": "task-2", "phase": "task", "namespace": "ns_1", "code": "y = 1 / 0"}
    )

    boot, cell1, cell2 = tracker.items
    assert (boot.index, boot.expanded) == (0, False)
    assert (cell1.index, cell1.expanded) == (1, True)
    assert (cell2.index, cell2.expanded) == (2, True)

    # Clean completion auto-collapses cell #1; error completion keeps cell #2 expanded.
    tracker.ingest(
        {"event": "repl_output", "execution_id": "task-1", "phase": "task", "stdout": "4\n", "duration_ms": 18}
    )
    tracker.ingest(
        {
            "event": "repl_output",
            "execution_id": "task-2",
            "phase": "task",
            "error": True,
            "error_type": "ZeroDivisionError",
            "stderr": "division by zero",
            "duration_ms": 9,
        }
    )
    assert cell1.expanded is False
    assert cell2.expanded is True

    # If the user manually collapses a running cell, completion does not override their choice.
    tracker.ingest({"event": "repl_code", "execution_id": "task-3", "phase": "task", "code": "raise ValueError()"})
    cell3 = tracker.items[-1]
    assert cell3.expanded is True
    assert tracker.toggle_repl_index(3) is True
    assert cell3.expanded is False and cell3.user_toggled is True
    tracker.ingest(
        {"event": "repl_output", "execution_id": "task-3", "phase": "task", "error": True, "error_type": "ValueError"}
    )
    assert cell3.expanded is False


def test_turn_tracker_toggles_and_confidence_bar() -> None:
    tracker = render.TurnTracker()
    # An orphan_deleted event at Turn start must not hijack the 'root' alias.
    tracker.ingest({"event": "orphan_deleted", "sandbox_id": "sbx-orphan", "agent_id": "orphan-99"})
    tracker.ingest({"event": "triage_decision", "mode": "hybrid", "route": "rlm", "summary": "Run Python."})
    tracker.ingest(
        {
            "event": "lm_end",
            "agent_id": "real-root",
            "ok": True,
            "duration_ms": 50,
            "reasoning": "Inspect the CSV headers first.",
        }
    )
    tracker.ingest(
        {
            "event": "repl_code",
            "agent_id": "real-root",
            "execution_id": "e1",
            "phase": "task",
            "code": "print(17 * 19)",
        }
    )
    tracker.ingest(
        {
            "event": "repl_output",
            "agent_id": "real-root",
            "execution_id": "e1",
            "phase": "task",
            "stdout": "323\n",
            "duration_ms": 11,
        }
    )

    thinking, repl_cell = tracker.items
    assert thinking.agent_label == "root" and repl_cell.agent_label == "root"
    assert thinking.expanded is False and repl_cell.expanded is False

    assert tracker.toggle_thinking() is True and thinking.expanded is True
    assert tracker.toggle_latest_repl() is True and repl_cell.expanded is True
    assert tracker.toggle_all() is True and thinking.expanded is False and repl_cell.expanded is False
    assert tracker.toggle_all() is True and thinking.expanded is True and repl_cell.expanded is True

    # An in-flight running REPL cell transitions to 'cancelled' when the Turn is cancelled.
    tracker.ingest(
        {
            "event": "repl_code",
            "agent_id": "real-root",
            "execution_id": "e2",
            "phase": "task",
            "code": "while True: pass",
        }
    )
    assert tracker.items[-1].status == "running"
    tracker.ingest({"event": "turn_cancelled", "error": "Cancelled by operator."})
    assert tracker.items[-1].status == "cancelled"

    bar = render.confidence_bar_text(0.94)
    assert "94%" in bar.plain and "█" in bar.plain and "░" in bar.plain


def test_bootstrap_omitted_payloads_and_sandbox_resuming_render_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    output, _console = _capture_console(monkeypatch, width=110)

    for event in (
        {"event": "sandbox_resuming", "state": "paused", "agent_id": "root-1"},
        {"event": "orphan_deleted", "sandbox_id": "sbx-old", "agent_id": "child-9"},
        {"event": "repl_code", "phase": "bootstrap", "code": "VERY_LONG_SHIM_SETUP = 1", "execution_id": "b1"},
        {"event": "repl_output", "phase": "bootstrap", "duration_ms": 14, "execution_id": "b1"},
        {
            "event": "tool_start",
            "tool": "llm_query",
            "source": "sandbox_bridge",
            "inputs": "[predictor payload omitted]",
        },
        {
            "event": "tool_end",
            "tool": "llm_query",
            "ok": True,
            "duration_ms": 8,
            "output": "[predictor payload omitted]",
        },
        {
            "event": "triage_decision",
            "mode": "hybrid",
            "route": "rlm",
            "evidence": {"override_reason": "selected_context", "probability": 0.91},
        },
    ):
        render.render_event(event)

    rendered = output.getvalue()
    assert "Resuming paused Daytona sandbox" in rendered
    assert "Deleted orphaned sandbox sbx-old" in rendered
    assert "Python bootstrap · Daytona" in rendered
    assert "VERY_LONG_SHIM_SETUP" not in rendered
    assert "Bootstrap completed · 14 ms" in rendered
    assert "[predictor payload omitted]" not in rendered
    assert "override=selected_context" in rendered and "confidence=91%" in rendered


def test_http_error_detail_turn_command_and_workspace_completions(monkeypatch: pytest.MonkeyPatch) -> None:
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    output, _console = _capture_console(monkeypatch, width=110)
    state = commands.SessionState(last_turn_id="turn-42")

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions/s1/files":
            return httpx.Response(
                200,
                json={
                    "session_id": "s1",
                    "files": ["files/q3.csv", "artifacts/out.json"],
                    "artifacts": ["artifacts/out.json"],
                },
            )
        if request.url.path == "/sessions/s1/turns/turn-42":
            return httpx.Response(
                200,
                json={
                    "turn_id": "turn-42",
                    "status": "completed",
                    "updated_at": "2026-10-18T10:00:00Z",
                    "result": {"route": "rlm", "answer": "Reconciled.", "trace_id": "tr-42"},
                },
            )
        if request.url.path == "/skills":
            xml = '<available_skills><skill name="tabular-analysis">Check CSV totals.</skill></available_skills>'
            return httpx.Response(200, json={"catalog_xml": xml})
        return httpx.Response(409, json={"detail": "A Turn is running on this Session; retry when it completes"})

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(handle)) as client:
        commands.handle_command(client, "/files", "s1", state=state)
        commands.handle_command(client, "/turn", "s1", state=state)
        commands.handle_command(client, "/skills", "s1", state=state)
        commands.handle_command(client, "/cat", "s1", state=state)
        commands.handle_command(client, "/mode rlm", "s1", state=state)

    rendered = output.getvalue()
    assert "turn-42" in rendered and "Reconciled." in rendered and "tr-42" in rendered
    assert "• tabular-analysis: Check CSV totals." in rendered
    assert "Usage: /cat <path>" in rendered
    assert "HTTP 409: A Turn is running on this Session; retry when it completes" in rendered

    completer = app._completer(state)
    cat_completions = [c.text for c in completer.get_completions(Document("/cat files/"), CompleteEvent())]
    ctx_completions = [c.text for c in completer.get_completions(Document("/context "), CompleteEvent())]
    replay_completions = [c.text for c in completer.get_completions(Document("/replay "), CompleteEvent())]
    empty_completions = [c.text for c in completer.get_completions(Document("summarize file"), CompleteEvent())]
    assert "files/q3.csv" in cat_completions
    assert "clear" in ctx_completions and "files/q3.csv" in ctx_completions
    assert {"on", "off"} <= set(replay_completions)
    assert empty_completions == []


def test_fullscreen_tui_docks_input_streams_turns_and_toggles_repl_cells(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import asyncio

    from textual import events

    monkeypatch.chdir(tmp_path)
    events_stream = [
        {"event": "triage_decision", "mode": "hybrid", "route": "rlm", "summary": "Execute Python."},
        {"event": "repl_code", "execution_id": "ex-1", "phase": "task", "namespace": "ns_1", "code": "total = 17 * 19"},
        {"event": "repl_output", "execution_id": "ex-1", "phase": "task", "duration_ms": 22, "stdout": "323\n"},
        {"event": "turn_complete", "turn_id": "t-1", "route": "rlm", "answer": "Total is 323.", "trace_id": "tr-1"},
    ]
    submitted_tasks: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/config"):
            return httpx.Response(
                200,
                json={
                    "mode": "hybrid",
                    "models": {"root": "openai/system.ai.gpt-5", "sub": "openai/system.ai.gpt-5-mini"},
                    "context": {"workspace_path": "files/q3.csv", "size_bytes": 128, "sha256": "abc123456789"},
                    "limits": {
                        "lm_calls": 150,
                        "depth": 3,
                        "children": 8,
                        "active_children": 4,
                        "timeout_seconds": 900,
                    },
                },
            )
        submitted_tasks.append(json.loads(request.content)["task"])
        body = "".join(f"data: {json.dumps(ev)}\n\n" for ev in events_stream)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    async def scenario() -> None:
        state = commands.SessionState()
        async with _running_tui(httpx.MockTransport(handle), "sess_fs", (100, 30), state) as (client, tui, pilot):
            inp = tui.query_one("#prompt-input", app._PromptInput)
            assert inp.placeholder == "What would you like to do?"

            # Typing '/mo' + Tab autocompletes the first popup item ('/mode ') directly;
            # typing '/mo' + Down + Tab selects the second popup item ('/models ').
            inp.value = "/mo"
            await pilot.pause(0.05)
            assert tui.query_one("#slash-popup").highlighted == 0 and tui._popup_items[0][0] == "/mode "
            await pilot.press("tab")
            await pilot.pause(0.05)
            assert inp.value == "/mode "

            inp.value = "/mo"
            await pilot.pause(0.05)
            await pilot.press("down")
            assert tui.query_one("#slash-popup").highlighted == 1 and tui._popup_items[1][0] == "/models "
            await pilot.press("tab")
            await pilot.pause(0.05)
            assert inp.value == "/models "

            inp.value = "/config"
            await pilot.press("enter")
            inp.value = "/replay on"
            await pilot.press("enter")
            assert state.root_model == "openai/system.ai.gpt-5"
            assert state.context_path == "files/q3.csv"
            assert state.record_replay is True

            # Pasting a multi-paragraph prompt preserves all paragraphs and submits the full text on Enter.
            multiline_prompt = (
                "Answer with ONLY the final results.\n\n"
                "You are given a machine with 5 registers: A B C D E.\n\n"
                "PROGRAM:\n1. ADD A 5\n2. ADD B 3\n40. ADD A 1"
            )
            inp.post_message(events.Paste(multiline_prompt))
            await pilot.pause(0.05)
            assert inp.value == multiline_prompt
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause(0.03)
                if not tui._turn_active:
                    break
            tui._refresh_last_turn_widget()
            await pilot.pause(0.05)
            answer_output = io.StringIO()
            Console(file=answer_output, width=100).print(tui.query_one(app._TurnWidget)._answer_static.content)
            rendered_answer = answer_output.getvalue()
            assert "Total is 323." in rendered_answer
            assert "Turn persisted to Session history." in rendered_answer
            assert submitted_tasks == [multiline_prompt]

            assert state.last_tracker is not None
            assert len(state.last_tracker.items) == 1
            repl_item = state.last_tracker.items[0]
            assert repl_item.expanded is False

            step_widget = tui.query_one(app._StepWidget)
            collapsed_render = str(step_widget.render())
            assert "▸" in collapsed_render and "Python #1" in collapsed_render

            # Pressing Ctrl+O toggles the REPL cell open in place.
            await pilot.press("ctrl+o")
            await pilot.pause(0.05)
            assert repl_item.expanded is True

            # Clicking the step widget toggles it closed again.
            await pilot.click(app._StepWidget)
            await pilot.pause(0.05)
            assert repl_item.expanded is False

            # Exercise the active Textual bindings rather than a separate key dispatcher.
            state.last_tracker.ingest({"event": "lm_end", "reasoning": "Supporting check complete."})
            tui._refresh_last_turn_widget()
            await pilot.pause(0.05)
            thinking = state.last_tracker.items[-1]
            await pilot.press("ctrl+t")
            assert thinking.expanded is True
            await pilot.press("alt+a")
            assert all(item.expanded for item in state.last_tracker.items)
            await pilot.press("alt+a")
            assert all(not item.expanded for item in state.last_tracker.items)

            # Pressing Ctrl+C once clears input and arms exit; pressing Ctrl+C a second time exits.
            inp.value = "draft text"
            await pilot.press("ctrl+c")
            await pilot.pause(0.05)
            assert inp.value == "" and tui.is_running is True
            await pilot.press("ctrl+c")
            await pilot.pause(0.05)
            assert tui.is_running is False

            # Reloading history in a fresh session preserves multi-line pasted prompts as single entries.
            reloaded = app.FleetTextualApp(client, "sess_fs", commands.SessionState())
            assert reloaded._history_entries[-1] == multiline_prompt

    asyncio.run(scenario())


def test_fullscreen_cancellation_closes_the_active_response_without_claiming_completion(monkeypatch, tmp_path):
    import asyncio
    import threading

    monkeypatch.chdir(tmp_path)
    closed = threading.Event()

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"event":"repl_code","execution_id":"active","code":"work()"}\n\n'
            assert closed.wait(5), "the client did not close the active response"
            yield b'data: {"event":"turn_complete","answer":"must-not-consume"}\n\n'

        def close(self):
            closed.set()

    def handle(request):
        if request.url.path.endswith("/config"):
            return httpx.Response(200, json={})
        return httpx.Response(200, stream=Stream())

    async def scenario():
        state = commands.SessionState()
        async with _running_tui(httpx.MockTransport(handle), "session-cancel", (100, 30), state) as (
            _client,
            tui,
            pilot,
        ):
            tui.submit_text("Run work")
            for _ in range(50):
                await pilot.pause(0.02)
                if state.last_tracker and state.last_tracker.items:
                    break
            assert tui._turn_active and tui._active_response is not None
            assert state.last_tracker.items[0].status == "running"
            await pilot.press("ctrl+c")
            for _ in range(50):
                await pilot.pause(0.02)
                if not tui._turn_active:
                    break
            assert closed.is_set() and not tui._turn_active
            assert tui._active_response is None
            assert state.last_tracker.items[0].status == "cancelled"
            assert state.last_tracker.terminal_event == {"event": "interrupted"}
            assert tui.is_running

    asyncio.run(scenario())


def test_timeout_command_holds_a_client_local_override_and_rejects_invalid_values(monkeypatch):
    output, _console = _capture_console(monkeypatch, width=120)
    state = commands.SessionState()

    for command, expected in (("/timeout 120", 120), ("/timeout off", 0), ("/timeout default", None)):
        assert commands.handle_command(None, command, "s1", state=state) is True
        assert state.timeout_seconds == expected
    assert "120s" in output.getvalue() or "request 120s" in output.getvalue()

    for command in (
        "/timeout 3601",
        "/timeout true",
        "/timeout 1.5",
        "/timeout 5",
        "/timeout -1",
        "/timeout " + "9" * 5_000,
    ):
        commands.handle_command(None, command, "s1", state=state)
    assert state.timeout_seconds is None, "an invalid argument must not change the override"
    assert "Invalid /timeout" in output.getvalue()
    # Another client on the same Session starts with no override.
    assert commands.SessionState().timeout_seconds is None


@pytest.mark.parametrize(
    ("override", "expected"),
    [(120, {"timeout_seconds": 120}), (0, {"timeout_seconds": 0}), (None, {})],
)
def test_timeout_override_is_transmitted_per_turn(override, expected):
    submitted: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        submitted.append(json.loads(request.content))
        return httpx.Response(200, content=b"")

    state = commands.SessionState(timeout_seconds=override)
    with (
        httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(handle)) as client,
        app._request_turn(client, "session-1", "Do work", state) as response,
    ):
        response.read()

    if expected:
        assert submitted[0]["timeout_seconds"] == expected["timeout_seconds"]
    else:
        assert "timeout_seconds" not in submitted[0]


def test_tui_status_area_renders_advisory_labels_without_completion_claims() -> None:
    tracker = render.TurnTracker()
    tracker.ingest({"event": "tool_start", "call_id": "A", "tool": "lookup", "status": "waiting_on_host"})
    assert "waiting_on_host" in tracker.status_line().plain

    tracker.ingest({"event": "tool_end", "call_id": "A", "status": "progress_unknown"})
    assert "waiting_on_host" not in tracker.status_line().plain

    tracker.ingest(
        {
            "event": "progress",
            "phase": "checkpoint",
            "scope": "scan workspace",
            "checkpoint": 9,
            "expect_seconds": 45,
            "status": "progressing",
        }
    )
    line = tracker.status_line().plain
    assert "progressing" in line and "scan workspace" in line and "checkpoint 9" in line
    assert not any(token in line for token in ("%", "percent", "complete", "remaining", "ETA"))

    tracker.ingest({"event": "done"})
    assert tracker.status_label is None, "the label is absent after done"


def test_tui_progress_evidence_does_not_leak_between_scopes() -> None:
    tracker = render.TurnTracker()
    tracker.ingest({"event": "progress", "scope": "first", "checkpoint": 9, "expect_seconds": 5})
    tracker.ingest({"event": "progress", "phase": "start", "scope": "second", "expect_seconds": 10})
    assert tracker.status_scope == "second"
    assert tracker.status_checkpoint is None
    assert tracker.status_expect_seconds == 10

    tracker.ingest({"event": "status", "status": "progress_unknown"})
    assert tracker.status_scope is None
    assert tracker.status_checkpoint is None
    assert tracker.status_expect_seconds is None
    assert "second" not in tracker.status_line().plain


def test_tui_displays_the_stored_route_answer_and_elapsed_time() -> None:
    tracker = render.TurnTracker()
    # A local clock measurement would differ; the terminal event's recorded values win.
    tracker.started_at -= 100
    tracker.ingest(
        {
            "event": "turn_complete",
            "turn_id": "t1",
            "answer": "5",
            "route": "flex",
            "duration_seconds": 12.5,
        }
    )

    assert tracker.turn_id == "t1"
    assert tracker.route == "flex"
    assert tracker.terminal_event["answer"] == "5"
    assert tracker.elapsed_seconds() == 12.5
    summary = tracker.status_line().plain
    assert "route=flex" in summary and "12.5s" in summary


def test_rich_cli_renders_status_progress_and_fenced_observations(monkeypatch) -> None:
    output, _console = _capture_console(monkeypatch, width=100)

    render.render_event({"event": "status", "status": "stalled", "scope": "scan workspace"})
    render.render_event({"event": "progress", "scope": "scan workspace", "checkpoint": 9})
    render.render_event({"event": "fenced", "namespace": "ns-1"})

    rendered = output.getvalue()
    assert "stalled" in rendered and "scan workspace" in rendered
    assert "checkpoint 9" in rendered
    assert "Publication fenced" in rendered
    assert not any(token in rendered for token in ("%", "percent", "complete", "remaining", "ETA"))


def _experiment_record(run_id: str, status: str = "completed") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": status,
        "stage": status,
        "started_at": "2026-10-25T12:00:00+00:00",
        "finished_at": None,
        "candidate_version": run_id,
        "gate": {"passed": True, "reasons": []},
        "progress": {"type": "progress", "metric_calls_used": 3, "metric_calls_remaining": 7},
    }


def _experiment_transport(records: list[dict[str, Any]], *, comparison: dict[str, Any] | None = None):
    state: dict[str, Any] = {"records": records, "comparison": comparison, "requests": [], "accepting": True}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        parts = request.url.path.split("/")
        if request.method == "GET" and parts[-1] == "experiments":
            active = next(
                (record["run_id"] for record in state["records"] if record["status"] in {"running", "approving"}),
                None,
            )
            return httpx.Response(
                200,
                json={
                    "experiments": state["records"],
                    "active_run_id": active,
                    "accepting": state["accepting"],
                },
            )
        if request.method == "GET" and parts[-2] == "experiments":
            record = next(r for r in state["records"] if r["run_id"] == parts[-1])
            return httpx.Response(200, json={"experiment": record, "comparison": state["comparison"]})
        if request.method == "POST" and parts[-1] == "cancel":
            record = next(r for r in state["records"] if r["run_id"] == parts[-2])
            record.update(status="running", stage="stopping")
            return httpx.Response(202, json={"experiment": record})
        if request.method == "POST" and parts[-1] == "review":
            record = next(r for r in state["records"] if r["run_id"] == parts[-2])
            body = json.loads(request.content)
            status = "approving" if body["decision"] == "approved" else "rejected"
            record.update(status=status, decision=body["decision"])
            return httpx.Response(202, json={"experiment": record})
        if request.method == "POST" and parts[-1] == "experiments":
            record = _experiment_record("sess_api-run", "running")
            state["records"].insert(0, record)
            return httpx.Response(202, json={"experiment": record})
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler), state


def test_experiments_screen_fetches_session_scoped_api_snapshot_and_detail() -> None:
    run_id = "sess_experiments-run-1"
    records = [_experiment_record(run_id), _experiment_record("sess_other-run-1", "failed")]
    report = {"manifest": {"evaluation": {"baseline": {}, "candidate": {"component_diffs": []}}}}
    transport, _state = _experiment_transport(records, comparison=report)

    async def scenario() -> None:
        async with _running_tui(transport, "sess_experiments", (100, 30)) as (_client, tui, pilot):
            tui._open_experiments()
            for _ in range(20):
                await pilot.pause(0.05)
                if tui.screen.records:
                    break
            assert run_id in str(tui.screen.query_one("#experiment-history").render())
            assert "sess_other-run-1" in str(tui.screen.query_one("#experiment-history").render())
            assert "3/10 metric calls" in str(tui.screen.query_one("#experiments-progress").render())

    asyncio.run(scenario())


@pytest.mark.parametrize("stale_response", ["success", "error"])
def test_experiment_navigation_discards_delayed_evidence_and_guards_approval(stale_response):
    from textual.widgets import Button

    records = [_experiment_record("s1-run-a"), _experiment_record("s1-run-b")]
    started = {record["run_id"]: threading.Event() for record in records}
    release = {record["run_id"]: threading.Event() for record in records}

    def handler(request):
        run_id = request.url.path.rsplit("/", 1)[-1]
        if run_id == "experiments":
            return httpx.Response(200, json={"experiments": records, "active_run_id": None, "accepting": True})
        started[run_id].set()
        assert release[run_id].wait(5), "test did not release detail request"
        if stale_response == "error" and run_id == "s1-run-a":
            return httpx.Response(500)
        record = next(record for record in records if record["run_id"] == run_id)
        return httpx.Response(200, json={"experiment": record, "comparison": {"evidence_for": run_id}})

    async def scenario():
        try:
            async with _running_tui(httpx.MockTransport(handler), "s1", (100, 30)) as (_client, tui, pilot):
                tui._open_experiments()
                assert await asyncio.to_thread(started["s1-run-a"].wait, 5)
                screen = tui.screen
                reviews = []
                screen.on_review = lambda *args: reviews.append(args)
                await pilot.click("#experiment-next")
                assert screen.selected_run_id == "s1-run-b"
                approve = screen.query_one("#experiment-approve", Button)
                assert approve.disabled
                screen.on_button_pressed(Button.Pressed(approve))
                assert not reviews
                release["s1-run-a"].set()
                assert await asyncio.to_thread(started["s1-run-b"].wait, 5)
                assert screen.comparison is None
                assert screen.comparison_run_id is None
                assert approve.disabled
                assert "s1-run-a" not in str(screen.query_one("#experiment-detail").render())
                release["s1-run-b"].set()
                for _ in range(20):
                    await pilot.pause(0.05)
                    if screen.comparison_run_id == "s1-run-b":
                        break
                assert screen.comparison == {"evidence_for": "s1-run-b"}
                assert not approve.disabled
                screen.on_button_pressed(Button.Pressed(approve))
                assert reviews[0][:2] == ("s1-run-b", "approved")
        finally:
            for event in release.values():
                event.set()

    asyncio.run(scenario())


def test_experiment_detail_rejects_mismatched_response_identity():
    from textual.widgets import Button

    record = _experiment_record("s1-run")

    def handler(request):
        if request.url.path.endswith("/experiments"):
            return httpx.Response(200, json={"experiments": [record], "accepting": True})
        return httpx.Response(200, json={"experiment": _experiment_record("s1-other"), "comparison": {"wrong": True}})

    async def scenario():
        async with _running_tui(httpx.MockTransport(handler), "s1", (100, 30)) as (_client, tui, pilot):
            tui._open_experiments()
            for _ in range(20):
                await pilot.pause(0.05)
                if tui.screen.records and not tui.screen._polling:
                    break
            screen = tui.screen
            assert screen._selected()["run_id"] == "s1-run"
            assert screen.comparison is None and screen.comparison_run_id is None
            assert screen.query_one("#experiment-approve", Button).disabled

    asyncio.run(scenario())


@pytest.mark.parametrize("late_response", ["success", "error"])
def test_experiment_detail_arriving_after_dismissal_is_ignored(late_response):
    record = _experiment_record("s1-run")
    started, release = threading.Event(), threading.Event()

    def handler(request):
        if request.url.path.endswith("/experiments"):
            return httpx.Response(200, json={"experiments": [record], "accepting": True})
        started.set()
        assert release.wait(5)
        if late_response == "error":
            return httpx.Response(500)
        return httpx.Response(200, json={"experiment": record, "comparison": {"late": True}})

    async def scenario():
        try:
            async with _running_tui(httpx.MockTransport(handler), "s1", (100, 30)) as (_client, tui, pilot):
                tui._open_experiments()
                assert await asyncio.to_thread(started.wait, 5)
                screen = tui.screen
                await screen.dismiss()
                await pilot.pause()
                assert not screen.is_attached
                release.set()
                await pilot.pause()
                assert screen.comparison is None and screen.comparison_run_id is None
        finally:
            release.set()

    asyncio.run(scenario())


def test_experiments_detail_renders_api_saved_diagnostics_diffs_and_failures() -> None:
    run_id = "sess_detail-run-1"
    comparison = {
        "manifest": {
            "evaluation": {
                "baseline": {
                    "dev": 0.5,
                    "holdout": 0.6,
                    "holdout_objectives": {"quality": 0.4},
                    "holdout_lm_calls_median": 3,
                    "holdout_lm_calls_measured": 2,
                    "holdout_lm_calls": [2, 3],
                    "holdout_seconds_per_example_median": 1.25,
                    "holdout_seconds_per_example": [1.25],
                    "holdout_artifact_passes": 1,
                    "holdout_artifact_cases": 2,
                },
                "candidate": {
                    "dev": 0.7,
                    "holdout": 0.8,
                    "holdout_objectives": {"quality": 0.7},
                    "holdout_lm_calls_median": 2,
                    "holdout_lm_calls_measured": 3,
                    "holdout_lm_calls": [1, 2, 3],
                    "holdout_critical_scores": [1.0, 0.0],
                    "holdout_errors": 1,
                    "holdout_seconds_per_example_median": 0.9,
                    "holdout_seconds_per_example": [0.9],
                    "holdout_artifact_passes": 2,
                    "holdout_artifact_cases": 2,
                    "component_diffs": [
                        {
                            "component": "root.instructions",
                            "diff": "-old guidance\n+new guidance",
                            "before": "old guidance",
                            "after": "new guidance",
                        }
                    ],
                    "holdout_failures": [{"case_id": "abc123", "type": "RuntimeError", "detail": "sanitized failure"}],
                },
            }
        }
    }
    transport, _state = _experiment_transport([_experiment_record(run_id)], comparison=comparison)

    async def scenario() -> None:
        async with _running_tui(transport, "sess_detail", (120, 40)) as (_client, tui, pilot):
            tui._open_experiments()
            for _ in range(30):
                await pilot.pause(0.05)
                if tui.screen.comparison:
                    break
            detail = tui.screen._format_record(tui.screen._selected())
            for expected in (
                "0.600 → 0.800 (+0.200)",
                "quality  0.400 → 0.700 (+0.300)",
                "Holdout candidate: LM calls total 6, median 2 (3/3 measured)",
                "artifact passes 2/2 applicable",
                "Critical holdout cases 1/2 passed; 1 rollout error",
                "old guidance",
                "new guidance",
                "sanitized failure",
            ):
                assert expected in detail, detail

    asyncio.run(scenario())


def test_experiments_disconnect_keeps_observing_run_and_reopen_shows_final_record() -> None:
    running = _experiment_record("sess_detach-run", "running")
    transport, api = _experiment_transport([running])

    async def scenario() -> None:
        async with _running_tui(transport, "sess_detach", (100, 30)) as (_client, tui, pilot):
            tui._open_experiments()
            for _ in range(20):
                await pilot.pause(0.05)
                if tui.screen.records:
                    break
            await pilot.click("#experiment-close")
            assert not isinstance(tui.screen, app.ExperimentsScreen)
            assert tui._optimization_active
            assert running["status"] == "running"

            running.update(status="completed", stage="completed")
            for _ in range(40):
                await pilot.pause(0.1)
                if not tui._optimization_active:
                    break
            assert not tui._optimization_active
            assert not tui._optimization_stopping
            assert tui._optimization_terminal_status == "completed"
            assert not any(request.url.path.endswith("/cancel") for request in api["requests"])

            tui._open_experiments()
            for _ in range(20):
                await pilot.pause(0.05)
                if tui.screen.records:
                    break
            assert tui.screen.selected_run_id == "sess_detach-run"
            assert tui.screen._selected()["status"] == "completed"

    asyncio.run(scenario())


def test_ambiguous_start_retry_reuses_request_id_then_definitive_results_rotate_it() -> None:
    record = _experiment_record("sess_start-retry-run", "running")
    state = {"start_result": "network"}
    posts: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/experiments"):
            return httpx.Response(
                200,
                json={"experiments": [record], "active_run_id": record["run_id"], "accepting": True},
            )
        if request.method == "POST" and request.url.path.endswith("/experiments"):
            body = json.loads(request.content)
            posts.append(body)
            if state["start_result"] == "network":
                raise httpx.ConnectError("connection lost after submit", request=request)
            if state["start_result"] == "conflict":
                return httpx.Response(409, json={"detail": "busy"}, request=request)
            return httpx.Response(202, json={"experiment": record}, request=request)
        return httpx.Response(404, request=request)

    async def scenario() -> None:
        async with _running_tui(httpx.MockTransport(handle), "sess_start-retry", (100, 30)) as (client, tui, _pilot):
            ctx = commands.Context(client, "sess_start-retry", tui.state)
            first = commands._parse_optimizer_arguments(ctx, "--max-metric-calls 10")[0]
            await tui._post_optimization(first)
            retry = commands._parse_optimizer_arguments(ctx, "--max-metric-calls 10")[0]
            assert retry["request_id"] == first["request_id"]

            state["start_result"] = "accepted"
            await tui._post_optimization(retry)
            assert tui.state.pending_optimizer_request is None
            next_action = commands._parse_optimizer_arguments(ctx, "--max-metric-calls 10")[0]
            assert next_action["request_id"] != retry["request_id"]

            state["start_result"] = "conflict"
            await tui._post_optimization(next_action)
            assert tui.state.pending_optimizer_request is None
            assert [body["request_id"] for body in posts] == [
                first["request_id"],
                retry["request_id"],
                next_action["request_id"],
            ]

    asyncio.run(scenario())


def test_experiments_review_and_cancel_use_idempotent_api_requests() -> None:
    running = _experiment_record("sess_active-run", "running")
    completed = _experiment_record("sess_review-run", "completed")
    transport, api = _experiment_transport([running, completed])

    async def scenario() -> None:
        async with _running_tui(transport, "sess_actions", (120, 40)) as (_client, tui, pilot):
            tui._open_experiments()
            for _ in range(20):
                await pilot.pause(0.05)
                if len(tui.screen.records) == 2:
                    break
            screen = tui.screen
            first = screen._review_request_id("sess_review-run", "approved")
            second = screen._review_request_id("sess_review-run", "approved")
            assert first == second
            screen.selected_run_id = "sess_review-run"
            screen.on_review("sess_review-run", "approved", first)
            await pilot.pause(0.05)
            screen.on_review("sess_review-run", "approved", second)
            await pilot.pause(0.05)
            screen.selected_run_id = "sess_active-run"
            screen.on_cancel("sess_active-run")
            await pilot.pause(0.05)
            posts = [r for r in api["requests"] if r.method == "POST"]
            assert [r.url.path.rsplit("/", 1)[-1] for r in posts] == ["review", "review", "cancel"]
            assert json.loads(posts[0].content) == {"decision": "approved", "request_id": first}
            assert json.loads(posts[1].content) == json.loads(posts[0].content)
            assert running["status"] == "running" and running["stage"] == "stopping"
            assert completed["status"] == "approving"

    asyncio.run(scenario())


def test_review_request_retries_keep_id_until_known_outcome_then_rotate() -> None:
    record = _experiment_record("sess_review-retry-run", "completed")
    state = {"review_error": "conflict"}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/experiments"):
            return httpx.Response(200, json={"experiments": [record], "active_run_id": None, "accepting": True})
        if request.method == "GET" and request.url.path.endswith(f"/experiments/{record['run_id']}"):
            return httpx.Response(200, json={"experiment": record, "comparison": None})
        if request.method == "POST" and request.url.path.endswith("/review"):
            if state["review_error"] == "network":
                raise httpx.ConnectError("connection lost", request=request)
            return httpx.Response(409, json={"detail": "candidate is no longer reviewable"}, request=request)
        return httpx.Response(404, request=request)

    async def scenario() -> None:
        async with _running_tui(httpx.MockTransport(handle), "sess_review-retry", (100, 30)) as (_client, tui, pilot):
            tui._open_experiments()
            for _ in range(20):
                await pilot.pause(0.05)
                if isinstance(tui.screen, app.ExperimentsScreen) and tui.screen.records:
                    break
            screen = tui.screen
            first = screen._review_request_id(record["run_id"], "approved")
            assert len(first) == 36
            await tui._post_review(record["run_id"], "approved", first)
            retry = screen._review_request_id(record["run_id"], "approved")
            assert retry != first

            state["review_error"] = "network"
            await tui._post_review(record["run_id"], "approved", retry)
            assert screen._review_request_id(record["run_id"], "approved") == retry

    asyncio.run(scenario())
