"""Offline domain regression coverage."""

from __future__ import annotations

import asyncio
import builtins
import io
import json
import os
import shlex
import signal
import sqlite3
import sys
import threading
import time
import types
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from rich.console import Console

import fleet_rlm.tui.app as app
import fleet_rlm.tui.commands as commands
import fleet_rlm.tui.render as render
import fleet_rlm.tui.widgets as widgets
from fleet_rlm import cli, launcher
from fleet_rlm.api import app as api
from fleet_rlm.config import FleetSettings
from fleet_rlm.launcher import LaunchError, LaunchPlan, ManagedProcess, plan_launch
from fleet_rlm.tui import local_paths, mentions, web, web_client
from fleet_rlm.tui.app import FleetTextualApp, _PromptInput
from fleet_rlm.tui.commands import COMMANDS, SessionState

# Consolidated from tui/test_tui.py


# Terminal rendering and commands


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
def test_improve_actions_are_visible_in_compact_terminals(size) -> None:
    from textual.widgets import Button, TabbedContent

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
            tui._open_improve()
            await pilot.pause()
            panel = tui.screen.query_one("#improve-panel")
            tabs = tui.screen.query_one("#improve-tabs", TabbedContent)
            for tab in ("review", "readiness", "runs", "results"):
                tabs.active = tab
                await pilot.pause(0.1)
                pane = tabs.get_pane(tab)
                for button in pane.query("Button"):
                    assert panel.region.contains_region(button.region), (tab, button.id)
            detail = tui.screen.query_one("#results-scroll")
            assert detail.region.bottom <= tui.screen.query_one("#job-approve").region.y
            assert detail.max_scroll_y > 0
            detail.scroll_end(animate=False)
            await pilot.pause()
            assert detail.scroll_y > 0
            close = tui.screen.query_one("#improve-close", Button)
            assert panel.region.contains_region(close.region)
            close.focus()
            await pilot.press("enter")
            assert not isinstance(tui.screen, app.ImproveScreen)

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
    assert "`/skills [pull|push|require <names...>|clear]`" in rendered and "`/exit`" in rendered


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
            assert request.url.params["role"] == "sub"
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


def test_fullscreen_review_uses_typed_callback_without_launch_roundtrip():
    launches, reviews = [], []
    state = commands.SessionState(
        fullscreen=True,
        optimizer_launcher=launches.append,
        optimizer_reviewer=lambda *args: reviews.append(args),
    )
    ctx = type("Context", (), {"session_id": "sess_tui", "state": state})()
    commands.COMMANDS["/optimize"].run(ctx, "--approve sess_tui-version")
    assert launches == []
    assert len(reviews) == 1 and reviews[0][:2] == ("sess_tui-version", "approved")
    assert len(reviews[0][2]) == 36


def test_plain_optimize_opens_runs_tab_and_run_button_confirms_before_starting():
    from types import SimpleNamespace

    launches, opened, pushed = [], [], []
    state = commands.SessionState(fullscreen=True, optimizer_launcher=launches.append, improve_opener=opened.append)
    ctx = commands.Context(None, "sess_tui", state)
    commands.COMMANDS["/optimize"].run(ctx, "")
    commands.COMMANDS["/improve"].run(ctx, "")
    commands.COMMANDS["/experiments"].run(ctx, "")
    assert opened == ["runs", "review", "review"] and launches == []

    fake = SimpleNamespace(
        client=None,
        session_id="sess_tui",
        state=state,
        _start_optimization=launches.append,
        push_screen=lambda screen, callback: pushed.append((screen, callback)),
    )
    app.FleetTextualApp._start_default_optimization(fake)
    screen, callback = pushed[0]
    assert "light GEPA budget" in screen.message and "FLEET_JOB_MAX_TOKENS" in screen.message
    callback(False)
    assert launches == []
    callback(True)
    commands.COMMANDS["/optimize"].run(ctx, "--gepa-budget light")
    assert len(launches) == 2 and launches[0] == launches[1]


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
            return httpx.Response(202, json={"job": record})
        record.update(status="completed", stage="completed")
        return httpx.Response(200, json={"job": record})

    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    output, _console = _capture_console(monkeypatch, width=100)
    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(handler)) as client:
        ctx = commands.Context(client, "sess_cli")
        commands.COMMANDS["/optimize"].run(ctx, "--max-metric-calls 10")

    assert [request.method for request in requests] == ["POST", "GET"]
    assert requests[0].url.path == "/improve/jobs"
    assert json.loads(requests[0].content)["max_metric_calls"] == 10
    assert "Optimization completed" in output.getvalue()


def test_non_tty_optimize_deduplicates_repeated_stage_output(monkeypatch) -> None:
    import time

    polls = 0
    run_id = "sess_cli-dedupe"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        if request.method == "POST":
            return httpx.Response(202, json={"job": {"run_id": run_id, "status": "running", "stage": "search"}})
        polls += 1
        if polls < 3:
            return httpx.Response(200, json={"job": {"run_id": run_id, "status": "running", "stage": "search"}})
        if polls == 3:
            return httpx.Response(200, json={"job": {"run_id": run_id, "status": "running", "stage": "validation"}})
        return httpx.Response(200, json={"job": {"run_id": run_id, "status": "completed", "stage": "completed"}})

    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    output, _console = _capture_console(monkeypatch, width=100)
    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(handler)) as client:
        ctx = commands.Context(client, "sess_cli")
        commands.COMMANDS["/optimize"].run(ctx, "--max-metric-calls 10")

    rendered = output.getvalue()
    assert rendered.count("Optimization stage: search") == 1
    assert rendered.count("Optimization stage: validation") == 1
    assert "Optimization completed" in rendered


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

            step_widget = tui.query_one(widgets._StepWidget)
            collapsed_render = str(step_widget.render())
            assert "▸" in collapsed_render and "Python #1" in collapsed_render

            # Spinner ticks must not invalidate every step's cached rendering.
            with monkeypatch.context() as patch:
                refreshed = []
                patch.setattr(step_widget, "refresh", lambda **kwargs: refreshed.append(kwargs))
                for _ in range(5):
                    tui.query_one(app._TurnWidget).sync_from_tracker()
                assert len(refreshed) == 5
                refreshed.clear()
                patch.setattr(tui, "_turn_active", True)
                patch.setattr(tui, "_active_turn_widget", tui.query_one(app._TurnWidget))
                for _ in range(5):
                    tui._tick_live_turn()
                assert refreshed == []

            # Pressing Ctrl+O toggles the REPL cell open in place.
            await pilot.press("ctrl+o")
            await pilot.pause(0.05)
            assert repl_item.expanded is True

            # Clicking the step widget toggles it closed again.
            await pilot.click(widgets._StepWidget)
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


def test_required_skills_command_preserves_order_validates_and_transmits(monkeypatch):
    output, _console = _capture_console(monkeypatch)
    state = commands.SessionState()
    commands.handle_command(None, "/skills require tabular-analysis delegation", "s1", state=state)
    assert state.requested_skills == ("tabular-analysis", "delegation")
    for invalid in ("A", "a a", "a b c d", "../escape"):
        commands.handle_command(None, "/skills require " + invalid, "s1", state=state)
        assert state.requested_skills == ("tabular-analysis", "delegation")
    submitted = []

    def handle(request):
        submitted.append(json.loads(request.content))
        return httpx.Response(200, content=b"")

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(handle)) as client:
        with app._request_turn(client, "s1", "task", state) as response:
            response.read()
        commands.handle_command(None, "/skills clear", "s1", state=state)
        with app._request_turn(client, "s1", "next task", state) as response:
            response.read()
    assert submitted[0]["requested_skills"] == ["tabular-analysis", "delegation"]
    assert "requested_skills" not in submitted[1] and state.requested_skills == ()
    assert "Required Skills" in output.getvalue()


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

    tracker.ingest({"event": "status", "status": "progress_unknown"})
    assert tracker.status_scope is None
    assert tracker.status_checkpoint is None
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
        if request.method == "GET" and parts[-1] == "jobs":
            active = next(
                (record["run_id"] for record in state["records"] if record["status"] in {"running", "approving"}),
                None,
            )
            return httpx.Response(
                200,
                json={
                    "jobs": state["records"],
                    "active_run_id": active,
                    "accepting": state["accepting"],
                },
            )
        if request.method == "GET" and parts[-2] == "jobs":
            record = next(r for r in state["records"] if r["run_id"] == parts[-1])
            return httpx.Response(200, json={"job": record, "comparison": state["comparison"]})
        if request.method == "POST" and parts[-1] == "cancel":
            record = next(r for r in state["records"] if r["run_id"] == parts[-2])
            record.update(status="running", stage="stopping")
            return httpx.Response(202, json={"job": record})
        if request.method == "POST" and parts[-1] == "review":
            record = next(r for r in state["records"] if r["run_id"] == parts[-2])
            body = json.loads(request.content)
            status = "approving" if body["decision"] == "approved" else "rejected"
            record.update(status=status, decision=body["decision"])
            return httpx.Response(202, json={"job": record})
        if request.method == "POST" and parts[-1] == "jobs":
            record = _experiment_record("sess_api-run", "running")
            state["records"].insert(0, record)
            return httpx.Response(202, json={"job": record})
        if (
            request.method == "GET"
            and parts[-1] in {"queue", "readiness", "history", "settings"}
            and parts[-1] in state
        ):
            return httpx.Response(200, json=state[parts[-1]])
        if request.method == "PUT" and parts[-1] == "settings":
            state.setdefault("saved_settings", []).append(json.loads(request.content))
            state["settings"] = {**state.get("settings", {}), **state["saved_settings"][-1]}
            return httpx.Response(200, json=state["settings"])
        if request.method == "POST" and parts[-1] == "labels":
            state.setdefault("labels", []).append(json.loads(request.content))
            return httpx.Response(200, json={"turn_id": state["labels"][-1]["turn_id"], "status": "approved"})
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler), state


def test_experiments_screen_fetches_the_global_job_snapshot_and_detail() -> None:
    run_id = "sess_experiments-run-1"
    records = [_experiment_record(run_id), _experiment_record("sess_other-run-1", "failed")]
    report = {"manifest": {"evaluation": {"baseline": {}, "candidate": {"component_diffs": []}}}}
    transport, state = _experiment_transport(records, comparison=report)

    async def scenario() -> None:
        async with _running_tui(transport, "sess_experiments", (100, 30)) as (_client, tui, pilot):
            tui._open_improve()
            for _ in range(20):
                await pilot.pause(0.05)
                if tui.screen.records:
                    break
            table = tui.screen.query_one("#runs-table")
            assert [row.value for row in table.rows] == [run_id, "sess_other-run-1"]
            assert "3/10 metric calls" in str(tui.screen.query_one("#improve-progress").render())
            paths = {request.url.path for request in state["requests"] if request.method == "GET"}
            assert {"/improve/jobs", f"/improve/jobs/{run_id}"} <= paths
            assert not any(path.startswith("/sessions") for path in paths)

    asyncio.run(scenario())


@pytest.mark.parametrize("stale_response", ["success", "error"])
def test_experiment_navigation_discards_delayed_evidence_and_guards_approval(stale_response):
    from textual.widgets import Button

    records = [_experiment_record("s1-run-a"), _experiment_record("s1-run-b")]
    started = {record["run_id"]: threading.Event() for record in records}
    release = {record["run_id"]: threading.Event() for record in records}

    def handler(request):
        run_id = request.url.path.rsplit("/", 1)[-1]
        if run_id == "jobs":
            return httpx.Response(200, json={"jobs": records, "active_run_id": None, "accepting": True})
        if run_id not in started:
            return httpx.Response(404)
        started[run_id].set()
        assert release[run_id].wait(5), "test did not release detail request"
        if stale_response == "error" and run_id == "s1-run-a":
            return httpx.Response(500)
        record = next(record for record in records if record["run_id"] == run_id)
        return httpx.Response(200, json={"job": record, "comparison": {"evidence_for": run_id}})

    async def scenario():
        try:
            async with _running_tui(httpx.MockTransport(handler), "s1", (100, 30)) as (_client, tui, pilot):
                tui._open_improve()
                assert await asyncio.to_thread(started["s1-run-a"].wait, 5)
                screen = tui.screen
                reviews = []
                screen.on_review = lambda *args: reviews.append(args)
                screen.query_one("#runs-table").move_cursor(row=1)
                await pilot.pause()
                assert screen.selected_run_id == "s1-run-b"
                approve = screen.query_one("#job-approve", Button)
                assert approve.disabled
                screen.on_button_pressed(Button.Pressed(approve))
                assert not reviews
                release["s1-run-a"].set()
                assert await asyncio.to_thread(started["s1-run-b"].wait, 5)
                assert screen.comparison is None
                assert screen.comparison_run_id is None
                assert approve.disabled
                assert "s1-run-a" not in str(screen.query_one("#results-text").render())
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
        if request.url.path.endswith("/improve/jobs"):
            return httpx.Response(200, json={"jobs": [record], "accepting": True})
        return httpx.Response(200, json={"job": _experiment_record("s1-other"), "comparison": {"wrong": True}})

    async def scenario():
        async with _running_tui(httpx.MockTransport(handler), "s1", (100, 30)) as (_client, tui, pilot):
            tui._open_improve()
            for _ in range(20):
                await pilot.pause(0.05)
                if tui.screen.records and not tui.screen._polling:
                    break
            screen = tui.screen
            assert screen._selected()["run_id"] == "s1-run"
            assert screen.comparison is None and screen.comparison_run_id is None
            assert screen.query_one("#job-approve", Button).disabled

    asyncio.run(scenario())


@pytest.mark.parametrize("late_response", ["success", "error"])
def test_experiment_detail_arriving_after_dismissal_is_ignored(late_response):
    record = _experiment_record("s1-run")
    started, release = threading.Event(), threading.Event()

    def handler(request):
        if request.url.path.endswith("/improve/jobs"):
            return httpx.Response(200, json={"jobs": [record], "accepting": True})
        started.set()
        assert release.wait(5)
        if late_response == "error":
            return httpx.Response(500)
        return httpx.Response(200, json={"job": record, "comparison": {"late": True}})

    async def scenario():
        try:
            async with _running_tui(httpx.MockTransport(handler), "s1", (100, 30)) as (_client, tui, pilot):
                tui._open_improve()
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
            tui._open_improve()
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
            tui._open_improve()
            for _ in range(20):
                await pilot.pause(0.05)
                if tui.screen.records:
                    break
            await pilot.click("#improve-close")
            assert not isinstance(tui.screen, app.ImproveScreen)
            assert tui._optimization_active
            assert running["status"] == "running"

            running.update(status="completed", stage="completed")
            for _ in range(40):
                await pilot.pause(0.1)
                if not tui._optimization_active:
                    break
            assert not tui._optimization_active
            assert not tui._optimization_stopping
            assert running["status"] == "completed"
            assert not any(request.url.path.endswith("/cancel") for request in api["requests"])

            tui._open_improve()
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
        if request.method == "GET" and request.url.path.endswith("/improve/jobs"):
            return httpx.Response(
                200,
                json={"jobs": [record], "active_run_id": record["run_id"], "accepting": True},
            )
        if request.method == "POST" and request.url.path.endswith("/improve/jobs"):
            body = json.loads(request.content)
            posts.append(body)
            if state["start_result"] == "network":
                raise httpx.ConnectError("connection lost after submit", request=request)
            if state["start_result"] == "conflict":
                return httpx.Response(409, json={"detail": "busy"}, request=request)
            return httpx.Response(202, json={"job": record}, request=request)
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


def test_ctrl_c_clears_and_quits_during_optimization_while_escape_cancels(monkeypatch):
    record = _experiment_record("sess_keys-run", "running")
    transport, _api = _experiment_transport([record])

    async def scenario():
        async with _running_tui(transport, "sess_keys", (100, 30)) as (_client, tui, pilot):
            stops = []
            monkeypatch.setattr(tui, "_request_optimizer_stop", lambda: stops.append(True))
            tui._optimizer_snapshot(record)
            inp = tui.query_one("#prompt-input", app._PromptInput)
            await pilot.press("escape")
            assert stops == [True]
            inp.value = "unfinished prompt"
            await pilot.press("ctrl+c")
            assert inp.value == "" and tui.is_running
            assert stops == [True]
            await pilot.press("ctrl+c")
            assert not tui.is_running
            assert stops == [True]

    asyncio.run(scenario())


def test_experiment_history_error_keeps_app_observing_after_dismissal():
    record = _experiment_record("sess_history-run", "running")
    state = {"fail": False}

    def handler(request):
        if request.url.path.endswith("/improve/jobs"):
            if state["fail"]:
                return httpx.Response(503)
            active = record["run_id"] if record["status"] == "running" else None
            return httpx.Response(200, json={"jobs": [record], "active_run_id": active, "accepting": True})
        if request.url.path.endswith(record["run_id"]):
            return httpx.Response(200, json={"job": record, "comparison": None})
        return httpx.Response(200, json={})

    async def scenario():
        async with _running_tui(httpx.MockTransport(handler), "sess_history", (100, 30)) as (_client, tui, pilot):
            tui._open_improve()
            screen = tui.screen
            for _ in range(30):
                await pilot.pause(0.05)
                if screen.records and not screen._polling:
                    break
            assert tui._optimization_active
            state["fail"] = True
            await screen._refresh()
            assert screen.records == []
            assert tui._optimization_run_id == record["run_id"]
            assert tui._optimization_active
            await screen.dismiss()
            state["fail"] = False
            record.update(status="completed", stage="completed")
            tui._tick_optimizer_snapshot()
            for _ in range(30):
                await pilot.pause(0.05)
                if not tui._optimization_active:
                    break
            assert not tui._optimization_active
            assert tui._optimization_terminal_status == "completed"

    asyncio.run(scenario())


def test_experiments_review_and_cancel_use_idempotent_api_requests() -> None:
    running = _experiment_record("sess_active-run", "running")
    completed = _experiment_record("sess_review-run", "completed")
    transport, api = _experiment_transport([running, completed])

    async def scenario() -> None:
        async with _running_tui(transport, "sess_actions", (120, 40)) as (_client, tui, pilot):
            tui._open_improve()
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
        if request.method == "GET" and request.url.path.endswith("/improve/jobs"):
            return httpx.Response(200, json={"jobs": [record], "active_run_id": None, "accepting": True})
        if request.method == "GET" and request.url.path.endswith(f"/improve/jobs/{record['run_id']}"):
            return httpx.Response(200, json={"job": record, "comparison": None})
        if request.method == "POST" and request.url.path.endswith("/review"):
            if state["review_error"] == "network":
                raise httpx.ConnectError("connection lost", request=request)
            return httpx.Response(409, json={"detail": "candidate is no longer reviewable"}, request=request)
        return httpx.Response(404, request=request)

    async def scenario() -> None:
        async with _running_tui(httpx.MockTransport(handle), "sess_review-retry", (100, 30)) as (_client, tui, pilot):
            tui._open_improve()
            for _ in range(20):
                await pilot.pause(0.05)
                if isinstance(tui.screen, app.ImproveScreen) and tui.screen.records:
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


def test_discovery_error_keeps_manual_native_provider_selection_available(monkeypatch):
    output, _console = _capture_console(monkeypatch, width=100)
    writes = []

    def handle(request):
        if request.method == "PUT":
            payload = json.loads(request.content)
            writes.append(payload)
            return httpx.Response(200, json=payload)
        return httpx.Response(503, json={"detail": "private provider credential"})

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(handle)) as client:
        commands.handle_command(client, "/model root", "s1")
        commands.handle_command(client, "/model root anthropic/manual-route", "s1")
    assert writes == [{"role": "root", "model": "anthropic/manual-route"}]
    assert "discovery is unavailable" in output.getvalue()
    assert "private provider credential" not in output.getvalue()


_QUEUE_ITEM = {
    "turn_id": "turn-1",
    "session_id": "sess_label_one",
    "task": "Total revenue for Q3?",
    "answer": "Revenue was 1250.50 across 17 stores.",
    "figures": ["1250.50", "17"],
    "label": "unlabelled",
    "expected_answer": None,
    "expected_values": [],
    "critical": False,
    "group": None,
    "updated_at": "2026-11-01T10:00:00+00:00",
}


async def _wait_for(pilot, condition) -> None:
    for _ in range(60):
        await pilot.pause(0.05)
        if condition():
            return
    raise AssertionError("condition was not reached")


def test_improve_tabs_render_from_api_snapshots() -> None:
    from textual.widgets import Checkbox, DataTable, Input, Sparkline, TabbedContent

    running = _experiment_record("sess_run-a", "running")
    running.update(
        stage="search",
        started_at="2026-10-25T12:00:00+00:00",
        progress={
            "metric_calls_used": 3,
            "metric_calls_remaining": 7,
            "tokens_used": 1200,
            "token_cap": 4800,
            "best_score": 0.725,
            "score_trajectory": [[0, 0.5], [1, 0.6], [2, 0.725]],
        },
    )
    transport, api = _experiment_transport([running])
    api["queue"] = {"items": [_QUEUE_ITEM]}
    api["readiness"] = {
        "ready": False,
        "examples": 5,
        "groups": 2,
        "critical": 0,
        "split_sizes": {"train": 3, "dev": 1, "holdout": 1},
        "missing": ["At least 3 source groups are needed (have 2)"],
        "new_labels_since_last_run": 4,
    }

    async def scenario() -> None:
        async with _running_tui(transport, "sess_tabs", (110, 40)) as (_client, tui, pilot):
            tui._open_improve()
            await pilot.pause()
            screen = tui.screen
            await _wait_for(pilot, lambda: screen.queue and screen.readiness and screen.records)
            await pilot.pause(0.1)
            # Review: the queue row, the Turn text, figures pre-checked, answer prefilled.
            assert screen.query_one("#review-queue", DataTable).row_count == 1
            assert "Total revenue for Q3?" in str(screen.query_one("#review-text").render())
            boxes = [box for _, box in screen._figure_boxes]
            assert [str(box.label) for box in boxes] == ["1250.50", "17"] and all(box.value for box in boxes)
            assert screen.query_one("#review-answer", Input).value == _QUEUE_ITEM["answer"]
            assert not screen.query_one("#review-critical", Checkbox).value
            # Readiness: counts, split sizes, each missing item, new labels.
            readiness = str(screen.query_one("#readiness-text").render())
            for expected in (
                "NOT READY",
                "Labelled examples  5",
                "train 3",
                "At least 3 source groups",
                "since the last run  4",
            ):
                assert expected in readiness, readiness
            # Runs: stepper, bars, best score, sparkline.
            screen.query_one("#improve-tabs", TabbedContent).active = "runs"
            await pilot.pause(0.1)
            detail = str(screen.query_one("#runs-detail").render())
            assert "✓ preparing → ▶ search → · validation → · finalization" in detail
            assert "3/10" in detail and "1,200/4,800" in detail and "0.725" in detail and "Elapsed" in detail
            assert screen.query_one("#runs-spark", Sparkline).data == [0.5, 0.6, 0.725]

    asyncio.run(scenario())


def test_review_buttons_post_labels_from_the_form() -> None:
    from textual.widgets import Checkbox, Input

    transport, api = _experiment_transport([])
    second = {**_QUEUE_ITEM, "turn_id": "turn-2", "answer": "It was 99 units."}
    api["queue"] = {"items": [_QUEUE_ITEM, second]}

    async def scenario() -> None:
        async with _running_tui(transport, "sess_labels", (110, 40)) as (_client, tui, pilot):
            tui._open_improve()
            await pilot.pause()
            screen = tui.screen
            await _wait_for(pilot, lambda: screen._figure_boxes)
            # Uncheck 17, add extra values, mark critical, name a group, then approve.
            screen._figure_boxes[1][1].value = False
            screen.query_one("#review-extra", Input).value = "2026, Q3"
            screen.query_one("#review-critical", Checkbox).value = True
            screen.query_one("#review-group", Input).value = "finance"
            screen.query_one("#label-approve").press()
            await _wait_for(pilot, lambda: api.get("labels"))
            assert api["labels"][0] == {
                "turn_id": "turn-1",
                "status": "approved",
                "expected_values": ["1250.50", "2026", "Q3"],
                "critical": True,
                "group": "finance",
            }  # the unedited preview is not sent: the server defaults to the Turn's full stored answer
            # The form moves on to the next Turn; rejecting the unchanged answer is a plain rejection.
            await _wait_for(pilot, lambda: screen.query_one("#review-answer", Input).value == second["answer"])
            screen.query_one("#label-reject").press()
            await _wait_for(pilot, lambda: len(api["labels"]) == 2)
            assert api["labels"][1] == {"turn_id": "turn-2", "status": "rejected"}
            # A corrected answer is saved as approved gold with the correction's own figures.
            screen.query_one("#review-answer", Input).value = "It was 120 units."
            screen.query_one("#label-reject").press()
            await _wait_for(pilot, lambda: len(api["labels"]) == 3)
            assert api["labels"][2] == {
                "turn_id": "turn-2",
                "status": "approved",
                "expected_values": ["120"],
                "expected_answer": "It was 120 units.",
            }

    asyncio.run(scenario())


def test_run_button_confirms_budget_and_token_cap_before_posting_the_job() -> None:
    from textual.widgets import TabbedContent

    transport, api = _experiment_transport([])

    def posts() -> list[httpx.Request]:
        return [request for request in api["requests"] if request.method == "POST"]

    async def scenario() -> None:
        async with _running_tui(transport, "sess_run", (110, 40)) as (_client, tui, pilot):
            tui._open_improve()
            await pilot.pause()
            screen = tui.screen
            screen.query_one("#improve-tabs", TabbedContent).active = "runs"
            await _wait_for(pilot, lambda: not screen.query_one("#job-run").disabled)
            await pilot.click("#job-run")
            await pilot.pause(0.1)
            assert isinstance(tui.screen, app.ConfirmAction)
            assert "light GEPA budget" in tui.screen.message and "Token cap" in tui.screen.message
            await pilot.click("#cancel")
            await pilot.pause(0.1)
            assert not posts()
            await pilot.click("#job-run")
            await pilot.pause(0.1)
            await pilot.click("#confirm")
            await _wait_for(pilot, posts)
            body = json.loads(posts()[0].content)
            assert posts()[0].url.path == "/improve/jobs"
            assert body["gepa_budget"] == "light" and body["request_id"]

    asyncio.run(scenario())


def test_results_tab_sorts_examples_by_delta_and_shows_the_installed_trend() -> None:
    from textual.widgets import DataTable, TabbedContent

    comparison = {
        "manifest": {
            "evaluation": {
                "baseline": {"dev": 0.5, "holdout": 0.5, "dev_scores": [0.5, 1.0], "holdout_scores": [0.0, 0.5]},
                "candidate": {"dev": 0.5, "holdout": 0.75, "dev_scores": [0.5, 0.25], "holdout_scores": [1.0, 0.5]},
            }
        }
    }
    done = _experiment_record("sess_done-run")
    done["gate"] = {"passed": False, "reasons": ["dev regressed"]}
    transport, api = _experiment_transport([done], comparison=comparison)
    api["history"] = {
        "versions": [
            {"run_id": "v2", "scores": {"candidate": {"holdout": 0.8}}},
            {"run_id": "v1", "scores": {"candidate": {"holdout": 0.6}}},
        ],
        "evaluations": [],
    }

    async def scenario() -> None:
        async with _running_tui(transport, "sess_results", (110, 40)) as (_client, tui, pilot):
            tui._open_improve()
            await pilot.pause()
            screen = tui.screen
            screen.query_one("#improve-tabs", TabbedContent).active = "results"
            await _wait_for(pilot, lambda: screen.comparison and screen.history)
            await pilot.pause(0.1)
            table = screen.query_one("#results-examples", DataTable)
            rows = [table.get_row_at(i) for i in range(table.row_count)]
            assert rows == [
                ["holdout #1", "0.000", "1.000", "+1.000", "better"],
                ["dev #1", "0.500", "0.500", "+0.000", "same"],
                ["holdout #2", "0.500", "0.500", "+0.000", "same"],
                ["dev #2", "1.000", "0.250", "-0.750", "worse"],
            ]
            text = str(screen.query_one("#results-text").render())
            assert "Promotion gate  FAIL" in text and "dev regressed" in text
            assert "0.500 → 0.750 (+0.250)" in text
            assert "INSTALLED TREND · HOLDOUT (2 versions)  0.600 → 0.800" in text
            assert "1 better, 1 worse, 2 unchanged" in text

    asyncio.run(scenario())


def test_evaluate_choices_start_each_kind_and_only_replay_and_synthetic_ask_to_confirm() -> None:
    from textual.widgets import TabbedContent

    transport, api = _experiment_transport([])

    def posts() -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in api["requests"] if r.method == "POST" and r.url.path == "/improve/jobs"]

    async def scenario() -> None:
        async with _running_tui(transport, "sess_eval", (110, 40)) as (_client, tui, pilot):
            tui._open_improve()
            await pilot.pause()
            screen = tui.screen
            screen.query_one("#improve-tabs", TabbedContent).active = "runs"
            await _wait_for(pilot, lambda: not screen.query_one("#evaluate_rescore").disabled)
            # Rescore only calls the judges, so it starts without a cost prompt.
            await pilot.click("#evaluate_rescore")
            await _wait_for(pilot, posts)
            assert [post["kind"] for post in posts()] == ["evaluate_rescore"]
            # The mock ledger now holds a running job, so the next kinds wait for it to finish.
            api["records"][0]["status"] = "completed"
            await _wait_for(pilot, lambda: not screen.query_one("#evaluate_replay").disabled)
            assert screen.query_one("#evaluate_synthetic").disabled
            for kind, words in (("evaluate_replay", "Replay"),):
                await pilot.click(f"#{kind}")
                await pilot.pause(0.1)
                assert isinstance(tui.screen, app.ConfirmAction) and words in tui.screen.message
                before = len(posts())
                await pilot.click("#cancel")
                await pilot.pause(0.1)
                assert len(posts()) == before
                await pilot.click(f"#{kind}")
                await pilot.pause(0.1)
                await pilot.click("#confirm")
                await _wait_for(pilot, lambda kind=kind: kind in {post["kind"] for post in posts()})
                api["records"][0]["status"] = "completed"
                await _wait_for(pilot, lambda: not screen.query_one("#evaluate_replay").disabled)
            assert len({post["request_id"] for post in posts()}) == 2

    asyncio.run(scenario())


def test_runs_and_results_show_kind_trigger_evaluation_splits_and_the_unverified_summary() -> None:
    from textual.widgets import DataTable, TabbedContent

    evaluation = {
        "kind": "replay",
        "program": {"digest": "a" * 64, "version": "optimize-abc"},
        "splits": {
            "train": {"n": 4, "mean": 0.9, "errors": 0, "critical": {"passed": 0, "n": 0}},
            "holdout": {"n": 3, "mean": 0.667, "errors": 1, "critical": {"passed": 1, "n": 2}},
        },
        "headline": {"holdout": 0.667, "critical_pass_rate": 0.5},
    }
    evaluated = _experiment_record("evaluate-replay-1")
    evaluated.update(kind="evaluate_replay", trigger="manual", gate=None, candidate_version=None, evaluation=evaluation)
    auto = _experiment_record("optimize-auto-1")
    auto.update(kind="optimize", trigger="auto")
    transport, api = _experiment_transport(
        [evaluated, auto], comparison={"manifest": {"evaluation": {}}, "rationale": "Holdout rose a little."}
    )
    api["history"] = {"versions": [], "evaluations": [evaluated]}

    async def scenario() -> None:
        async with _running_tui(transport, "sess_eval_results", (120, 40)) as (_client, tui, pilot):
            tui._open_improve()
            await pilot.pause()
            screen = tui.screen
            await _wait_for(pilot, lambda: screen.records and screen.evaluations)
            screen.query_one("#improve-tabs", TabbedContent).active = "runs"
            await pilot.pause(0.1)
            table = screen.query_one("#runs-table", DataTable)
            assert [table.get_row_at(i)[1:3] for i in range(table.row_count)] == [
                ["evaluate_replay", "manual"],
                ["optimize", "auto"],
            ]
            screen.query_one("#improve-tabs", TabbedContent).active = "results"
            await pilot.pause(0.1)
            text = str(screen.query_one("#results-text").render())
            assert "EVALUATED · HOLDOUT  optimize-abc 0.667" in text
            assert "Program  optimize-abc (aaaaaaaaaaaa)" in text
            assert "HEADLINE  holdout 0.667 · critical pass rate 50%" in text
            assert "holdout    mean 0.667 over 3 examples · critical 1/2 · 1 errors" in text
            table.move_cursor(row=1)
            await _wait_for(pilot, lambda: screen.selected_run_id == "optimize-auto-1")
            await _wait_for(pilot, lambda: screen.comparison_run_id == "optimize-auto-1")
            await pilot.pause(0.1)
            assert "not evidence" in str(screen.query_one("#results-text").render())
            assert "Holdout rose a little." in str(screen.query_one("#results-text").render())

    asyncio.run(scenario())


def test_readiness_tab_shows_auto_run_state_and_saves_edited_settings() -> None:
    from textual.widgets import Checkbox, Input, TabbedContent

    transport, api = _experiment_transport([])
    api["settings"] = {"auto_run": False, "min_new_labels": 5, "max_metric_calls": None, "max_total_tokens": 0}
    api["readiness"] = {
        "ready": True,
        "examples": 8,
        "groups": 4,
        "critical": 1,
        "split_sizes": {},
        "missing": [],
        "new_labels_since_last_run": 0,
    }

    async def scenario() -> None:
        async with _running_tui(transport, "sess_settings", (110, 40)) as (_client, tui, pilot):
            tui._open_improve()
            await pilot.pause()
            screen = tui.screen
            screen.query_one("#improve-tabs", TabbedContent).active = "readiness"
            await _wait_for(pilot, lambda: screen.improve_settings and screen.readiness)
            await pilot.pause(0.1)
            text = str(screen.query_one("#readiness-text").render())
            assert "AUTO-RUN  off · after 5 new labels · light budget · token cap unlimited" in text
            assert "nothing installs without your approval" in text
            assert screen.query_one("#auto-min-labels", Input).value == "5"
            screen.query_one("#settings-save").press()
            await _wait_for(pilot, lambda: api.get("saved_settings"))
            await pilot.pause(0.1)
            assert api["saved_settings"] == [{}]
            api["saved_settings"].clear()
            screen.query_one("#auto-run", Checkbox).value = True
            screen.query_one("#auto-min-labels", Input).value = "3"
            screen.query_one("#auto-metric-calls", Input).value = "40"
            screen.query_one("#auto-tokens", Input).value = ""
            screen.query_one("#settings-save").press()
            await _wait_for(pilot, lambda: api.get("saved_settings"))
            assert api["saved_settings"] == [
                {"auto_run": True, "min_new_labels": 3, "max_metric_calls": 40, "max_total_tokens": None}
            ]
            await _wait_for(
                pilot,
                lambda: (
                    "AUTO-RUN  ON · after 3 new labels · 40 metric calls"
                    in str(screen.query_one("#readiness-text").render())
                ),
            )
            screen.query_one("#auto-min-labels", Input).value = "many"
            screen.query_one("#settings-save").press()
            await pilot.pause(0.2)
            assert len(api["saved_settings"]) == 1

    asyncio.run(scenario())


def _turn_transport(replay_selected: bool):
    event = {
        "event": "turn_complete",
        "turn_id": "turn-9",
        "route": "flex",
        "answer": "Total was 1250.50 over 17 days.",
        "trace_id": "tr-9",
        "replay_snapshot_selected": replay_selected,
    }
    transport, api = _experiment_transport([])
    base = transport.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/turn"):
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=f"data: {json.dumps(event)}\n\n"
            )
        return base(request)

    return httpx.MockTransport(handler), api


def test_ctrl_l_labels_the_offered_turn_and_ctrl_n_opens_review() -> None:
    transport, api = _turn_transport(True)

    async def scenario() -> None:
        async with _running_tui(transport, "sess_offer", (110, 40)) as (_client, tui, pilot):
            tui.submit_text("total?")
            await _wait_for(pilot, lambda: tui._label_offer is not None)
            hints = str(tui.query_one("#composer-hints").render())
            assert "label:" in hints and "ctrl+l" in hints and "ctrl+n" in hints
            await pilot.press("ctrl+l")
            await _wait_for(pilot, lambda: api.get("labels"))
            assert api["labels"] == [
                {
                    "turn_id": "turn-9",
                    "status": "approved",
                    "expected_values": ["1250.50", "17"],
                }
            ]
            assert tui._label_offer is None
            assert "ctrl+l" not in str(tui.query_one("#composer-hints").render())

            # Typing anything withdraws a later offer; ctrl+n opens Review at the offered Turn.
            tui.submit_text("again?")
            await _wait_for(pilot, lambda: tui._label_offer is not None)
            tui.query_one("#prompt-input", app._PromptInput).insert("x")
            await pilot.pause(0.05)
            assert tui._label_offer is None
            tui.query_one("#prompt-input", app._PromptInput).clear()
            tui.submit_text("third?")
            await _wait_for(pilot, lambda: tui._label_offer is not None)
            await pilot.press("ctrl+n")
            await pilot.pause(0.1)
            assert isinstance(tui.screen, app.ImproveScreen)
            assert tui.screen.selected_turn_id == "turn-9"
            assert tui.screen.query_one("#improve-tabs").active == "review"

    asyncio.run(scenario())


def test_no_label_offer_without_a_replay_snapshot() -> None:
    transport, _api = _turn_transport(False)

    async def scenario() -> None:
        async with _running_tui(transport, "sess_no_offer", (110, 40)) as (_client, tui, pilot):
            tui.submit_text("total?")
            await _wait_for(pilot, lambda: tui.state.last_tracker and tui.state.last_tracker.terminal_event)
            await pilot.pause(0.1)
            assert tui._label_offer is None
            assert "label:" not in str(tui.query_one("#composer-hints").render())

    asyncio.run(scenario())


def test_status_bar_badge_follows_readiness() -> None:
    transport, api = _experiment_transport([])
    api["readiness"] = {"ready": True, "examples": 8, "groups": 3, "critical": 1, "split_sizes": {}, "missing": []}

    async def scenario() -> None:
        async with _running_tui(transport, "sess_badge", (110, 40)) as (_client, tui, pilot):
            await _wait_for(pilot, lambda: tui._readiness_ready)
            assert "ready to optimize" in str(tui.query_one("#status-left").render())
            api["readiness"] = {**api["readiness"], "ready": False}
            await tui._refresh_readiness()
            assert "ready to optimize" not in str(tui.query_one("#status-left").render())

    asyncio.run(scenario())


def test_validation_progress_readiness_sections_and_review_flags_render() -> None:
    from textual.widgets import DataTable, TabbedContent

    running = _experiment_record("sess_val-run", "running")
    running.update(
        stage="validation",
        progress={"metric_calls_used": 10, "metric_calls_remaining": 6, "validation_done": 14, "validation_total": 36},
    )
    transport, api = _experiment_transport([running])
    api["queue"] = {
        "items": [
            {**_QUEUE_ITEM, "turn_id": "turn-flag", "flags": ["2 sandbox errors"], "evaluation_score": None},
            {**_QUEUE_ITEM, "turn_id": "turn-calm", "flags": []},
        ]
    }
    api["readiness"] = {
        "ready": False,
        "examples": 8,
        "groups": 4,
        "critical": 1,
        "split_sizes": {"train": 4, "dev": 2, "holdout": 2},
        "missing": ["dev would have 2 examples; need 5 — label more Turns from more Sessions"],
        "min_split_examples": 5,
        "new_labels_since_last_run": 0,
        "components": [{"name": "routing", "description": "Chooses direct, flex, or rlm for a task."}],
        "improvable_examples": 0,
        "note": "All labelled examples already pass; optimizing now is unlikely to change anything.",
    }

    async def scenario() -> None:
        async with _running_tui(transport, "sess_val", (120, 40)) as (_client, tui, pilot):
            tui._open_improve()
            await pilot.pause()
            screen = tui.screen
            await _wait_for(pilot, lambda: screen.queue and screen.readiness and screen.records)
            await pilot.pause(0.1)
            assert "Validation · 14/36 rollouts" in str(screen.query_one("#improve-progress").render())
            assert "flagged Turns teach GEPA the most" in str(screen.query_one("#review-legend").render())
            queue = screen.query_one("#review-queue", DataTable)
            assert queue.get_row_at(0)[0].startswith("⚑") and not queue.get_row_at(1)[0].startswith("⚑")
            assert "2 sandbox errors" in str(screen.query_one("#review-text").render())
            screen.query_one("#improve-tabs", TabbedContent).active = "runs"
            await pilot.pause(0.1)
            assert "14/36" in str(screen.query_one("#runs-detail").render())
            screen.query_one("#improve-tabs", TabbedContent).active = "readiness"
            await pilot.pause(0.1)
            text = str(screen.query_one("#readiness-text").render())
            for expected in (
                "need 5",
                "Each of dev and holdout needs at least 5 examples",
                "WHAT CAN IMPROVE",
                "routing: Chooses direct, flex, or rlm",
                "scoring below 1.0 in the latest evaluation: 0",
                "already pass",
            ):
                assert expected in text, text

    asyncio.run(scenario())


def test_results_open_with_the_outcome_headline_then_the_unverified_summary() -> None:
    from textual.widgets import TabbedContent

    comparison = {
        "manifest": {
            "evaluation": {
                "baseline": {"dev_scores": [0.5, 0.5], "holdout_scores": [1.0]},
                "candidate": {
                    "changed_components": ["routing.predict"],
                    "components_total": 8,
                    "dev_scores": [1.0, 0.5],
                    "holdout_scores": [1.0],
                },
            }
        },
        "rationale": "Routing changed and one dev example improved.",
    }
    transport, _api = _experiment_transport([_experiment_record("sess_head-run")], comparison=comparison)

    async def scenario() -> None:
        async with _running_tui(transport, "sess_head", (120, 40)) as (_client, tui, pilot):
            tui._open_improve()
            await pilot.pause()
            screen = tui.screen
            screen.query_one("#improve-tabs", TabbedContent).active = "results"
            await _wait_for(pilot, lambda: screen.comparison)
            await pilot.pause(0.1)
            text = str(screen.query_one("#results-text").render())
            headline = text.index("OUTCOME  Changed 1 of 8: routing.predict")
            summary = text.index("not evidence")
            assert "1 improved, 0 regressed of 2" in text and headline < summary < text.index("RUN  sess_head-run")

    asyncio.run(scenario())


def test_rejecting_unchanged_corrected_gold_remains_rejected():
    from types import SimpleNamespace

    from fleet_rlm.tui.improve import ImproveScreen

    widgets = {"#review-answer": SimpleNamespace(value="Correct answer 42"), "#review-extra": SimpleNamespace(value="")}
    form = SimpleNamespace(query_one=lambda selector, *_args: widgets[selector], _prefilled_answer="Correct answer 42")
    assert ImproveScreen._label_body(form, {"turn_id": "t", "answer": "Wrong answer 41"}, approve=False) == {
        "turn_id": "t",
        "status": "rejected",
    }


def test_reject_of_a_previously_corrected_turn_stays_a_rejection_and_queue_failure_is_visible() -> None:
    corrected = {
        **_QUEUE_ITEM,
        "label": "approved",
        "expected_answer": "A corrected answer of 42.",
        "expected_values": ["42"],
    }
    transport, api = _experiment_transport([_experiment_record("legacy-run")])
    api["records"][0]["legacy_session_id"] = "sess_old"
    api["queue"] = {"items": [corrected]}

    async def scenario() -> None:
        async with _running_tui(transport, "sess_reject", (110, 40)) as (_client, tui, pilot):
            tui._open_improve()
            await pilot.pause()
            screen = tui.screen
            await _wait_for(pilot, lambda: screen._figure_boxes and screen.records)
            screen.query_one("#label-reject").press()
            await _wait_for(pilot, lambda: api.get("labels"))
            assert api["labels"][0] == {"turn_id": "turn-1", "status": "rejected"}
            # A per-Session job from before jobs were global is never approvable, whatever its evidence.
            screen.comparison, screen.comparison_run_id = {"ok": True}, "legacy-run"
            screen._update_job_actions(screen._selected())
            assert screen.query_one("#job-approve").disabled

    asyncio.run(scenario())

    transport, _api = _experiment_transport([])

    async def unavailable() -> None:
        async with _running_tui(transport, "sess_noq", (110, 40)) as (_client, tui, pilot):
            tui._open_improve()
            await pilot.pause()
            await _wait_for(pilot, lambda: tui.screen.queue_error)
            await pilot.pause(0.1)
            assert "unavailable" in str(tui.screen.query_one("#review-text").render())

    asyncio.run(unavailable())


# Menus and file mentions


async def _wait_until_idle(tui, pilot):
    for _ in range(100):
        if not tui._turn_active:
            return
        await pilot.pause(0.01)
    pytest.fail("Turn worker did not finish")


@asynccontextmanager
async def _menu_test(app, size=(80, 24)):
    async with app.run_test(size=size) as pilot:
        yield pilot, app.query_one("#prompt-input", _PromptInput)


@pytest.fixture
def menu_app(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Unexpected HTTP request: {request.method} {request.url.path}")

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(unexpected_request)) as client:
        yield FleetTextualApp(client, "menu-test", SessionState())


@pytest.mark.asyncio
async def test_history_preserves_drafts_and_does_not_reopen_command_suggestions(menu_app):
    async with _menu_test(menu_app, size=(100, 30)) as (pilot, composer):
        composer.value = "/timeout off"
        await pilot.press("enter")
        composer.value = "/timeout 120"
        await pilot.press("enter")
        composer.value = "keep this unsent draft"

        await pilot.press("down")
        assert composer.value == "keep this unsent draft"

        await pilot.press("up")
        assert composer.value == "/timeout 120"
        assert not menu_app.query_one("#slash-popup").display
        await pilot.press("up")
        assert composer.value == "/timeout off"
        assert not menu_app.query_one("#slash-popup").display

        await pilot.press("down", "down")
        assert composer.value == "keep this unsent draft"
        assert not menu_app.query_one("#slash-popup").display


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(132, 40), (80, 24)])
async def test_every_command_is_reachable_and_the_highlight_stays_visible(menu_app, size):
    from textual.widgets import OptionList

    async with _menu_test(menu_app, size=size) as (pilot, composer):
        composer.value = "/"
        await pilot.pause()
        popup = menu_app.query_one("#slash-popup", OptionList)
        assert popup.option_count == len(COMMANDS) + 3  # /help and both exit aliases

        await pilot.press("up")
        await pilot.pause()
        assert popup.highlighted == popup.option_count - 1
        assert popup.scroll_y > 0
        assert "/quit" in menu_app.export_screenshot()
        await pilot.press("tab")
        assert composer.value == "/quit"
        assert menu_app.is_running  # accepting a completion never runs it


@pytest.mark.asyncio
async def test_enter_accepts_incomplete_suggestions_before_executing_the_command(menu_app):
    async with _menu_test(menu_app, size=(100, 30)) as (pilot, composer):
        composer.value = "/ti"
        await pilot.press("enter")
        assert composer.value == "/timeout "
        assert menu_app.state.timeout_seconds is None
        await pilot.press("enter")
        assert composer.value == "/timeout off"
        assert menu_app.state.timeout_seconds is None
        await pilot.press("enter")
        assert composer.value == ""
        assert menu_app.state.timeout_seconds == 0

        composer.value = "/r"
        await pilot.press("enter")
        assert composer.value == "/rm "  # no DELETE request while selecting a command


@pytest.mark.asyncio
async def test_mouse_selection_fills_the_composer_without_running_the_command(menu_app):
    async with _menu_test(menu_app, size=(100, 30)) as (pilot, composer):
        composer.value = "/ti"
        await pilot.pause()
        await pilot.click("#slash-popup", offset=(2, 1))
        assert composer.value == "/timeout "
        assert composer.has_focus
        assert menu_app.state.timeout_seconds is None


@pytest.mark.asyncio
async def test_escape_keeps_the_input_and_explicit_tab_reopens_completion(menu_app):
    async with _menu_test(menu_app, size=(100, 30)) as (pilot, composer):
        composer.value = "/ti"
        await pilot.press("escape")
        await pilot.pause()
        assert composer.value == "/ti"
        assert not menu_app.query_one("#slash-popup").display
        await pilot.press("tab")
        assert composer.value == "/timeout "
        assert menu_app.query_one("#slash-popup").display


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prefix", "choices"),
    [
        ("/mode ", ["direct", "flex", "rlm", "hybrid"]),
        ("/timeout ", ["off", "default"]),
        ("/skills ", ["push", "pull", "require", "clear"]),
        ("/replay ", ["on", "off"]),
        ("/model ", ["root", "sub"]),
        ("/config ", ["mode ", "json"]),
        ("/config mode ", ["direct", "flex", "rlm", "hybrid"]),
        ("/settings ", ["mode ", "json"]),
        ("/settings mode ", ["direct", "flex", "rlm", "hybrid"]),
        ("/context ", ["clear"]),
        ("/optimize ", ["--max-metric-calls", "--approve"]),
    ],
)
async def test_every_advertised_submenu_can_be_navigated_and_completed(menu_app, prefix, choices):
    from textual.widgets import OptionList

    async with _menu_test(menu_app, size=(80, 24)) as (pilot, composer):
        for index, choice in enumerate(choices):
            composer.value = prefix
            await pilot.pause()
            popup = menu_app.query_one("#slash-popup", OptionList)
            assert popup.display
            assert popup.option_count == len(choices)
            await pilot.press(*(["down"] * index), "tab")
            assert composer.value == prefix + choice
            assert composer.has_focus


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/attach", "/put"])
async def test_local_path_menu_can_descend_into_a_directory(menu_app, tmp_path, command):
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "revenue.csv").write_text("amount\n42\n")
    async with _menu_test(menu_app, size=(80, 24)) as (pilot, composer):
        composer.value = command + " fol"
        await pilot.press("tab")
        assert composer.value == command + " folder/"
        assert menu_app.query_one("#slash-popup").display
        await pilot.press("tab")
        assert composer.value == command + " folder/revenue.csv"


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/cat", "/stat", "/get", "/rm", "/context"])
async def test_workspace_file_menus_and_mentions_include_all_cached_paths(menu_app, command):
    menu_app.state.workspace_files = [f"files/input-{index:02}.csv" for index in range(12)]
    async with _menu_test(menu_app, size=(80, 24)) as (pilot, composer):
        composer.value = command + " files/"
        await pilot.press("up", "tab")
        assert composer.value == command + " files/input-11.csv"
        composer.value = "Summarize @files/"
        await pilot.press("up", "tab")
        assert composer.value == "Summarize @files/input-11.csv "


@pytest.mark.asyncio
async def test_transfer_menu_quotes_paths_with_spaces_and_the_commands_execute(monkeypatch, tmp_path):
    import base64
    import json

    monkeypatch.chdir(tmp_path)
    source = tmp_path / "quarter report.csv"
    source.write_bytes(b"amount\n42\n")
    remote: dict[str, bytes] = {}

    def transfer(request: httpx.Request) -> httpx.Response:
        key = request.url.path.rsplit("/workspace/files/", 1)[1]
        if request.method == "PUT":
            remote[key] = base64.b64decode(json.loads(request.content)["content_base64"])
            return httpx.Response(200, json={"path": key})
        assert request.method == "GET"
        return httpx.Response(200, content=remote[key])

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(transfer)) as client:
        tui = FleetTextualApp(client, "menu-transfer", SessionState())
        async with _menu_test(tui, size=(80, 24)) as (pilot, composer):
            composer.value = "/put qua"
            await pilot.press("tab")
            assert composer.value == "/put 'quarter report.csv'"
            await pilot.press("enter")
            assert remote == {"files/quarter report.csv": b"amount\n42\n"}

            source.unlink()
            composer.value = "/get files/qua"
            await pilot.press("tab")
            assert composer.value == "/get 'files/quarter report.csv'"
            await pilot.press("enter")
            assert source.read_bytes() == b"amount\n42\n"


@pytest.mark.asyncio
async def test_all_commands_wrap_in_both_directions_and_page_keys_scroll(menu_app):
    from textual.widgets import OptionList

    async with _menu_test(menu_app, size=(80, 24)) as (pilot, composer):
        composer.value = "/"
        await pilot.pause()
        popup = menu_app.query_one("#slash-popup", OptionList)
        visited = []
        for _ in range(popup.option_count):
            option = popup.get_option_at_index(popup.highlighted)
            visited.append(option.prompt.plain.split()[0])
            await pilot.press("down")
        assert visited == [*COMMANDS, "/help", "/exit", "/quit"]
        assert popup.highlighted == 0
        await pilot.press("up", "down", "down", "up")
        assert popup.highlighted == 0
        await pilot.press("pagedown")
        assert popup.highlighted > 0
        assert popup.scroll_y > 0
        await pilot.press("pageup")
        assert popup.highlighted == 0
        assert composer.has_focus


@pytest.mark.asyncio
async def test_multiline_editing_and_editing_recalled_history_preserve_the_new_draft(menu_app):
    async with _menu_test(menu_app, size=(100, 30)) as (pilot, composer):
        composer.value = "/timeout off"
        await pilot.press("enter")
        composer.value = "first line\nsecond line\nthird line"
        await pilot.press("up")
        assert composer.cursor_location[0] == 1
        assert composer.value == "first line\nsecond line\nthird line"
        await pilot.press("up", "up")
        assert composer.value == "/timeout off"
        await pilot.press("down")
        assert composer.value == "first line\nsecond line\nthird line"

        await pilot.press("up", "up", "up")
        assert composer.value == "/timeout off"
        await pilot.press("backspace")
        assert composer.value == "/timeout of"
        await pilot.press("escape", "down")
        assert composer.value == "/timeout of"
        await pilot.press("up", "down")
        assert composer.value == "/timeout of"


@pytest.mark.asyncio
async def test_nested_mode_selection_dispatches_only_after_the_completed_command_is_submitted(monkeypatch, tmp_path):
    import json

    monkeypatch.chdir(tmp_path)
    submitted: list[dict] = []

    def mode(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT" and request.url.path == "/sessions/menu-mode/mode"
        submitted.append(json.loads(request.content))
        return httpx.Response(200, json={"mode": submitted[-1]["mode"]})

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(mode)) as client:
        tui = FleetTextualApp(client, "menu-mode", SessionState())
        async with _menu_test(tui, size=(80, 24)) as (pilot, composer):
            composer.value = "/config m"
            await pilot.press("enter")
            assert composer.value == "/config mode "
            await pilot.press("down", "enter")
            assert composer.value == "/config mode flex"
            assert submitted == []
            await pilot.press("enter")
            assert submitted == [{"mode": "flex"}]
            assert tui.state.mode == "flex"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prefix", "burst", "expected"),
    [
        ("/", [("down", None), ("down", None), ("tab", None)], "/artifacts"),
        ("", [("slash", "/"), ("m", "m"), ("o", "o"), ("down", None), ("tab", None)], "/models "),
        ("/mo", [("down", None), ("escape", None)], "/mo"),
    ],
)
async def test_terminal_key_bursts_preserve_input_and_completion_order(menu_app, prefix, burst, expected):
    from textual import events

    async with _menu_test(menu_app, size=(80, 24)) as (pilot, composer):
        composer.value = "/timeout 120"
        await pilot.press("enter")
        composer.value = prefix
        await pilot.pause()
        # Unlike Pilot.press(), a terminal can deliver multiple keys without waiting for
        # the screen to settle after each one. Submit the same public key events as a burst.
        for key, character in burst:
            menu_app.post_message(events.Key(key, character))
        await pilot.pause()
        assert composer.value == expected
        if burst[-1][0] == "escape":
            assert not menu_app.query_one("#slash-popup").display


@pytest.mark.asyncio
async def test_local_mentions_browse_folders_and_quote_files_without_uploading(menu_app, tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    (folder / "quarter report.csv").write_text("amount\n42\n")
    async with _menu_test(menu_app, size=(80, 24)) as (pilot, composer):
        composer.value = "Summarize @"
        await pilot.pause()
        assert menu_app.query_one("#slash-popup").display
        assert any(usage == "@./reports/" for _fill, usage, _description in menu_app._popup_items)
        composer.value = "Summarize @rep"
        await pilot.press("down", "tab")
        assert composer.value == "Summarize @./reports/"
        assert menu_app.query_one("#slash-popup").display
        await pilot.press("tab")
        assert composer.value == 'Summarize @"./reports/quarter report.csv" '
        assert not menu_app.query_one("#slash-popup").display


@pytest.mark.asyncio
async def test_local_mentions_keep_typing_inside_quoted_folder_paths(menu_app, tmp_path):
    folder = tmp_path / "my reports"
    folder.mkdir()
    (folder / 'quarter "one".csv').write_text("amount\n42\n")
    async with _menu_test(menu_app, size=(80, 24)) as (pilot, composer):
        composer.value = "Read @my"
        await pilot.press("down", "tab")
        assert composer.value == 'Read @"./my reports/"'
        await pilot.press("q")
        assert composer.value == 'Read @"./my reports/q"'
        assert menu_app.query_one("#slash-popup").display
        await pilot.press("tab")
        assert composer.value == 'Read @"./my reports/quarter \\"one\\".csv" '


@pytest.mark.asyncio
async def test_local_mentions_complete_at_the_cursor_and_ignore_email_addresses(menu_app, tmp_path):
    (tmp_path / "report.csv").write_text("amount\n42\n")
    async with _menu_test(menu_app, size=(80, 24)) as (pilot, composer):
        composer.value = "Read @rep and summarize it"
        composer.move_cursor((0, len("Read @rep")))
        await pilot.pause()
        assert menu_app.query_one("#slash-popup").display
        await pilot.press("tab")
        assert composer.value == "Read @./report.csv and summarize it"
        assert composer.cursor_location == (0, len("Read @./report.csv"))
        composer.value = "Contact user@rep"
        await pilot.pause()
        assert not menu_app.query_one("#slash-popup").display
        composer.value = 'Read @"./rep'
        await pilot.press("tab")
        assert composer.value == "Read @./report.csv "


@pytest.mark.asyncio
async def test_mention_menu_tracks_cursor_movement_without_editing(menu_app, tmp_path):
    (tmp_path / "report.csv").write_text("amount\n42\n")
    async with _menu_test(menu_app, size=(80, 24)) as (pilot, composer):
        composer.value = "Read @rep and summarize"
        await pilot.pause()
        popup = menu_app.query_one("#slash-popup")
        assert not popup.display
        composer.move_cursor((0, len("Read @rep")))
        await pilot.pause()
        assert popup.display
        assert menu_app._popup_items[0][1] == "@./report.csv"
        composer.move_cursor(composer.document.end)
        await pilot.pause()
        assert not popup.display
        assert composer.value == "Read @rep and summarize"
        assert composer.has_focus


@pytest.mark.asyncio
@pytest.mark.parametrize("style", ["relative", "absolute", "home"])
async def test_local_mentions_support_typed_paths_and_scroll_all_files(menu_app, monkeypatch, tmp_path, style):
    folder = tmp_path / "reports"
    folder.mkdir()
    for index in range(12):
        (folder / f"input-{index:02}.csv").write_text("amount\n42\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    prefix = {"relative": "reports/", "absolute": f"{folder}/", "home": "~/reports/"}[style]
    completed_prefix = "./reports/" if style == "relative" else prefix
    async with _menu_test(menu_app, size=(80, 24)) as (pilot, composer):
        composer.value = f"Read @{prefix}"
        await pilot.pause()
        assert menu_app.query_one("#slash-popup").option_count == 12
        await pilot.press("up", "tab")
        assert composer.value == f"Read @{completed_prefix}input-11.csv "


@pytest.mark.asyncio
async def test_workspace_and_local_mentions_with_the_same_path_stay_distinct(menu_app, tmp_path):
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "report.csv").write_text("amount\n42\n")
    menu_app.state.workspace_files = ["files/report.csv"]
    async with _menu_test(menu_app, size=(80, 24)) as (pilot, composer):
        composer.value = "Read @files/rep"
        await pilot.pause()
        assert [usage for _fill, usage, _description in menu_app._popup_items] == [
            "@files/report.csv",
            "@./files/report.csv",
        ]
        await pilot.press("down", "tab")
        assert composer.value == "Read @./files/report.csv "


@pytest.mark.asyncio
async def test_mentions_upload_on_submit_deduplicate_and_rewrite_before_the_turn(monkeypatch, tmp_path):
    import json
    import re

    monkeypatch.chdir(tmp_path)
    (tmp_path / "quarter report.csv").write_bytes(b"first file")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "quarter report.csv").write_bytes(b"second file")
    uploads = []
    tasks = []

    def respond(request):
        if request.url.path.endswith("/attach"):
            uploads.append(request.content)
            return httpx.Response(200, json={"rel_path": f"files/upload-{len(uploads)}.csv"})
        assert request.url.path.endswith("/turn")
        tasks.append(json.loads(request.content)["task"])
        return httpx.Response(200, text='data: {"event":"done"}\n\n')

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        tui = FleetTextualApp(client, "mentions", SessionState(workspace_files=["files/existing.csv"]))
        async with _menu_test(tui, size=(80, 24)) as (pilot, composer):
            composer.value = (
                'Compare @"./quarter report.csv" @"./other/quarter report.csv" '
                '@"./quarter report.csv" @files/existing.csv'
            )
            await pilot.press("enter")
            await _wait_until_idle(tui, pilot)
            assert len(uploads) == 2
            assert b"first file" in uploads[0] and b"second file" in uploads[1]
            names = [re.search(rb'filename="([^"]+)"', upload).group(1) for upload in uploads]
            assert names[0] != names[1]
            assert tasks == ["Compare @files/upload-1.csv @files/upload-2.csv @files/upload-1.csv @files/existing.csv"]
            assert tui.state.workspace_files == ["files/existing.csv", "files/upload-1.csv", "files/upload-2.csv"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "directory", "http", "unclosed_quote"])
async def test_attachment_failures_keep_the_draft_and_never_start_a_turn(monkeypatch, tmp_path, failure):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "report.csv").write_text("amount\n42\n")
    (tmp_path / "folder").mkdir()
    requested = []

    def respond(request):
        if request.url.path == "/improve/readiness":
            return httpx.Response(200, json={"ready": False})
        requested.append(request.url.path)
        assert request.url.path.endswith("/attach")
        return httpx.Response(413, json={"detail": "Attachment exceeds the configured size limit"})

    text = {
        "missing": "Read @./missing.csv",
        "directory": "Read @./folder/",
        "http": "Read @./report.csv",
        "unclosed_quote": 'Read @"./report.csv',
    }[failure]
    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        tui = FleetTextualApp(client, "mention-failure", SessionState())
        async with _menu_test(tui, size=(80, 24)) as (pilot, composer):
            composer.value = text
            await pilot.press("escape", "enter")
            await _wait_until_idle(tui, pilot)
            assert composer.value == text
            assert len(requested) == (1 if failure == "http" else 0)


@pytest.mark.asyncio
async def test_cancellation_during_an_attachment_does_not_send_a_turn(monkeypatch, tmp_path):
    import threading

    monkeypatch.chdir(tmp_path)
    (tmp_path / "report.csv").write_bytes(b"amount\n42\n")
    uploading = threading.Event()
    release = threading.Event()
    requested = []

    def respond(request):
        requested.append(request.url.path)
        assert request.url.path.endswith("/attach")
        uploading.set()
        assert release.wait(5)
        return httpx.Response(200, json={"rel_path": "files/report.csv"})

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        tui = FleetTextualApp(client, "mention-cancel", SessionState())
        async with _menu_test(tui, size=(80, 24)) as (pilot, composer):
            composer.value = "Read @./report.csv"
            try:
                await pilot.press("enter")
                for _ in range(100):
                    if uploading.is_set():
                        break
                    await pilot.pause(0.01)
                assert uploading.is_set()
                await pilot.press("ctrl+c")
                assert tui._cancel_requested
            finally:
                release.set()
            await _wait_until_idle(tui, pilot)
            assert requested == ["/sessions/mention-cancel/attach"]
            assert composer.value == "Read @./report.csv"


@pytest.mark.asyncio
async def test_enter_keeps_exact_workspace_paths_even_when_not_first(menu_app, monkeypatch):
    submitted = []
    monkeypatch.setattr(menu_app, "submit_text", submitted.append)
    menu_app.state.workspace_files = ["files/report-old.csv", "files/report"]
    async with _menu_test(menu_app, size=(80, 24)) as (pilot, composer):
        composer.value = "/cat files/report"
        await pilot.press("enter")
        assert submitted == ["/cat files/report"]


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/put", "/get"])
async def test_enter_keeps_free_form_transfer_destinations(menu_app, monkeypatch, tmp_path, command):
    submitted = []
    monkeypatch.setattr(menu_app, "submit_text", submitted.append)
    menu_app.state.workspace_files = ["files/report-old.csv"]
    (tmp_path / "report-old.csv").write_bytes(b"existing file")
    text = f"{command} source.csv " + ("files/report" if command == "/put" else "report")
    async with _menu_test(menu_app, size=(80, 24)) as (pilot, composer):
        composer.value = text
        await pilot.press("enter")
        assert submitted == [text]


@pytest.fixture
def git_mention_project(monkeypatch, tmp_path):
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("Git is required for Git ignore contract tests")
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, capture_output=True)
    folder = tmp_path / "src" / "fleet_rlm"
    (folder / "nested").mkdir(parents=True)
    (folder / "__init__.py").write_text("root source")
    (folder / "nested" / "settings.py").write_text("nested source")
    (folder / "ignored").mkdir()
    (folder / "ignored" / "secret.py").write_text("ignored source")
    (folder / "tracked.tmp").write_text("tracked but ignored")
    (folder / ".hidden.py").write_text("hidden source")
    (folder / ".private").mkdir()
    (folder / ".private" / "secret.py").write_text("hidden subtree")
    (folder / "nested" / ".gitignore").write_text("private.py\n")
    (folder / "nested" / "private.py").write_text("nested ignored")
    (folder / "alias.py").symlink_to(folder / "__init__.py")
    (folder / "alias-dir").symlink_to(folder / "nested", target_is_directory=True)
    (tmp_path / ".gitignore").write_text("ignored/\n*.tmp\n")
    subprocess.run(["git", "add", "-f", "src/fleet_rlm/tracked.tmp"], check=True, capture_output=True)
    return folder


@pytest.mark.asyncio
@pytest.mark.usefixtures("git_mention_project")
async def test_bare_name_suggests_nested_directories_and_files(menu_app):
    async with _menu_test(menu_app, size=(80, 24)) as (pilot, composer):
        composer.value = "Review @fleet_rlm"
        await pilot.pause()
        assert [usage for _fill, usage, _description in menu_app._popup_items] == [
            "@./src/fleet_rlm",
            "@./src/fleet_rlm/",
        ]
        await pilot.press("tab")
        assert composer.value == "Review @./src/fleet_rlm "
        assert not menu_app.query_one("#slash-popup").display
        composer.value = "Read @settings"
        await pilot.press("tab")
        assert composer.value == "Read @./src/fleet_rlm/nested/settings.py "
        composer.value = "Read @private"
        await pilot.pause()
        assert not menu_app.query_one("#slash-popup").display


@pytest.mark.asyncio
@pytest.mark.usefixtures("git_mention_project")
async def test_local_directory_mentions_complete_during_a_terminal_key_burst(menu_app):
    from textual import events

    async with _menu_test(menu_app) as (pilot, composer):
        for character in "review @fleet_rlm":
            key = {" ": "space", "@": "at", "_": "underscore"}.get(character, character)
            menu_app.post_message(events.Key(key, character))
        menu_app.post_message(events.Key("tab", None))
        await pilot.pause()
        assert composer.value == "review @./src/fleet_rlm "
        assert composer.has_focus
        assert menu_app.state.workspace_files == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("git_mention_project")
@pytest.mark.parametrize("selection", ["tab", "enter", "mouse"])
async def test_selected_directory_uploads_recursively_before_starting_the_turn(selection):
    import base64
    import json

    uploaded = {}
    tasks = []

    def respond(request):
        if request.method == "PUT":
            path = request.url.path.split("/workspace/files/", 1)[1]
            uploaded[path] = base64.b64decode(json.loads(request.content)["content_base64"])
            return httpx.Response(200, json={"path": path})
        assert request.url.path.endswith("/turn")
        tasks.append(json.loads(request.content)["task"])
        return httpx.Response(200, text='data: {"event":"done"}\n\n')

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        tui = FleetTextualApp(client, "directory-mention", SessionState())
        async with _menu_test(tui, size=(80, 24)) as (pilot, composer):
            composer.value = "Review @fleet_rlm"
            await pilot.pause()
            if selection == "mouse":
                await pilot.click("#slash-popup", offset=(2, 1))
            else:
                await pilot.press(selection)
            assert composer.value == "Review @./src/fleet_rlm "
            assert uploaded == {} and tasks == []
            await pilot.press("enter")
            await _wait_until_idle(tui, pilot)
            root = next(iter(uploaded)).split("/fleet_rlm/", 1)[0] + "/fleet_rlm"
            assert uploaded == {f"{root}/__init__.py": b"root source", f"{root}/nested/settings.py": b"nested source"}
            assert tasks == [f"Review @{root}"]
            assert set(tui.state.workspace_files) == set(uploaded)


@pytest.mark.asyncio
@pytest.mark.usefixtures("git_mention_project")
async def test_partial_directory_upload_failure_keeps_draft_and_never_starts_a_turn():
    requested = []

    def respond(request):
        assert request.method == "PUT" and "/workspace/files/" in request.url.path
        requested.append(request.url.path)
        if len(requested) == 2:
            return httpx.Response(413, json={"detail": "Directory file too large"})
        return httpx.Response(200, json={"path": request.url.path.split("/workspace/files/", 1)[1]})

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        tui = FleetTextualApp(client, "directory-failure", SessionState())
        async with _menu_test(tui, size=(80, 24)) as (pilot, composer):
            composer.value = "Review @./src/fleet_rlm"
            await pilot.press("enter")
            await _wait_until_idle(tui, pilot)
            assert composer.value == "Review @./src/fleet_rlm"
            assert len(requested) == 2
            assert len(tui.state.workspace_files) == 1


@pytest.mark.parametrize("mention", ["src/fleet_rlm", "./src/fleet_rlm", "./src/fleet_rlm/"])
@pytest.mark.usefixtures("git_mention_project")
def test_directory_mentions_preserve_structure_and_deduplicate_overlaps(mention):
    from fleet_rlm.tui.commands import Context

    uploaded = []

    def respond(request):
        assert request.method == "PUT"
        uploaded.append(request.url.path.split("/workspace/files/", 1)[1])
        return httpx.Response(200, json={"path": uploaded[-1]})

    text = f"Review @{mention} @./src/fleet_rlm/nested @./src/fleet_rlm/__init__.py @{mention}"
    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        ctx = Context(client, "directory-overlap", SessionState())
        rewritten = mentions.attach_local_mentions(ctx, text, cancelled=lambda: False)
        root = uploaded[0].split("/fleet_rlm/", 1)[0] + "/fleet_rlm"
        assert rewritten == f"Review @{root} @{root}/nested @{root}/__init__.py @{root}"
        assert len(uploaded) == len(set(uploaded)) == 2


@pytest.mark.parametrize("limit", ["files", "bytes"])
@pytest.mark.usefixtures("git_mention_project")
def test_directory_limits_refuse_before_upload_instead_of_truncating(monkeypatch, limit):
    from fleet_rlm.tui.commands import Context

    monkeypatch.setattr(mentions, "_MAX_DIRECTORY_FILES" if limit == "files" else "_MAX_DIRECTORY_BYTES", 1)

    def unexpected(request):
        pytest.fail(f"Unexpected upload: {request.url.path}")

    with (
        httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(unexpected)) as client,
        pytest.raises(ValueError, match="limited to"),
    ):
        mentions.attach_local_mentions(Context(client, "limit"), "Read @src/fleet_rlm", cancelled=lambda: False)


@pytest.mark.usefixtures("git_mention_project")
def test_cancelling_directory_upload_skips_remaining_files():
    from fleet_rlm.tui.commands import Context

    uploaded = []

    def respond(request):
        uploaded.append(request.url.path)
        return httpx.Response(200, json={"path": request.url.path.split("/workspace/files/", 1)[1]})

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        ctx = Context(client, "directory-cancel")
        text = "Read @src/fleet_rlm"
        assert mentions.attach_local_mentions(ctx, text, cancelled=lambda: bool(uploaded)) == text
        assert len(uploaded) == len(ctx.state.workspace_files) == 1


@pytest.mark.usefixtures("git_mention_project")
def test_directory_discovery_respects_gitignore_negations(tmp_path):
    folder = tmp_path / "src" / "fleet_rlm"
    (folder / "keep.tmp").write_text("explicitly unignored")
    (tmp_path / ".gitignore").write_text("ignored/\n*.tmp\n!src/fleet_rlm/keep.tmp\n")
    assert {path.as_posix() for path in local_paths.iter_local_files(folder)} == {
        "__init__.py",
        "nested/settings.py",
        "keep.tmp",
    }


@pytest.mark.usefixtures("git_mention_project")
def test_directory_upload_fails_closed_when_git_cannot_inspect_ignores(monkeypatch):
    from fleet_rlm.tui.commands import Context

    def no_git(*_args, **_kwargs):
        raise FileNotFoundError("git is unavailable")

    def unexpected(request):
        pytest.fail(f"Unexpected upload: {request.url.path}")

    monkeypatch.setattr(local_paths.subprocess, "run", no_git)
    with (
        httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(unexpected)) as client,
        pytest.raises(ValueError, match="Git is required"),
    ):
        mentions.attach_local_mentions(Context(client, "no-git"), "Read @src/fleet_rlm", cancelled=lambda: False)


def test_directory_upload_outside_git_skips_hidden_entries_and_symlinks(monkeypatch, tmp_path):
    import base64
    import json

    from fleet_rlm.tui.commands import Context

    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "source code"
    (folder / "nested").mkdir(parents=True)
    (folder / "main.py").write_text("root")
    (folder / "nested" / "main.py").write_text("nested")
    (folder / ".hidden").write_text("private")
    (folder / "link").symlink_to(folder / "nested", target_is_directory=True)
    uploaded = {}

    def respond(request):
        path = request.url.path.split("/workspace/files/", 1)[1]
        uploaded[path] = base64.b64decode(json.loads(request.content)["content_base64"])
        return httpx.Response(200, json={"path": path})

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        rewritten = mentions.attach_local_mentions(
            Context(client, "no-repo"), 'Read @"./source code"', cancelled=lambda: False
        )
        root = next(iter(uploaded)).rsplit("/main.py", 1)[0]
        assert uploaded == {f"{root}/main.py": b"root", f"{root}/nested/main.py": b"nested"}
        assert rewritten == f'Read @"{root}"'


def test_nested_path_search_reuses_git_index_across_typing_windows(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "settings.py").write_text("content")
    scans = []
    clock = [61.0]

    def git_files(root):
        scans.append(root)
        return [root.joinpath("nested/settings.py").relative_to(root)]

    monkeypatch.setattr(local_paths, "_git_files", git_files)
    monkeypatch.setattr(local_paths.time, "monotonic", lambda: clock[0])
    for seconds in (61.0, 65.0, 90.0, 119.0):
        clock[0] = seconds
        paths = local_paths.search_local_paths("settings")
        assert [path.as_posix() for path in paths] == ["nested/settings.py"]
    assert scans == [tmp_path]
    clock[0] = 120.0
    local_paths.search_local_paths("settings")
    assert scans == [tmp_path, tmp_path]


# Browser terminal security


def test_build_web_app_command_quotes_arguments_and_preserves_options() -> None:
    command = web.build_web_app_command(
        api_url="http://fleet.test/api's-root",
        mlflow_mode="local",
        session_id="browser-session",
        mlflow_port=5100,
        mlflow_experiment="team's runtime",
    )

    assert shlex.split(command) == [
        web.sys.executable,
        "-m",
        "fleet_rlm.tui.web_client",
        "--url",
        "http://fleet.test/api's-root",
        "--mlflow",
        "local",
        "--session",
        "browser-session",
        "--mlflow-port",
        "5100",
        "--mlflow-experiment",
        "team's runtime",
    ]


def test_build_web_app_command_rejects_credential_bearing_api_urls() -> None:
    import pytest

    with pytest.raises(ValueError, match="without credentials"):
        web.build_web_app_command(
            api_url="https://user:secret@fleet.test",
            mlflow_mode="local",
        )


def test_serve_web_client_configures_textual_serve(monkeypatch) -> None:
    captured = {}

    class FakeServer:
        def __init__(self, command, **kwargs):
            captured.update(command=command, **kwargs)

        def serve(self):
            captured["served"] = True

    monkeypatch.setattr(web, "FleetWebServer", FakeServer)

    web.serve_web_client(
        api_url="http://fleet.test",
        mlflow_mode="local",
        host="127.0.0.1",
        port=8080,
        public_url="https://fleet.example.test",
    )

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8080
    assert captured["title"] == "Fleet RLM Lite"
    assert captured["public_url"] == "https://fleet.example.test"
    assert shlex.split(captured["command"])[-2:] == ["--mlflow", "local"]
    assert captured["served"] is True


@pytest.mark.parametrize(
    ("public_url", "origin", "host"),
    [
        (None, "http://127.0.0.1:8080", "127.0.0.1:8080"),
        ("https://fleet.example.test/proxy", "https://FLEET.example.test:443", "fleet.example.test"),
        ("http://fleet.example.test:80", "http://fleet.example.test", "fleet.example.test:80"),
        ("http://[::1]:8080", "http://[::1]:8080", "[::1]:8080"),
    ],
)
def test_terminal_handshake_delegates_only_for_configured_origin(monkeypatch, public_url, origin, host):
    from aiohttp.test_utils import make_mocked_request
    from textual_serve.server import Server

    accepted = []
    response = object()

    async def handle(_self, request):
        accepted.append(request)
        return response

    monkeypatch.setattr(Server, "handle_websocket", handle)
    server = web.FleetWebServer("unused", host="127.0.0.1", port=8080, public_url=public_url)
    request = make_mocked_request("GET", "/ws", headers={"Origin": origin, "Host": host})
    assert asyncio.run(server.handle_websocket(request)) is response
    assert accepted == [request]


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        ("https://attacker.example", "fleet.example.test"),
        (None, "fleet.example.test"),
        ("null", "fleet.example.test"),
        ("https://fleet.example.test/path", "fleet.example.test"),
        ("https://user@fleet.example.test", "fleet.example.test"),
        ("https://fleet.example.test:444", "fleet.example.test"),
        ("http://fleet.example.test", "fleet.example.test"),
        ("https://fleet.example.test.evil", "fleet.example.test"),
        ("https://fleet.example.test", "attacker.example"),
        ("https://attacker.example", "attacker.example"),
        ("https://fleet.example.test", "fleet.example.test:444"),
        ("https://fleet.example.test https://attacker.example", "fleet.example.test"),
        ("https://fleet.example.test:bad", "fleet.example.test"),
        ("https://fleet.example.test?", "fleet.example.test"),
        ("https://fleet.example.test#", "fleet.example.test"),
        ("https://fleet.example.test:", "fleet.example.test"),
    ],
)
def test_terminal_rejection_precedes_websocket_acceptance_and_process_start(monkeypatch, origin, host):
    from aiohttp import web as aiohttp_web
    from aiohttp.test_utils import make_mocked_request
    from textual_serve import server as textual_server

    def forbidden(*_args, **_kwargs):
        pytest.fail("rejected handshake must not accept a socket or construct an AppService")

    monkeypatch.setattr(textual_server.web, "WebSocketResponse", forbidden)
    monkeypatch.setattr(textual_server, "AppService", forbidden)
    server = web.FleetWebServer("unused", public_url="https://fleet.example.test")
    headers = {"Host": host, "X-Forwarded-Host": "fleet.example.test"}
    if origin is not None:
        headers["Origin"] = origin
    request = make_mocked_request("GET", "/ws", headers=headers)
    with pytest.raises(aiohttp_web.HTTPForbidden):
        asyncio.run(server.handle_websocket(request))


@pytest.mark.parametrize("header", ["Origin", "Host"])
def test_terminal_rejects_duplicate_security_headers(header):
    from aiohttp import web as aiohttp_web
    from aiohttp.test_utils import make_mocked_request
    from multidict import CIMultiDict

    headers = CIMultiDict({"Origin": "https://fleet.example.test", "Host": "fleet.example.test"})
    headers.add(header, headers[header])
    server = web.FleetWebServer("unused", public_url="https://fleet.example.test")
    request = make_mocked_request("GET", "/ws", headers=headers)
    with pytest.raises(aiohttp_web.HTTPForbidden):
        asyncio.run(server.handle_websocket(request))


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "fleet.example.test"])
@pytest.mark.parametrize("token", [None, "", "short", "x" * 32 + "\n"])
def test_non_loopback_web_listener_requires_secret(monkeypatch, host, token):
    monkeypatch.delenv("FLEET_WEB_AUTH_TOKEN", raising=False)
    if token is not None:
        monkeypatch.setenv("FLEET_WEB_AUTH_TOKEN", token)
    with pytest.raises(ValueError, match="FLEET_WEB_AUTH_TOKEN"):
        web.FleetWebServer("unused", host=host)


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic " + "s" * 32, "duplicate", "valid"])
def test_non_loopback_websocket_requires_secret_before_start(monkeypatch, authorization):
    from aiohttp import web as aiohttp_web
    from aiohttp.test_utils import make_mocked_request
    from multidict import CIMultiDict
    from textual_serve.server import Server

    token = "s" * 32
    monkeypatch.setenv("FLEET_WEB_AUTH_TOKEN", token)
    accepted = []

    async def handle(_self, request):
        accepted.append(request)
        return "accepted"

    monkeypatch.setattr(Server, "handle_websocket", handle)
    server = web.FleetWebServer("unused", host="0.0.0.0", public_url="https://fleet.example.test")
    headers = CIMultiDict({"Origin": "https://fleet.example.test", "Host": "fleet.example.test"})
    if authorization in {"valid", "duplicate"}:
        headers.add("Authorization", f"Bearer {token}")
        if authorization == "duplicate":
            headers.add("Authorization", f"Bearer {token}")
    elif authorization is not None:
        headers.add("Authorization", authorization)
    request = make_mocked_request("GET", "/ws", headers=headers)
    if authorization == "valid":
        assert asyncio.run(server.handle_websocket(request)) == "accepted"
        assert accepted == [request]
    else:
        with pytest.raises(aiohttp_web.HTTPUnauthorized):
            asyncio.run(server.handle_websocket(request))
        assert accepted == []


def test_authenticated_non_loopback_client_still_requires_matching_origin(monkeypatch):
    from aiohttp import web as aiohttp_web
    from aiohttp.test_utils import make_mocked_request

    monkeypatch.setenv("FLEET_WEB_AUTH_TOKEN", "s" * 32)
    server = web.FleetWebServer("unused", host="0.0.0.0", public_url="https://fleet.example.test")
    request = make_mocked_request(
        "GET",
        "/ws",
        headers={"Authorization": "Bearer " + "s" * 32, "Origin": "https://evil.test", "Host": "fleet.example.test"},
    )
    with pytest.raises(aiohttp_web.HTTPForbidden):
        asyncio.run(server.handle_websocket(request))


@pytest.mark.parametrize(
    ("arguments", "expected_session", "mlflow_mode"),
    [
        (["--mlflow", "databricks"], "generated", "databricks"),
        (["--session", "shared-browser-session"], "shared-browser-session", "local"),
    ],
)
def test_web_client_session_and_mlflow_configuration(monkeypatch, arguments, expected_session, mlflow_mode) -> None:
    settings = FleetSettings(_env_file=None)
    selected = []
    apps = []

    class FakeApp:
        def __init__(self, _client, session_id, state):
            apps.append((session_id, state))

        def run(self):
            pass

    monkeypatch.setattr(web_client, "get_settings", lambda mode: selected.append(mode) or settings)
    monkeypatch.setattr(web_client, "FleetTextualApp", FakeApp)

    web_client.main(["--url", "http://fleet.test", *arguments])

    assert selected == [mlflow_mode]
    session_id, state = apps[0]
    if expected_session == "generated":
        assert session_id.startswith("sess_web_")
    else:
        assert session_id == expected_session
    assert state.mlflow_settings is settings


# Consolidated from lite/test_cli.py


# CLI entry points


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_serve_rejects_invalid_explicit_ports_before_starting_a_server(monkeypatch, capsys, port):
    settings = FleetSettings(_env_file=None)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda *_args, **_kwargs: pytest.fail("invalid settings must not start a server")
    )
    with pytest.raises(SystemExit) as error:
        cli.main(["serve", "--port", str(port)])
    assert error.value.code == 2
    assert "invalid configuration" in capsys.readouterr().err


def test_serve_validates_and_applies_explicit_bind_settings(monkeypatch):
    settings = FleetSettings(_env_file=None)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    captured = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: captured.update(app=app, **kwargs))
    cli.main(["serve", "--host", "localhost", "--port", "12345"])
    assert (captured["host"], captured["port"]) == ("localhost", 12345)
    assert captured["app"].state.settings.configuration()["sources"]["port"] == "explicit"


@pytest.mark.parametrize("backend", ["local", "databricks"])
def test_eval_missing_experiment_diagnostic_names_selected_profile(monkeypatch, backend):
    import mlflow

    from fleet_rlm.config import MlflowTarget

    # Validated settings reject empty names; exercise the defensive boundary with a double.
    settings = SimpleNamespace(
        mlflow_target=lambda: MlflowTarget(backend, "offline", "runtime", "", "opt"),
        mlflow_descriptor=lambda **_kwargs: {},
        mlflow_tracing_sql_warehouse_id="offline",
    )
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(cli.launcher, "verify_api_destination", lambda *_args: None)
    with pytest.raises(SystemExit) as error:
        cli._eval_command(SimpleNamespace(eval_command="run", url="http://test"), settings)
    message = str(error.value)
    assert f"FLEET_MLFLOW_{backend.upper()}_EVAL_EXPERIMENT_NAME" in message
    assert "FLEET_MLFLOW_EVAL_EXPERIMENT_NAME" not in message


@pytest.mark.parametrize("backend", ["local", "databricks"])
def test_eval_rejects_empty_selected_experiment_before_external_work(monkeypatch, capsys, backend):
    monkeypatch.setenv(f"FLEET_MLFLOW_{backend.upper()}_EVAL_EXPERIMENT_NAME", "")
    monkeypatch.setattr(
        cli.launcher, "mlflow_is_healthy", lambda *_args: pytest.fail("invalid settings must not probe MLflow")
    )
    monkeypatch.setattr(cli, "_eval_command", lambda *_args: pytest.fail("invalid settings must not evaluate"))
    with pytest.raises(SystemExit) as error:
        cli.main(["eval", "dataset", "--mlflow", backend])
    assert error.value.code == 2
    assert "invalid configuration" in capsys.readouterr().err


def test_eval_run_passes_destination_and_exits_unsuccessfully_on_incomplete_collection(monkeypatch, capsys):
    import mlflow
    import pytest

    from fleet_rlm.config import MlflowTarget
    from fleet_rlm.optimize import evaluation

    settings = SimpleNamespace(
        mlflow_target=lambda: MlflowTarget("local", "file:///tmp/mlruns", "runtime", "eval", "opt"),
        judge_environment=lambda: {"OPENAI_API_BASE": "https://gateway.example/v1", "OPENAI_API_KEY": "gateway-key"},
        fleet_judge_model="databricks:/judge",
        build_jev_lm=lambda: None,
        build_judge_lm=lambda: None,
        resolved_model=lambda _role: SimpleNamespace(build_adapter=lambda: None),
        mlflow_descriptor=lambda **_kwargs: {},
    )
    monkeypatch.setenv("OPENAI_API_BASE", "https://stale-provider.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "stale-key")
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "set_experiment", lambda _name: SimpleNamespace(experiment_id="eval-id"))
    captured = {}
    monkeypatch.setattr(cli.launcher, "verify_api_destination", lambda *_args: None)

    def evaluate(_cases, **kwargs):
        import os

        assert os.environ["OPENAI_API_BASE"] == "https://gateway.example/v1"
        assert os.environ["OPENAI_API_KEY"] == "gateway-key"
        captured.update(kwargs)
        kwargs["report"]("Case direct-00: trace tr-1")
        raise evaluation.EvaluationError("Expectations are not readable yet; recover trace tr-1 without rerunning")

    monkeypatch.setattr(evaluation, "evaluate_cases", evaluate)
    with pytest.raises(SystemExit, match="recover trace tr-1"):
        cli._eval_command(
            SimpleNamespace(eval_command="run", seed=7, limit=1, url="http://test", collect_only=True),
            settings,
        )
    assert captured["experiment_id"] == "eval-id"
    assert captured["uc_location"] is None
    assert captured["jev_lm"] is None
    assert "Collected labelled Turns" not in capsys.readouterr().out


def test_eval_label_cli_accepts_critical_and_explicit_unpin(monkeypatch) -> None:
    captured = []
    settings = FleetSettings(_env_file=None)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(cli.launcher, "mlflow_is_healthy", lambda _url: True)
    monkeypatch.setattr(cli, "_eval_command", lambda args, _settings: captured.append(args))

    cli.main(["eval", "label", "tr-1", "--value", "42", "--critical"])
    cli.main(["eval", "label", "tr-1", "--value", "42", "--no-critical"])

    assert [args.critical for args in captured] == [True, False]


def test_eval_label_syncs_selected_replay_labels_to_configured_lakebase(monkeypatch) -> None:
    import mlflow

    from fleet_rlm.config import MlflowTarget
    from fleet_rlm.store import Store

    settings = SimpleNamespace(
        mlflow_target=lambda: MlflowTarget("local", "file:///tmp/mlruns", "runtime", "eval", "opt"),
        fleet_database_url="postgresql://example",
        fleet_lakebase_endpoint=None,
        fleet_database_schema="fleet_rlm_lite",
    )
    trace = SimpleNamespace(
        info=SimpleNamespace(trace_metadata={"mlflow.trace.session": "session-1"}),
        data=SimpleNamespace(
            request={
                "replay_snapshot_selected": True,
                "session_id": "session-1",
                "turn_id": "turn-1",
                "replay_snapshot_sha256": "a" * 64,
                "task": "Review this source file",
                "workspace_manifest": "/workspace/files/input.txt",
                "context_files": {"files/input.txt": "b" * 64},
            }
        ),
    )
    trace.search_assessments = lambda **_kwargs: []
    monkeypatch.setattr("fleet_rlm.optimize.evaluation.selected_snapshot", lambda *_args: {"task": "immutable source"})
    calls = {}
    store = SimpleNamespace(
        save_review_labels=lambda *args, **kwargs: calls.update(label_args=args, labels=kwargs),
        close=lambda: calls.update(closed=True),
    )
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "get_trace", lambda *_args, **_kwargs: trace)
    monkeypatch.setattr(Store, "open", lambda *args, **kwargs: calls.update(open=(args, kwargs)) or store)
    monkeypatch.setattr("fleet_rlm.optimize.evaluation.label_trace", lambda *_args, **_kwargs: None)

    cli._eval_command(
        SimpleNamespace(
            eval_command="label",
            trace_id="trace-1",
            group=None,
            status="approved",
            answer="42",
            value=[],
            route="rlm",
            critical=False,
            reviewer="reviewer-1",
        ),
        settings,
    )

    assert calls["open"][1]["schema"] == "fleet_rlm_lite"
    assert calls["label_args"] == ("session-1", "turn-1", "a" * 64)
    assert calls["labels"]["expected_answer"] == "42"
    assert "context_files" not in calls["labels"]
    assert calls["closed"] is True


def test_trace_verify_command_routes_scope_to_the_trace_handler(monkeypatch) -> None:
    captured = []
    settings = FleetSettings(_env_file=None)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(cli.launcher, "mlflow_is_healthy", lambda _url: True)
    monkeypatch.setattr(cli, "_trace_verify_command", lambda args, _settings: captured.append(args))

    cli.main(["trace", "verify", "trace:/catalog.schema/id", "--scope", "optimization"])

    assert captured[0].trace_id == "trace:/catalog.schema/id"
    assert captured[0].scope == "optimization"


def _local_trace_settings():
    from fleet_rlm.config import MlflowTarget

    return SimpleNamespace(
        mlflow_target=lambda: MlflowTarget("local", "file:///tmp/mlruns", "runtime", "eval", "opt"),
        mlflow_trace_schema="runtime_schema",
        mlflow_trace_table_prefix="runtime_prefix",
        mlflow_eval_trace_table_prefix="eval_prefix",
        mlflow_optimization_trace_schema="optimization_schema",
        mlflow_optimization_trace_table_prefix="optimization_prefix",
    )


def test_trace_verify_flushes_and_requires_a_readable_trace(monkeypatch) -> None:
    import mlflow

    settings = _local_trace_settings()
    flushed = []
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "get_experiment_by_name", lambda _name: SimpleNamespace(trace_location=None))
    monkeypatch.setattr(mlflow, "get_trace", lambda trace_id, **kwargs: flushed.append((trace_id, kwargs)) or None)

    import pytest

    with pytest.raises(SystemExit, match="was not found after flushing"):
        cli._trace_verify_command(SimpleNamespace(scope="runtime", trace_id="tr-missing"), settings)

    assert flushed == [("tr-missing", {"silent": True, "flush": True})]


def test_trace_verify_confirms_stored_span_count(monkeypatch, capsys) -> None:
    import mlflow

    settings = _local_trace_settings()
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "get_experiment_by_name", lambda _name: SimpleNamespace(trace_location=None))
    monkeypatch.setattr(
        mlflow,
        "get_trace",
        lambda _trace_id, **_kwargs: SimpleNamespace(data=SimpleNamespace(spans=[object(), object()])),
    )

    cli._trace_verify_command(SimpleNamespace(scope="runtime", trace_id="tr-found"), settings)

    assert "Trace export verified: tr-found (2 spans; file:///tmp/mlruns)" in capsys.readouterr().out


def _databricks_trace_settings():
    from fleet_rlm.config import MlflowTarget

    return SimpleNamespace(
        mlflow_target=lambda: MlflowTarget(
            "databricks", "databricks", "/Shared/runtime", "/Shared/eval", "/Shared/opt"
        ),
        mlflow_trace_catalog="catalog",
        mlflow_trace_schema="runtime_schema",
        mlflow_trace_table_prefix="runtime_prefix",
        mlflow_eval_trace_table_prefix="eval_prefix",
        mlflow_optimization_trace_schema="optimization_schema",
        mlflow_optimization_trace_table_prefix="optimization_prefix",
        mlflow_tracing_sql_warehouse_id="warehouse-id",
    )


def test_trace_verify_requires_the_configured_unity_catalog_destination(monkeypatch) -> None:
    import mlflow
    import pytest
    from mlflow.entities.trace_location import UnityCatalog

    settings = _databricks_trace_settings()
    monkeypatch.setenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID", "prior-value")
    experiment = SimpleNamespace(trace_location=UnityCatalog("catalog", "runtime_schema", "wrong_prefix"))
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "get_experiment_by_name", lambda _name: experiment)
    with pytest.raises(SystemExit, match="trace destination mismatch"):
        cli._trace_verify_command(
            SimpleNamespace(scope="runtime", trace_id="trace:/catalog.runtime_schema/id"), settings
        )


def test_trace_verify_checks_the_trace_itself_is_in_the_configured_destination(monkeypatch) -> None:
    import mlflow
    import pytest
    from mlflow.entities.trace_location import UnityCatalog

    settings = _databricks_trace_settings()
    monkeypatch.setenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID", "prior-value")
    experiment = SimpleNamespace(trace_location=UnityCatalog("catalog", "runtime_schema", "runtime_prefix"))
    trace = SimpleNamespace(
        info=SimpleNamespace(trace_location=UnityCatalog("other", "runtime_schema", "runtime_prefix")),
        data=SimpleNamespace(spans=[object()]),
    )
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "get_experiment_by_name", lambda _name: experiment)
    monkeypatch.setattr(mlflow, "get_trace", lambda _trace_id, **_kwargs: trace)
    with pytest.raises(SystemExit, match="stored outside the configured destination"):
        cli._trace_verify_command(
            SimpleNamespace(scope="runtime", trace_id="trace:/catalog.runtime_schema/id"), settings
        )


def test_trace_verify_accepts_wrapped_unity_catalog_location(monkeypatch, capsys) -> None:
    import mlflow
    from mlflow.entities.trace_location import TraceLocation, TraceLocationType, UnityCatalog

    settings = _databricks_trace_settings()
    location = UnityCatalog("catalog", "runtime_schema", "runtime_prefix")
    experiment = SimpleNamespace(trace_location=location)
    trace = SimpleNamespace(
        info=SimpleNamespace(
            trace_location=TraceLocation(type=TraceLocationType.UC_TABLE_PREFIX, uc_table_prefix=location)
        ),
        data=SimpleNamespace(spans=[object()]),
    )
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "get_experiment_by_name", lambda _name: experiment)
    monkeypatch.setattr(mlflow, "get_trace", lambda _trace_id, **_kwargs: trace)

    cli._trace_verify_command(SimpleNamespace(scope="runtime", trace_id="trace:/catalog.runtime_schema/id"), settings)

    output = " ".join(capsys.readouterr().out.split())
    assert (
        "Trace export verified: trace:/catalog.runtime_schema/id (1 spans; catalog.runtime_schema.runtime_prefix)"
    ) in output


def test_web_cli_serves_textual_client_after_verifying_api_destination(monkeypatch) -> None:
    from contextlib import nullcontext

    settings = FleetSettings(_env_file=None)
    calls = {}
    plan = cli.launcher.LaunchPlan(api_url="http://fleet.test", start_api=False)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(
        cli.launcher,
        "plan_launch",
        lambda **kwargs: calls.update(plan=kwargs) or plan,
    )
    monkeypatch.setattr(cli.launcher, "running_services", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(
        cli.launcher,
        "verify_api_destination",
        lambda *args: calls.update(verified=args),
    )
    monkeypatch.setattr(cli, "serve_web_client", lambda **kwargs: calls.update(web=kwargs))

    cli.main(
        [
            "web",
            "--url",
            "http://fleet.test",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
            "--session",
            "browser-session",
        ]
    )

    assert calls["plan"]["url"] == "http://fleet.test"
    assert calls["verified"][0] == "http://fleet.test"
    assert calls["web"]["host"] == "127.0.0.1"
    assert calls["web"]["port"] == 8765
    assert calls["web"]["session_id"] == "browser-session"


def test_web_starts_owned_services_when_no_external_url_is_given(monkeypatch) -> None:
    from contextlib import nullcontext

    settings = FleetSettings(_env_file=None)
    calls: dict[str, Any] = {}
    plan = cli.launcher.LaunchPlan(
        api_url="http://127.0.0.1:8000",
        start_api=True,
        start_mlflow=True,
        mlflow_url="http://127.0.0.1:5001",
    )
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(cli.launcher, "plan_launch", lambda **_kwargs: plan)

    def running_services(selected_plan, **kwargs):
        calls["serving_plan"] = selected_plan
        calls["services"] = kwargs
        return nullcontext(selected_plan)

    monkeypatch.setattr(cli.launcher, "running_services", running_services)
    monkeypatch.setattr(cli.launcher, "verify_api_destination", lambda *_args: None)
    monkeypatch.setattr(cli, "serve_web_client", lambda **kwargs: calls.update(web=kwargs))

    cli.main(["web", "--host", "127.0.0.1", "--port", "8765"])

    assert calls["serving_plan"] is plan
    assert calls["web"]["api_url"] == "http://127.0.0.1:8000"


def test_managed_optimize_reads_private_settings_before_lazy_optimizer_import(monkeypatch, tmp_path) -> None:
    from fleet_rlm.optimization_runs import worker_settings_payload

    settings = FleetSettings(
        _env_file=None,
        compiled_program_dir=str(tmp_path),
        mlflow_mode="local",
    )
    monkeypatch.setattr(cli.launcher, "mlflow_is_healthy", lambda _url: True)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: pytest.fail("managed worker must use stdin settings"))
    original_reader = cli.read_worker_settings
    events: list[str] = []

    def read_settings(stream):
        events.append("read settings")
        return original_reader(stream)

    monkeypatch.setattr(cli, "read_worker_settings", read_settings)
    monkeypatch.setattr(sys, "stdin", io.StringIO(worker_settings_payload(settings).decode()))
    fake_gepa = types.SimpleNamespace(run=lambda args, parser, selected: events.append(("run", args, parser, selected)))
    fake_optimizer_package = types.ModuleType("fleet_rlm.optimize")
    fake_optimizer_package.gepa = fake_gepa
    monkeypatch.setitem(sys.modules, "fleet_rlm.optimize", fake_optimizer_package)
    real_import = builtins.__import__

    def tracked_import(name, *args, **kwargs):
        fromlist = kwargs.get("fromlist", args[2] if len(args) > 2 else ())
        if name == "fleet_rlm.optimize" and "gepa" in fromlist:
            events.append("import optimizer")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracked_import)

    cli.main(
        [
            "optimize",
            "--managed-run-id",
            "optimize-worker-run",
            "--managed-owner-id",
            "api-owner",
        ]
    )

    assert events[:2] == ["read settings", "import optimizer"]
    run = events[2]
    assert run[0] == "run"
    assert run[1].managed_run_id == "optimize-worker-run"
    assert run[1].managed_owner_id == "api-owner"
    assert run[2].prog == "fleet optimize"
    assert run[3].compiled_program_dir == str(tmp_path)


@pytest.mark.parametrize(
    "allow_public,arguments,message",
    [
        (False, ["--public-url", "https://fleet.example.test"], None),
        (True, [], "public-url is required"),
    ],
)
def test_web_cli_rejects_invalid_public_listener(monkeypatch, capsys, allow_public, arguments, message):
    settings = FleetSettings(_env_file=None, allow_non_loopback_bind=allow_public)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(
        cli.launcher,
        "plan_launch",
        lambda **_kwargs: pytest.fail("invalid public listener must fail before planning services"),
    )

    with pytest.raises(SystemExit) as error:
        cli.main(["web", "--url", "http://fleet.test", "--host", "0.0.0.0", *arguments])

    assert error.value.code == 2
    if message:
        assert message in capsys.readouterr().err


def test_web_cli_requires_authentication_before_starting_services(monkeypatch, capsys):
    settings = FleetSettings(_env_file=None, allow_non_loopback_bind=True)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.delenv("FLEET_WEB_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        cli.launcher, "plan_launch", lambda **_kwargs: pytest.fail("must reject before starting services")
    )
    with pytest.raises(SystemExit) as error:
        cli.main(["web", "--url", "http://fleet.test", "--host", "0.0.0.0", "--public-url", "https://fleet.test"])
    assert error.value.code == 2
    assert "FLEET_WEB_AUTH_TOKEN" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["cli", "web"])
def test_inherited_databricks_port_is_rejected_before_launch(monkeypatch, capsys, command):
    settings = FleetSettings(_env_file=None, mlflow_mode="databricks")
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(cli.launcher, "api_is_healthy", lambda _url: False)
    monkeypatch.setattr(cli.launcher, "plan_launch", lambda **_kwargs: pytest.fail("invalid port must not launch"))
    with pytest.raises(SystemExit) as error:
        cli.main([command, "--mlflow-port", "5001"])
    assert error.value.code == 2
    assert "--mlflow-port is only supported with local MLflow" in capsys.readouterr().err


def test_eval_score_cli_scores_collected_traces_without_running_turns(monkeypatch, capsys) -> None:
    import mlflow

    from fleet_rlm.config import MlflowTarget
    from fleet_rlm.optimize import evaluation

    settings = SimpleNamespace(
        mlflow_target=lambda: MlflowTarget("local", "file:///tmp/mlruns", "runtime", "eval", "opt"),
        judge_environment=lambda: {},
        fleet_judge_model="databricks:/judge",
        build_jev_lm=lambda: "jev",
        build_judge_lm=lambda: "judge",
        resolved_model=lambda _role: SimpleNamespace(build_adapter=lambda: "adapter"),
    )
    traces = {"tr-1": object(), "tr-2": object()}
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "set_experiment", lambda _name: SimpleNamespace(experiment_id="eval-id"))
    monkeypatch.setattr(mlflow, "get_trace", lambda trace_id, **_kw: traces.get(trace_id))
    captured = {}

    def score(received, **kwargs):
        captured.update(kwargs, traces=received)
        return SimpleNamespace(metrics={"quality/mean": 1.0})

    monkeypatch.setattr(evaluation, "score_evaluation_traces", score)
    monkeypatch.setattr(evaluation, "evaluate_cases", lambda *_a, **_k: pytest.fail("scoring must not rerun Turns"))
    cli._eval_command(SimpleNamespace(eval_command="score", trace_ids=["tr-1", "tr-2"]), settings)
    assert captured["traces"] == list(traces.values())
    assert captured["experiment_id"] == "eval-id" and captured["judge_lm"] == "judge" and captured["jev_lm"] == "jev"
    assert "quality/mean" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="tr-missing is not readable"):
        cli._eval_command(SimpleNamespace(eval_command="score", trace_ids=["tr-missing"]), settings)


def test_optimize_cli_takes_no_session_and_offers_check_and_a_token_cap(monkeypatch) -> None:
    from fleet_rlm.optimize import gepa

    captured = []
    settings = FleetSettings(_env_file=None)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(cli.launcher, "mlflow_is_healthy", lambda _url: True)
    monkeypatch.setattr(gepa, "run", lambda args, _parser, _settings: captured.append(args))
    with pytest.raises(SystemExit):
        cli.main(["optimize", "--session", "sess_a"])
    cli.main(["optimize", "--check", "--max-total-tokens", "0"])
    assert [(args.check, args.max_total_tokens) for args in captured] == [(True, 0)]


def test_score_diagnostics_only_skips_unrelated_judges(monkeypatch):
    import mlflow

    from fleet_rlm.optimize import evaluation, scorers

    calls = {}
    trace = object()
    monkeypatch.setattr(mlflow, "get_trace", lambda *_a, **_k: trace)
    monkeypatch.setattr(evaluation, "apply_judge_environment", lambda settings: calls.update(environment=settings))
    monkeypatch.setattr(scorers, "make_execution_evidence_judge", lambda model: calls.update(model=model))
    monkeypatch.setattr(
        evaluation,
        "score_evaluation_traces",
        lambda traces, **kwargs: calls.update(traces=traces, options=kwargs) or SimpleNamespace(metrics={}),
    )
    settings = SimpleNamespace(fleet_judge_model="openai:/configured-judge")
    cli._score_command(SimpleNamespace(trace_ids=["saved"], execution_diagnostics_only=True), settings, "1")
    assert calls["environment"] is settings
    assert calls["traces"] == [trace]
    assert calls["model"] == settings.fleet_judge_model
    assert [s.name for s in calls["options"]["scorers"]] == ["execution_evidence_diagnostics"]
    assert "judge_lm" not in calls["options"]


def test_registered_score_uses_source_registry_and_evaluation_destination(monkeypatch):
    import mlflow

    from fleet_rlm.optimize import evaluation

    calls = {}
    trace, registered = object(), object()
    identities = [{"experiment_id": "1", "name": "diagnostics", "version": 1, "digest": "digest"}]
    monkeypatch.setattr(mlflow, "get_trace", lambda *_a, **_k: trace)
    monkeypatch.setattr(evaluation, "apply_judge_environment", lambda _settings: None)
    monkeypatch.setattr(
        evaluation,
        "load_registered_scorers",
        lambda source, names: (calls.update(source=source, names=names) or [registered], identities),
    )
    monkeypatch.setattr(
        evaluation,
        "score_evaluation_traces",
        lambda traces, **kwargs: calls.update(traces=traces, options=kwargs) or SimpleNamespace(metrics={}),
    )
    cli._score_command(
        SimpleNamespace(trace_ids=["saved"], scorer_experiment_id="1", registered_scorer=["diagnostics"]),
        SimpleNamespace(fleet_judge_model="configured"),
        "49",
    )
    assert calls["source"] == "1"
    assert calls["options"]["experiment_id"] == "49"
    assert calls["options"]["scorers"] == [registered]
    assert calls["options"]["registered_provenance"] == identities
    assert calls["traces"] == [trace]


@pytest.mark.parametrize(
    "options",
    [
        {"scorer_experiment_id": "1"},
        {"registered_scorer": ["diagnostics"]},
        {"scorer_experiment_id": "1", "registered_scorer": ["diagnostics"], "execution_diagnostics_only": True},
    ],
)
def test_registered_score_rejects_incomplete_or_conflicting_selection(monkeypatch, options):
    import mlflow

    from fleet_rlm.optimize import evaluation

    monkeypatch.setattr(mlflow, "get_trace", lambda *_a, **_k: object())
    monkeypatch.setattr(evaluation, "apply_judge_environment", lambda _settings: None)
    monkeypatch.setattr(evaluation, "score_evaluation_traces", lambda *_a, **_k: pytest.fail("must not score"))
    with pytest.raises(SystemExit):
        cli._score_command(SimpleNamespace(trace_ids=["saved"], **options), object(), "49")


# Local service ownership


def _plan(**overrides: Any) -> LaunchPlan:
    options: dict[str, Any] = {
        "url": None,
        "host": "127.0.0.1",
        "port": 8000,
        "settings": FleetSettings(_env_file=None),
        "api_healthy": lambda _url: False,
        "mlflow_healthy": lambda _url: False,
    }
    return plan_launch(**{**options, **overrides})


def test_explicit_url_starts_nothing(monkeypatch) -> None:
    monkeypatch.setattr(launcher, "verify_api_destination", lambda *_args: None)
    plan = _plan(url="http://example.test:9000/")
    assert (plan.api_url, plan.start_api, plan.start_mlflow) == ("http://example.test:9000", False, False)


def test_a_healthy_api_is_reused_and_nothing_is_started(monkeypatch) -> None:
    monkeypatch.setattr(launcher, "verify_api_destination", lambda *_args: None)
    plan = _plan(api_healthy=lambda _url: True)
    assert not plan.start_api and not plan.start_mlflow and plan.api_env == {}
    assert any("Reusing the API server" in note for note in plan.notes)


def test_default_starts_local_mlflow_then_the_api_pointed_at_it() -> None:
    plan = _plan()
    assert plan.start_mlflow and plan.start_api and plan.mlflow_url == "http://127.0.0.1:5001"
    assert plan.api_env["FLEET_MLFLOW_LOCAL_PORT"] == "5001"
    assert plan.api_env["FLEET_MLFLOW_LOCAL_EXPERIMENT_NAME"] == "fleet-rlm-dev"


def test_a_healthy_mlflow_is_reused_but_the_api_still_points_at_it() -> None:
    plan = _plan(mlflow_healthy=lambda _url: True, settings=FleetSettings(_env_file=None, mlflow_local_port=5002))
    assert plan.start_api and not plan.start_mlflow
    assert plan.api_env["FLEET_MLFLOW_LOCAL_PORT"] == "5002"
    assert any("Reusing the MLflow server" in note for note in plan.notes)


def test_databricks_mode_leaves_the_configured_tracking_alone() -> None:
    plan = _plan(settings=FleetSettings(_env_file=None, mlflow_mode="databricks"))
    assert (
        plan.start_api
        and not plan.start_mlflow
        and plan.api_env["FLEET_MLFLOW_DATABRICKS_TRACKING_URI"] == "databricks"
        and plan.mlflow_url is None
    )


def test_wait_ready_reports_a_process_that_exits_with_its_log(tmp_path: Path) -> None:
    process = ManagedProcess(
        "Demo", [sys.executable, "-c", "print('boom: bad config'); raise SystemExit(3)"], tmp_path / "demo.log"
    )
    process.start()
    with pytest.raises(LaunchError, match=r"exited during startup \(code 3\)[\s\S]*boom: bad config"):
        process.wait_ready(lambda: False, timeout=10, interval=0.05)


def test_wait_ready_times_out_and_stop_terminates_the_process(tmp_path: Path) -> None:
    process = ManagedProcess("Demo", [sys.executable, "-c", "import time; time.sleep(60)"], tmp_path / "demo.log")
    process.start()
    with pytest.raises(LaunchError, match="not ready after"):
        process.wait_ready(lambda: False, timeout=0.3, interval=0.05)
    assert process._process is not None and process._process.poll() is None
    process.stop(grace=5)
    assert process._process is not None and process._process.poll() is not None


def test_wait_ready_returns_once_the_check_passes(tmp_path: Path) -> None:
    process = ManagedProcess("Demo", [sys.executable, "-c", "import time; time.sleep(60)"], tmp_path / "demo.log")
    process.start()
    try:
        calls = iter([False, False, True])
        assert process.wait_ready(lambda: next(calls), timeout=10, interval=0.01) >= 0
    finally:
        process.stop(grace=5)


def test_stop_kills_the_whole_process_group_including_grandchildren(tmp_path: Path) -> None:
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    process = ManagedProcess("Demo", [sys.executable, "-c", script], tmp_path / "demo.log")
    process.start()
    deadline = time.monotonic() + 10
    while not process.tail().strip().isdigit() and time.monotonic() < deadline:
        time.sleep(0.05)
    grandchild = int(process.tail().strip())
    process.stop(grace=5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail("grandchild process survived stop()")


class _FakeProcess:
    def __init__(self, name: str, events: list[str], log_path: Path) -> None:
        self.name, self.events, self.log_path = name, events, log_path

    def start(self) -> None:
        self.events.append(f"start {self.name}")

    def wait_ready(self, _check: Any, _timeout: float) -> float:
        self.events.append(f"ready {self.name}")
        return 0.0

    def stop(self, grace: float | None = 10.0) -> None:
        self.events.append(f"stop {self.name}")
        self.events.append(f"grace {self.name} {grace}")


def _patch_fake_processes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(launcher, "_mlflow_process", lambda _port, _data: _FakeProcess("mlflow", events, Path("m.log")))
    monkeypatch.setattr(
        launcher, "_api_process", lambda _plan, _host, _port: _FakeProcess("api", events, Path("a.log"))
    )
    return events


def _run_services(plan: LaunchPlan, body: Any = None) -> list[str]:
    messages: list[str] = []
    with launcher.running_services(
        plan, host="127.0.0.1", port=8000, mlflow_port=5001, startup_timeout=5, log=messages.append
    ):
        if body:
            body()
    return messages


def test_services_start_mlflow_first_and_stop_the_api_first(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _patch_fake_processes(monkeypatch)
    _run_services(_plan())
    assert events == [
        "start mlflow",
        "ready mlflow",
        "start api",
        "ready api",
        "stop api",
        "grace api None",
        "stop mlflow",
        "grace mlflow 10.0",
    ]


def test_services_are_stopped_when_the_body_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _patch_fake_processes(monkeypatch)

    def boom() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _run_services(_plan(), boom)
    assert events[-4:] == ["stop api", "grace api None", "stop mlflow", "grace mlflow 10.0"]


@pytest.mark.parametrize("interrupted", [False, True])
def test_shutdown_ignores_repeated_signals_and_restores_handlers(monkeypatch, interrupted) -> None:
    events = _patch_fake_processes(monkeypatch)
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    original_stop = _FakeProcess.stop

    def stop(process, grace: float | None = 10.0) -> None:
        # Deliver real signals while the launcher is waiting for owned services.
        signal.raise_signal(signal.SIGINT)
        signal.raise_signal(signal.SIGTERM)
        original_stop(process, grace)

    monkeypatch.setattr(_FakeProcess, "stop", stop)

    def body() -> None:
        if interrupted:
            raise KeyboardInterrupt

    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            _run_services(_plan(), body)
    else:
        _run_services(_plan(), body)

    assert events[-4:] == ["stop api", "grace api None", "stop mlflow", "grace mlflow 10.0"]
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers


def test_shutdown_restores_handlers_and_unregisters_cleanup_on_failure(monkeypatch) -> None:
    _patch_fake_processes(monkeypatch)
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    unregistered = []
    monkeypatch.setattr(launcher.atexit, "register", lambda _callback: None)
    monkeypatch.setattr(launcher.atexit, "unregister", unregistered.append)

    def stop(_process, grace: float | None = 10.0) -> None:
        _ = grace
        raise RuntimeError("stop failed")

    monkeypatch.setattr(_FakeProcess, "stop", stop)
    with pytest.raises(RuntimeError, match="stop failed"):
        _run_services(_plan())

    assert len(unregistered) == 1
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers


def test_a_failed_api_start_still_stops_mlflow(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _patch_fake_processes(monkeypatch)

    class FailingApi(_FakeProcess):
        def wait_ready(self, _check: Any, _timeout: float) -> float:
            raise LaunchError("API server exited during startup")

    monkeypatch.setattr(launcher, "_api_process", lambda _plan, _host, _port: FailingApi("api", events, Path("a.log")))
    with pytest.raises(LaunchError):
        _run_services(_plan())
    assert events[-4:] == ["stop api", "grace api None", "stop mlflow", "grace mlflow 10.0"]


def _stub_cli(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(launcher, "verify_api_destination", lambda *_args: None)

    class Services:
        def __init__(self, plan: LaunchPlan, **kwargs: Any) -> None:
            seen["plan"], seen["kwargs"] = plan, kwargs

        def __enter__(self) -> LaunchPlan:
            return seen["plan"]

        def __exit__(self, *_exc: Any) -> None:
            seen["exited"] = True

    monkeypatch.setattr(cli.launcher, "running_services", Services)
    monkeypatch.setattr(cli.launcher, "api_is_healthy", lambda _url: False)
    monkeypatch.setattr(cli.launcher, "mlflow_is_healthy", lambda _url: False)
    monkeypatch.setattr(cli, "run_interactive_cli", lambda **kw: seen.setdefault("ran", kw))
    return seen


def test_fleet_cli_starts_services_then_runs_the_client_against_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _stub_cli(monkeypatch)
    cli.main(["cli", "--session", "s1"])
    plan = seen["plan"]
    assert plan.start_api and plan.start_mlflow and seen["exited"]
    assert seen["ran"]["base_url"] == plan.api_url
    assert seen["ran"]["session_id"] == "s1"
    assert seen["ran"]["settings"].mlflow_mode == "local"


def test_fleet_cli_with_an_explicit_url_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _stub_cli(monkeypatch)
    cli.main(["cli", "--session", "s1", "--url", "http://remote.test:9000"])
    assert not seen["plan"].start_api and seen["ran"]["base_url"] == "http://remote.test:9000"


@pytest.mark.parametrize("during_startup", [False, True])
def test_fleet_cli_handles_interrupt_without_traceback(monkeypatch, capsys, during_startup) -> None:
    seen = _stub_cli(monkeypatch)

    def interrupt(**_kwargs) -> None:
        raise KeyboardInterrupt

    if during_startup:
        monkeypatch.setattr(cli.launcher, "plan_launch", interrupt)
    else:
        monkeypatch.setattr(cli, "run_interactive_cli", interrupt)

    with pytest.raises(SystemExit) as error:
        cli.main(["cli"])

    assert error.value.code == 130
    assert during_startup or seen["exited"]
    output = capsys.readouterr()
    assert "Traceback" not in output.out + output.err


def test_fleet_cli_reports_a_startup_failure_and_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    class Failing:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> None:
            raise LaunchError("API server exited during startup (code 1).")

        def __exit__(self, *_exc: Any) -> None:
            return None

    monkeypatch.setattr(cli.launcher, "running_services", Failing)
    monkeypatch.setattr(cli.launcher, "api_is_healthy", lambda _url: False)
    monkeypatch.setattr(cli.launcher, "mlflow_is_healthy", lambda _url: False)
    monkeypatch.setattr(cli, "run_interactive_cli", lambda **_kw: pytest.fail("client must not start"))
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["cli"])
    assert exit_info.value.code == 1


def test_owned_service_shutdown_stops_api_before_mlflow(monkeypatch) -> None:
    events = _patch_fake_processes(monkeypatch)
    plan = LaunchPlan(
        api_url="http://127.0.0.1:8000",
        start_api=True,
        start_mlflow=True,
        mlflow_url="http://127.0.0.1:5001",
    )

    _run_services(plan)

    api_stop_index = events.index("stop api")
    mlflow_stop_index = events.index("stop mlflow")
    assert api_stop_index < mlflow_stop_index
    assert events[api_stop_index + 1] == "grace api None"
    assert events[mlflow_stop_index + 1] == "grace mlflow 10.0"


def test_reused_api_shutdown_does_not_stop_remote_services(monkeypatch) -> None:
    events = _patch_fake_processes(monkeypatch)

    _run_services(LaunchPlan(api_url="http://remote.test:8000"))

    assert events == []


def test_managed_process_stop_accepts_unbounded_grace_without_force_kill(monkeypatch, tmp_path: Path) -> None:
    class Process:
        pid = 123
        returncode = None

        def __init__(self) -> None:
            self.wait_timeout: float | None = -1

        def poll(self):
            return self.returncode

        def wait(self, timeout: float | None = None):
            self.wait_timeout = timeout
            self.returncode = 0
            return 0

        def send_signal(self, _signal: int) -> None:
            return None

    process = ManagedProcess("API", [], tmp_path / "api.log")
    child = Process()
    process._process = child  # type: ignore[assignment]
    signals: list[int] = []
    monkeypatch.setattr(ManagedProcess, "_signal", staticmethod(lambda _process, sig: signals.append(sig)))

    process.stop(grace=None)

    assert signals == [signal.SIGTERM]
    assert child.wait_timeout is None
    assert child.returncode == 0


# MLflow backend selection


def test_profiles_have_independent_precedence_and_provenance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/fleet.toml").write_text(
        '[config]\nschema_version=5\n[mlflow.local]\nexperiment_name="local-toml"\n'
        '[mlflow.databricks]\nexperiment_name="/Shared/db-toml"\n'
    )
    (tmp_path / ".env").write_text("FLEET_MLFLOW_LOCAL_EXPERIMENT_NAME=local-dotenv\n")
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_EXPERIMENT_NAME", "/Shared/db-env")
    local = FleetSettings(_env_file=".env")
    db = FleetSettings(_env_file=".env", mlflow_mode="databricks")
    assert local.mlflow_experiment_name == "local-dotenv"
    assert db.mlflow_experiment_name == "/Shared/db-env"
    assert local.configuration()["sources"]["mlflow_local_experiment_name"] == "dotenv"
    assert db.configuration()["sources"]["mlflow_databricks_experiment_name"] == "environment"
    assert local.configuration()["digest"] != db.configuration()["digest"]
    assert local.with_explicit_overrides(mlflow_local_experiment_name="explicit").mlflow_experiment_name == "explicit"


def test_configured_databricks_destinations_keep_the_three_experiments_separate():
    settings = FleetSettings(_env_file=None, mlflow_mode="databricks")
    target = settings.mlflow_target()

    assert target.tracking_uri == "databricks"
    assert target.experiment_name == "/Shared/fleet-rlm-runtime"
    assert target.eval_experiment_name == "/Shared/fleet-rlm-eval"
    assert target.optimization_experiment_name == "/Shared/fleet-rlm-gepa"
    assert (settings.mlflow_trace_catalog, settings.mlflow_trace_schema, settings.mlflow_trace_table_prefix) == (
        "example_catalog",
        "fleet_observability",
        "fleet_rlm_runtime",
    )
    assert (settings.mlflow_eval_trace_table_prefix, settings.mlflow_optimization_trace_schema) == (
        "fleet_rlm_eval",
        "fleet_gepa",
    )
    assert FleetSettings(_env_file=None).mlflow_target().mode == "local"


def test_databricks_trace_prefix_dotenv_overrides_toml_and_can_be_corrected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FLEET_MLFLOW_DATABRICKS_TRACE_TABLE_PREFIX", raising=False)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/fleet.toml").write_text(
        '[config]\nschema_version=5\n[mlflow.databricks]\ntrace_table_prefix="fleet_rlm_dev"\n'
    )
    env = tmp_path / ".env"
    env.write_text("FLEET_MLFLOW_DATABRICKS_TRACE_TABLE_PREFIX=fleet_rlm_runtime\n")
    stale = FleetSettings(_env_file=env, mlflow_mode="databricks")
    assert stale.mlflow_trace_table_prefix == "fleet_rlm_runtime"
    assert stale.configuration_source("mlflow_databricks_trace_table_prefix") == "dotenv"

    env.write_text("FLEET_MLFLOW_DATABRICKS_TRACE_TABLE_PREFIX=fleet_rlm_dev\n")
    corrected = FleetSettings(_env_file=env, mlflow_mode="databricks")
    assert corrected.mlflow_trace_table_prefix == "fleet_rlm_dev"
    assert corrected.configuration_source("mlflow_databricks_trace_table_prefix") == "dotenv"


@pytest.mark.parametrize("legacy", ["FLEET_MLFLOW_MODE", "FLEET_MLFLOW_EXPERIMENT_NAME", "MLFLOW_TRACKING_URI"])
def test_retired_dotenv_keys_require_migration_without_showing_values(tmp_path, monkeypatch, legacy):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"{legacy}=private-marker\n")
    with pytest.raises(ValueError, match="migrate") as error:
        FleetSettings(_env_file=".env")
    assert legacy in str(error.value)
    assert "private-marker" not in str(error.value)


def test_retired_process_mode_cannot_redirect_the_cli(monkeypatch, capsys):
    monkeypatch.setenv("FLEET_MLFLOW_MODE", "databricks")
    with pytest.raises(SystemExit) as error:
        cli.main(["cli"])
    assert error.value.code == 2
    assert "migrate" in capsys.readouterr().err


def test_old_schema_requires_migration_and_toml_selects_mlflow_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "fleet.toml.example"
    monkeypatch.setenv("FLEET_CONFIG", str(path))
    path.write_text("[config]\nschema_version=3\n")
    with pytest.raises(ValueError, match="schema_version = 5"):
        FleetSettings(_env_file=None)
    path.write_text('[config]\nschema_version=5\n[mlflow]\nmode="databricks"\n')
    assert FleetSettings(_env_file=None).mlflow_mode == "databricks"


@pytest.mark.parametrize("arguments", [["databricks"], ["--mlflow", "databricks"], ["--session", "s1", "databricks"]])
def test_cli_selects_databricks_and_propagates_the_profile(monkeypatch, arguments):
    from contextlib import nullcontext

    seen = {}
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.invalid")
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACKING_URI", "databricks")
    monkeypatch.setattr(launcher, "api_is_healthy", lambda _url: False)
    monkeypatch.setattr(launcher, "verify_api_destination", lambda *_args: None)
    monkeypatch.setattr(launcher, "running_services", lambda plan, **_kwargs: nullcontext(plan))
    monkeypatch.setattr(cli, "run_interactive_cli", lambda **kwargs: seen.update(kwargs))
    cli.main(["cli", *arguments])
    assert seen["settings"].mlflow_mode == "databricks"
    assert seen["settings"].mlflow_target().experiment_name.startswith("/")


@pytest.mark.parametrize(
    "arguments",
    [
        ["cli", "databricks", "--mlflow", "local"],
        ["cli", "databricks", "--mlflow-port", "5002"],
        ["cli", "--mlflow", "env"],
        ["cli", "--mlflow-port", "0"],
    ],
)
def test_invalid_selectors_and_overrides_fail_before_launch(monkeypatch, arguments):
    monkeypatch.setattr(launcher, "plan_launch", lambda **_kwargs: pytest.fail("must not launch"))
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2


@pytest.mark.parametrize("backend", ["local", "databricks"])
def test_standalone_commands_receive_selected_backend(monkeypatch, backend):
    seen = []
    monkeypatch.setattr(launcher, "mlflow_is_healthy", lambda _url: True)
    monkeypatch.setattr(cli, "_eval_command", lambda _args, settings: seen.append(settings.mlflow_mode))
    monkeypatch.setattr(cli, "_trace_verify_command", lambda _args, settings: seen.append(settings.mlflow_mode))
    monkeypatch.setattr(
        "fleet_rlm.optimize.gepa.run", lambda _args, _parser, settings: seen.append(settings.mlflow_mode)
    )
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **_kwargs: seen.append(app.state.settings.mlflow_mode))
    cli.main(["eval", "label", "tr-1", "--mlflow", backend])
    cli.main(["trace", "verify", "tr-1", "--mlflow", backend])
    cli.main(["optimize", "--mlflow", backend])
    cli.main(["serve", "--mlflow", backend])
    assert seen == [backend] * 4


def test_standalone_local_command_explains_missing_server(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "mlflow_is_healthy", lambda _url: False)
    with pytest.raises(SystemExit) as error:
        cli.main(["trace", "verify", "tr-1"])
    assert error.value.code == 2
    assert "Local MLflow is not reachable" in capsys.readouterr().err


def _health(settings):
    return {"status": "ok", "mlflow": {**settings.mlflow_descriptor(), "ready": True, "experiment_id": "17"}}


def _respond(monkeypatch, payload):
    monkeypatch.setattr(
        launcher.httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(
            200, json=payload, request=httpx.Request("GET", "http://fleet.test/health")
        ),
    )


def test_reuse_checks_metadata_without_creating_a_session(monkeypatch):
    settings = FleetSettings(_env_file=None)
    _respond(monkeypatch, _health(settings))
    plan = launcher.plan_launch(url="http://fleet.test", host="127.0.0.1", port=8000, settings=settings)
    assert not plan.start_api and not plan.start_mlflow


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "databricks"),
        ("tracking_uri", "http://127.0.0.1:5002"),
        ("experiment_name", "other"),
        ("eval_experiment_name", "other"),
        ("optimization_experiment_name", "other"),
        ("trace_location", {"catalog": "wrong"}),
    ],
)
def test_reuse_rejects_destination_mismatches(monkeypatch, field, value):
    settings = FleetSettings(_env_file=None)
    payload = _health(settings)
    payload["mlflow"][field] = value
    _respond(monkeypatch, payload)
    with pytest.raises(launcher.LaunchError, match="destination mismatch"):
        launcher.verify_api_destination("http://fleet.test", settings.mlflow_descriptor())


@pytest.mark.parametrize("payload", [[], {"status": "ok"}, {"status": "ok", "mlflow": {"ready": False}}])
def test_reuse_rejects_unknown_or_unready_servers(monkeypatch, payload):
    _respond(monkeypatch, payload)
    with pytest.raises(launcher.LaunchError, match="no ready MLflow"):
        launcher.verify_api_destination("http://fleet.test", FleetSettings(_env_file=None).mlflow_descriptor())


def test_eval_preflight_rejects_runtime_experiment_before_work(monkeypatch):
    settings = FleetSettings(
        _env_file=None,
        mlflow_mode="databricks",
        mlflow_databricks_tracking_uri="databricks",
        databricks_host="https://workspace.invalid",
    )
    _respond(monkeypatch, _health(settings))
    monkeypatch.setattr(
        "mlflow.set_experiment", lambda *_args, **_kwargs: pytest.fail("must not create evaluation work")
    )
    with pytest.raises(SystemExit, match="destination mismatch"):
        cli._eval_command(SimpleNamespace(eval_command="run", url="http://fleet.test"), settings)


def test_databricks_health_rejects_another_workspace_before_evaluation(monkeypatch):
    settings = FleetSettings(
        _env_file=None,
        mlflow_mode="databricks",
        mlflow_databricks_tracking_uri="databricks",
        databricks_host="https://client.invalid",
    )
    server_settings = settings.with_explicit_overrides(databricks_host="https://server.invalid")
    monkeypatch.setattr(api, "_configure_tracing", lambda _settings: "17")
    monkeypatch.setattr(api.mlflow, "flush_trace_async_logging", lambda: None)
    with TestClient(api.create_app(settings=server_settings, initialize_db=False)) as client:
        health = client.get("/health").json()
    assert health["mlflow"]["workspace_host"] == "https://server.invalid"
    _respond(monkeypatch, health)
    with pytest.raises(launcher.LaunchError, match="destination mismatch"):
        launcher.verify_api_destination("http://fleet.test", settings.mlflow_descriptor())


@pytest.mark.parametrize("tracking_uri", ["databricks", "databricks://named"])
def test_databricks_workspace_identity_uses_resolved_profile_host(monkeypatch, tracking_uri):
    calls = []

    def credentials(uri):
        calls.append(uri)
        return SimpleNamespace(host="https://PROFILE.invalid/", token="private-marker")

    monkeypatch.setattr("mlflow.utils.databricks_utils.get_databricks_host_creds", credentials)
    settings = FleetSettings(
        _env_file=None,
        mlflow_mode="databricks",
        mlflow_databricks_tracking_uri=tracking_uri,
        databricks_host="https://gateway.invalid" if tracking_uri != "databricks" else "",
    )
    descriptor = settings.mlflow_descriptor(evaluation=True)
    assert descriptor["workspace_host"] == "https://profile.invalid"
    assert calls == [tracking_uri]
    assert "private-marker" not in str(descriptor)


def test_databricks_workspace_identity_resolution_fails_closed(monkeypatch):
    def fail(_uri):
        raise ValueError("private-marker")

    monkeypatch.setattr("mlflow.utils.databricks_utils.get_databricks_host_creds", fail)
    settings = FleetSettings(_env_file=None, mlflow_mode="databricks", databricks_host="")
    with pytest.raises(ValueError, match="Could not resolve") as error:
        settings.mlflow_descriptor()
    assert "private-marker" not in str(error.value)


def test_tracing_failure_releases_owned_database_pool(monkeypatch):
    events = []
    store = SimpleNamespace(
        initialize=lambda: events.append("initialize"),
        close_migration_pool=lambda: events.append("close migration"),
        close=lambda: events.append("close store"),
    )
    monkeypatch.setattr(api.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(api, "mlflow_is_healthy", lambda _url: False)
    with (
        pytest.raises(RuntimeError, match="Local MLflow is not reachable"),
        TestClient(api.create_app(settings=FleetSettings(_env_file=None))),
    ):
        pytest.fail("application must not become ready")
    assert events == ["initialize", "close migration", "close store"]


@pytest.mark.parametrize("failure", ["experiments", "registry", "store", "flush"])
def test_shutdown_attempts_every_owned_cleanup_after_a_failure(monkeypatch, tmp_path, failure):
    events = []

    def cleanup(name):
        events.append(name)
        if name == failure:
            raise RuntimeError(f"{name} cleanup failed")

    store = SimpleNamespace(initialize=lambda: None, close_migration_pool=lambda: None, close=lambda: cleanup("store"))

    class FakeExperimentManager:
        def __init__(self, _settings, _store):
            pass

        async def initialize(self):
            return None

        async def close(self):
            cleanup("experiments")

    monkeypatch.setattr(api.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(api, "ExperimentManager", FakeExperimentManager)
    monkeypatch.setattr(api, "_configure_tracing", lambda _settings: "17")
    monkeypatch.setattr(
        api, "SessionRuntimeRegistry", lambda *_args: SimpleNamespace(close=lambda: cleanup("registry"))
    )
    monkeypatch.setattr(api.mlflow, "flush_trace_async_logging", lambda: cleanup("flush"))
    app = api.create_app(settings=FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)))
    if failure == "flush":
        with TestClient(app):
            pass
    else:
        with pytest.raises(RuntimeError, match=f"{failure} cleanup failed"), TestClient(app):
            pass
    assert events == ["experiments", "registry", "store", "flush"]


def test_shutdown_preserves_injected_store_ownership(monkeypatch):
    store = SimpleNamespace(close=lambda: pytest.fail("injected Store belongs to its caller"))
    monkeypatch.setattr(api, "SessionRuntimeRegistry", lambda *_args: SimpleNamespace(close=lambda: None))
    with TestClient(
        api.create_app(
            settings=FleetSettings(_env_file=None), initialize_db=False, configure_observability=False, store=store
        )
    ):
        pass


def test_health_reports_successfully_initialized_tracking_without_a_session(monkeypatch):
    monkeypatch.setattr(api, "_configure_tracing", lambda _settings: "17")
    monkeypatch.setattr(api.mlflow, "flush_trace_async_logging", lambda: None)
    app = api.create_app(settings=FleetSettings(_env_file=None), initialize_db=False)
    with TestClient(app) as client:
        payload = client.get("/health").json()
        assert payload == {**_health(app.state.settings), "version": api.VERSION}


def test_databricks_initialization_errors_abort_startup_without_echoing_credentials(monkeypatch):
    def fail(*_args, **_kwargs):
        raise ValueError("private-marker")

    monkeypatch.setattr(api, "configure_mlflow_observability", fail)
    with pytest.raises(RuntimeError, match="Could not initialize databricks") as error:
        api._configure_tracing(FleetSettings(_env_file=None, mlflow_mode="databricks"))
    assert "private-marker" not in str(error.value)


def test_databricks_destination_mismatch_aborts_startup_with_safe_guidance(monkeypatch):
    from fleet_rlm import observability

    monkeypatch.setattr(observability, "capture_serving_snapshot", lambda: None)
    monkeypatch.setattr(api.mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(api.mlflow.tracing, "configure", lambda **_kwargs: None)
    monkeypatch.setattr(
        api.mlflow,
        "get_experiment_by_name",
        lambda _name: SimpleNamespace(tags={"mlflow.experiment.databricksTraceDestinationPath": "private-marker"}),
    )
    monkeypatch.setattr(api.mlflow, "set_experiment", lambda *_args, **_kwargs: pytest.fail("must not activate"))
    app = api.create_app(settings=FleetSettings(_env_file=None, mlflow_mode="databricks"), initialize_db=False)

    with pytest.raises(RuntimeError, match="trace destination mismatch") as error, TestClient(app):
        pytest.fail("application must not become ready")

    message = str(error.value)
    assert "private-marker" not in message
    assert ".env overrides" in message
    for suffix in ("CATALOG", "SCHEMA", "TABLE_PREFIX"):
        assert f"FLEET_MLFLOW_DATABRICKS_TRACE_{suffix}" in message


def test_databricks_auth_uses_dotenv_credentials_but_named_profile_stays_explicit(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    path.write_text("DATABRICKS_HOST=https://workspace.invalid\nDATABRICKS_TOKEN=private-marker\n")
    settings = FleetSettings(_env_file=path, mlflow_mode="databricks", mlflow_databricks_tracking_uri="databricks")
    monkeypatch.setenv("DATABRICKS_HOST", "")
    monkeypatch.setenv("DATABRICKS_TOKEN", "")
    settings.prepare_mlflow_auth()
    import os

    assert os.environ["DATABRICKS_TOKEN"] == "private-marker"
    assert os.environ["DATABRICKS_HOST"] == "https://workspace.invalid"
    monkeypatch.setenv("DATABRICKS_TOKEN", "existing")
    settings.with_explicit_overrides(mlflow_databricks_tracking_uri="databricks://named").prepare_mlflow_auth()
    assert os.environ["DATABRICKS_TOKEN"] == "existing"
    assert os.environ["DATABRICKS_CONFIG_PROFILE"] == "named"
    assert "private-marker" not in str(settings.configuration())


def test_databricks_named_profile_is_propagated_to_sdk_subprocesses():
    settings = FleetSettings(
        _env_file=None,
        mlflow_mode="databricks",
        mlflow_databricks_tracking_uri="databricks://example-profile",
    )

    environment = settings.mlflow_environment()

    assert environment["FLEET_MLFLOW_DATABRICKS_TRACKING_URI"] == "databricks://example-profile"
    assert environment["DATABRICKS_CONFIG_PROFILE"] == "example-profile"
    assert "DATABRICKS_TOKEN" not in environment


def test_local_service_uses_configured_persistent_directory(tmp_path):
    data = tmp_path / "tracking"
    process = launcher._mlflow_process(5012, str(data))
    assert f"sqlite:///{data / 'mlflow.db'}" in process.command
    assert str(data / "artifacts") in process.command
    assert process.command[-2:] == ["--port", "5012"]


def test_local_mlflow_server_environment_forwards_dotenv_kek_and_respects_env_precedence(tmp_path, monkeypatch):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("MLFLOW_CRYPTO_KEK_PASSPHRASE=dotenv-kek-marker\nMLFLOW_CRYPTO_KEK_VERSION=2\n")
    data_dir = str(tmp_path / "mlflow")

    baseline = FleetSettings(_env_file=None, mlflow_local_data_dir=data_dir)
    settings = FleetSettings(_env_file=dotenv_path, mlflow_local_data_dir=data_dir)

    assert settings.local_mlflow_server_environment() == {
        "MLFLOW_CRYPTO_KEK_PASSPHRASE": "dotenv-kek-marker",
        "MLFLOW_CRYPTO_KEK_VERSION": "2",
    }
    assert "dotenv-kek-marker" not in str(settings.configuration())
    assert settings.configuration()["digest"] == baseline.configuration()["digest"]

    monkeypatch.setenv("MLFLOW_CRYPTO_KEK_PASSPHRASE", "env-kek-marker")
    monkeypatch.setenv("MLFLOW_CRYPTO_KEK_VERSION", "3")
    assert settings.local_mlflow_server_environment() == {
        "MLFLOW_CRYPTO_KEK_PASSPHRASE": "env-kek-marker",
        "MLFLOW_CRYPTO_KEK_VERSION": "3",
    }

    monkeypatch.setenv("MLFLOW_CRYPTO_KEK_PASSPHRASE", "")
    assert settings.local_mlflow_server_environment() == {"MLFLOW_CRYPTO_KEK_VERSION": "3"}

    monkeypatch.setenv("MLFLOW_CRYPTO_KEK_VERSION", "0")
    with pytest.raises(ValueError, match="MLFLOW_CRYPTO_KEK_VERSION"):
        settings.local_mlflow_server_environment()
    with pytest.raises(LaunchError, match="MLFLOW_CRYPTO_KEK_VERSION"):
        _plan(settings=settings)


def test_local_service_forwards_kek_env_and_keeps_repr_secret_free(tmp_path, monkeypatch):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("MLFLOW_CRYPTO_KEK_PASSPHRASE=dotenv-kek-marker\nMLFLOW_CRYPTO_KEK_VERSION=2\n")
    data = tmp_path / "tracking"
    settings = FleetSettings(_env_file=dotenv_path, mlflow_local_data_dir=str(data))

    plan = _plan(settings=settings)
    assert plan.start_mlflow
    assert plan.mlflow_env == {
        "MLFLOW_CRYPTO_KEK_PASSPHRASE": "dotenv-kek-marker",
        "MLFLOW_CRYPTO_KEK_VERSION": "2",
    }
    assert "dotenv-kek-marker" not in repr(plan)
    assert "MLFLOW_CRYPTO_KEK_PASSPHRASE" not in plan.api_env

    seen_processes: list[ManagedProcess] = []
    real_mlflow_process = launcher._mlflow_process

    def fake_mlflow_process(mlflow_port: int, data_dir: str, env: dict[str, str] | None = None) -> Any:
        proc = real_mlflow_process(mlflow_port, data_dir, env=env)
        seen_processes.append(proc)
        return SimpleNamespace(
            name=proc.name,
            log_path=proc.log_path,
            start=lambda: None,
            wait_ready=lambda *_a: 0.0,
            stop=lambda **_kw: None,
        )

    monkeypatch.setattr(launcher, "_mlflow_process", fake_mlflow_process)
    monkeypatch.setattr(
        launcher,
        "_api_process",
        lambda *_a: SimpleNamespace(
            name="API", log_path=Path("a.log"), start=lambda: None, wait_ready=lambda *_a: 0.0, stop=lambda **_kw: None
        ),
    )
    _run_services(plan)
    assert len(seen_processes) == 1
    assert seen_processes[0].env == {
        "MLFLOW_CRYPTO_KEK_PASSPHRASE": "dotenv-kek-marker",
        "MLFLOW_CRYPTO_KEK_VERSION": "2",
        "MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS": "true",
    }
    assert "dotenv-kek-marker" not in repr(seen_processes[0])


def test_plan_launch_warns_and_doctor_blocks_on_local_mlflow_kek_mismatch(tmp_path, monkeypatch):
    import fleet_rlm.doctor as doctor

    mlflow_dir = tmp_path / "mlflow"
    mlflow_dir.mkdir()
    db_path = mlflow_dir / "mlflow.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE secrets (secret_id TEXT PRIMARY KEY, kek_version INTEGER)")
        conn.execute("INSERT INTO secrets VALUES ('s-1', 2)")

    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("MLFLOW_CRYPTO_KEK_PASSPHRASE=dotenv-kek-marker\nMLFLOW_CRYPTO_KEK_VERSION=2\n")
    settings = FleetSettings(_env_file=dotenv_path, mlflow_local_data_dir=str(mlflow_dir))

    def respond_secrets(using_default: bool) -> None:
        monkeypatch.setattr(
            launcher.httpx,
            "get",
            lambda *_a, **_kw: httpx.Response(
                200,
                json={"secrets_available": True, "using_default_passphrase": using_default},
                request=httpx.Request("GET", "http://127.0.0.1:5001/ajax-api/3.0/mlflow/gateway/secrets/config"),
            ),
        )

    respond_secrets(True)
    reused_plan = _plan(mlflow_healthy=lambda _u: True, settings=settings)
    assert not reused_plan.start_mlflow and reused_plan.mlflow_env == {}
    assert any("default KEK passphrase" in note for note in reused_plan.notes)

    monkeypatch.setattr(launcher, "mlflow_is_healthy", lambda _u: True)
    blocked_reuse = doctor.check_mlflow(settings)
    assert blocked_reuse["status"] == "blocked"
    assert "default KEK passphrase" in blocked_reuse["message"]

    respond_secrets(False)
    assert doctor.check_mlflow(settings)["status"] == "ready"

    dotenv_missing_version = tmp_path / ".env.v1"
    dotenv_missing_version.write_text("MLFLOW_CRYPTO_KEK_PASSPHRASE=dotenv-kek-marker\n")
    mismatch = doctor.check_mlflow(
        FleetSettings(_env_file=dotenv_missing_version, mlflow_local_data_dir=str(mlflow_dir))
    )
    assert mismatch["status"] == "blocked"
    assert "MLFLOW_CRYPTO_KEK_VERSION" in mismatch["message"]

    no_passphrase = doctor.check_mlflow(FleetSettings(_env_file=None, mlflow_local_data_dir=str(mlflow_dir)))
    assert no_passphrase["status"] == "blocked"
    assert "MLFLOW_CRYPTO_KEK_PASSPHRASE" in no_passphrase["message"]

    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE secrets SET kek_version = 1")
    respond_secrets(True)
    ready = doctor.check_mlflow(FleetSettings(_env_file=None, mlflow_local_data_dir=str(mlflow_dir)))
    assert ready["status"] == "ready"


def test_optimize_inherits_backend_and_effective_profile_without_sdk_destination(monkeypatch, capsys):
    import time

    settings = FleetSettings(
        _env_file=None, mlflow_mode="databricks", mlflow_databricks_experiment_name="/Shared/custom"
    )
    state = commands.SessionState(mlflow_settings=settings)
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                202,
                json={"job": {"run_id": "s1-run", "status": "running", "stage": "search"}},
            )
        return httpx.Response(200, json={"job": {"run_id": "s1-run", "status": "completed"}})

    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    monkeypatch.setenv("MLFLOW_EXPERIMENT_ID", "stale")
    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(handle)) as client:
        ctx = commands.Context(client, "s1", state)
        commands._optimize(ctx, "--max-metric-calls 1")
        commands._optimize(ctx, "--mlflow=local")

    assert [request.method for request in requests] == ["POST", "GET"]
    assert requests[0].url.path == "/improve/jobs"
    body = json.loads(requests[0].content)
    assert body["max_metric_calls"] == 1
    assert body["request_id"]
    assert "mlflow" not in body and "tracking_uri" not in body and "experiment_name" not in body
    assert "MLFLOW_EXPERIMENT_ID" not in requests[0].headers
    assert "MLFLOW_TRACKING_URI" not in requests[0].headers
    assert os.environ["MLFLOW_EXPERIMENT_ID"] == "stale"
    assert "session's MLflow backend" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["cli", "web"])
def test_invalid_configured_backend_exits_with_doctor_hint(tmp_path, monkeypatch, capsys, command):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "fleet.toml"
    config.write_text('[config]\nschema_version=5\n[mlflow]\nmode="databricks"\n')
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACING_SQL_WAREHOUSE_ID", "")
    with pytest.raises(SystemExit) as error:
        cli.main([command])
    assert error.value.code == 2
    assert "fleet doctor" in capsys.readouterr().err


def test_doctor_command_reports_config_and_check_status(tmp_path, monkeypatch):
    import fleet_rlm.doctor as doctor

    monkeypatch.chdir(tmp_path)
    config = tmp_path / "fleet.toml"
    config.write_text('[config]\nschema_version=5\n[mlflow]\nmode="databricks"\n')
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACING_SQL_WAREHOUSE_ID", "")
    monkeypatch.setattr(doctor, "CONSOLE", Console(file=io.StringIO(), force_terminal=False), raising=False)

    stream = io.StringIO()
    monkeypatch.setattr(cli, "_CONSOLE", Console(file=stream, force_terminal=False))
    with pytest.raises(SystemExit) as bad_cfg:
        cli.main(["doctor"])
    assert bad_cfg.value.code == 1
    assert "blocked" in stream.getvalue()

    monkeypatch.delenv("FLEET_CONFIG", raising=False)
    monkeypatch.delenv("FLEET_MLFLOW_DATABRICKS_TRACING_SQL_WAREHOUSE_ID", raising=False)
    seen_probes: list[bool] = []
    monkeypatch.setattr(
        doctor,
        "check_models",
        lambda _s, *, probe_models=False: seen_probes.append(probe_models) or {"status": "ready", "message": "ok"},
    )
    monkeypatch.setattr(doctor, "check_database", lambda _s: {"status": "ready", "message": "ok"})
    monkeypatch.setattr(doctor, "check_mlflow", lambda _s: {"status": "ready", "message": "ok"})
    monkeypatch.setattr(doctor, "check_daytona", lambda _s: {"status": "ready", "message": "ok"})

    ready_stream = io.StringIO()
    monkeypatch.setattr(cli, "_CONSOLE", Console(file=ready_stream, force_terminal=False))
    with pytest.raises(SystemExit) as ok:
        cli.main(["doctor", "--probe-models"])
    assert ok.value.code == 0
    assert seen_probes == [True]

    monkeypatch.setattr(doctor, "check_daytona", lambda _s: {"status": "blocked", "message": "Snapshot missing"})
    with pytest.raises(SystemExit) as blocked:
        cli.main(["doctor"])
    assert blocked.value.code == 1


def test_snapshot_cli_commands_redact_secrets_and_route_actions(monkeypatch):
    import fleet_rlm.doctor as doctor

    settings = FleetSettings(
        _env_file=None,
        fleet_daytona_api_key="daytona-secret-xyz",
        fleet_daytona_org_id="org-1",
    )
    monkeypatch.setattr(cli, "get_settings", lambda *_a: settings)
    stream = io.StringIO()
    monkeypatch.setattr(cli, "_CONSOLE", Console(file=stream, force_terminal=False))

    monkeypatch.setattr(doctor, "_daytona_client", lambda _s: SimpleNamespace(snapshot=object()))
    monkeypatch.setattr(
        doctor,
        "preflight",
        lambda *_a, **_kw: SimpleNamespace(name="fleet-rlm-python313-v15", id="snap-1"),
    )
    with pytest.raises(SystemExit) as check_exit:
        cli.main(["snapshot", "check"])
    assert check_exit.value.code == 0
    assert "Snapshot ready: fleet-rlm-python313-v15 (snap-1)" in stream.getvalue()

    def fake_build(*, api_key, org_id, on_logs):
        assert api_key == "daytona-secret-xyz" and org_id == "org-1"
        on_logs("building with key daytona-secret-xyz\n")
        return SimpleNamespace(name="fleet-rlm-python313-v15", id="snap-2")

    monkeypatch.setattr(doctor, "build_snapshot", fake_build)
    with pytest.raises(SystemExit) as build_exit:
        cli.main(["snapshot", "build"])
    assert build_exit.value.code == 0
    out = stream.getvalue()
    assert "daytona-secret-xyz" not in out
    assert "[redacted]" in out
    assert "Snapshot built: fleet-rlm-python313-v15 (snap-2)" in out

    monkeypatch.setattr(
        doctor,
        "activate_snapshot",
        lambda **_kw: SimpleNamespace(name="fleet-rlm-python313-v15", id="snap-2"),
    )
    with pytest.raises(SystemExit) as activate_exit:
        cli.main(["snapshot", "activate"])
    assert activate_exit.value.code == 0
    assert "Snapshot activated: fleet-rlm-python313-v15 (snap-2)" in stream.getvalue()


def test_toml_backend_is_used_by_standalone_commands_and_explicit_flag_wins(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "fleet.toml"
    config.write_text('[config]\nschema_version=5\n[mlflow]\nmode="databricks"\n')
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    monkeypatch.setattr(launcher, "mlflow_is_healthy", lambda _url: True)
    seen = []
    monkeypatch.setattr(cli, "_trace_verify_command", lambda _args, settings: seen.append(settings.mlflow_mode))
    cli.main(["trace", "verify", "trace-1"])
    cli.main(["trace", "verify", "trace-1", "--mlflow", "local"])
    assert seen == ["databricks", "local"]


def test_abort_response_shuts_down_socketpair_stream_and_unblocks_reader():
    import socket

    from httpcore._backends.sync import SyncStream

    from fleet_rlm.tui.stream import _abort_response

    server_sock, client_sock = socket.socketpair()
    reader_unblocked = threading.Event()
    received: list[bytes] = []
    errors: list[BaseException] = []

    class _BlockingSocketStream(httpx.SyncByteStream):
        def __init__(self, stream: SyncStream) -> None:
            self._stream = stream

        def __iter__(self):
            try:
                while True:
                    chunk = self._stream.read(4096, timeout=300.0)
                    if not chunk:
                        break
                    received.append(chunk)
                    yield chunk
            finally:
                reader_unblocked.set()

    class _NoPrematureCloseResponse(httpx.Response):
        def close(self) -> None:
            if not reader_unblocked.is_set():
                raise AssertionError("response.close() must not race socket shutdown")
            super().close()

    sync_stream = SyncStream(client_sock)
    try:
        client_sock.settimeout(300.0)
        response = _NoPrematureCloseResponse(
            200,
            stream=_BlockingSocketStream(sync_stream),
            extensions={"network_stream": sync_stream},
        )
        thread = threading.Thread(
            target=lambda: _consume_iter_lines(response, errors),
            daemon=True,
        )
        thread.start()

        started = time.monotonic()
        _abort_response(response)
        thread.join(timeout=1.0)
        elapsed = time.monotonic() - started

        assert not thread.is_alive()
        assert elapsed < 0.5
        assert not errors
        assert received == []
        server_sock.settimeout(0.5)
        assert server_sock.recv(1) == b""
    finally:
        sync_stream.close()
        server_sock.close()


def _consume_iter_lines(response: httpx.Response, errors: list[BaseException]) -> None:
    try:
        list(response.iter_lines())
    except BaseException as exc:
        errors.append(exc)


def _loopback_sse_server(on_turn_post):
    import contextlib
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _SSEHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _write_chunk(self, data: bytes) -> None:
            self.wfile.write(f"{len(data):x}\r\n".encode("ascii") + data + b"\r\n")
            self.wfile.flush()

        def do_GET(self) -> None:
            if self.path == "/health":
                body = json.dumps({"status": "ok"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            if content_length:
                self.rfile.read(content_length)
            on_turn_post(self)

    class _Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    @contextlib.contextmanager
    def _managed():
        server = _Server(("127.0.0.1", 0), _SSEHandler)
        port = int(server.server_address[1])
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2.0)

    return _managed()


@pytest.mark.asyncio
async def test_live_tcp_sse_cancel_unblocks_promptly_and_clears_cancelling_state():
    import select
    import socket

    stream_open = threading.Event()
    peer_disconnected = threading.Event()
    release_server = threading.Event()

    def handle_post(handler: Any) -> None:
        if handler.path != "/sessions/tcp-cancel/turn":
            handler.send_response(404)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()
        handler._write_chunk(b": ping\n\n")
        event = {"event": "repl_code", "code": "print(1)", "index": 1}
        handler._write_chunk(f"data: {json.dumps(event)}\n\n".encode())
        stream_open.set()

        deadline = time.monotonic() + 5.0
        while not release_server.is_set() and time.monotonic() < deadline:
            try:
                readable, _, _ = select.select([handler.connection], [], [], 0.02)
                if readable and handler.connection.recv(1, socket.MSG_PEEK) == b"":
                    peer_disconnected.set()
                    break
            except OSError:
                peer_disconnected.set()
                break

    try:
        with _loopback_sse_server(handle_post) as base_url:
            state = SessionState()
            with httpx.Client(
                base_url=base_url,
                timeout=httpx.Timeout(300.0, connect=10.0),
            ) as client:
                tui = FleetTextualApp(client, "tcp-cancel", state)
                async with _menu_test(tui, size=(100, 30)) as (pilot, composer):
                    composer.value = "run task"
                    await pilot.press("enter")
                    for _ in range(150):
                        if (
                            stream_open.is_set()
                            and tui._turn_active
                            and tui._active_response is not None
                            and state.last_tracker is not None
                            and len(state.last_tracker.items) == 1
                        ):
                            break
                        await pilot.pause(0.01)
                    assert stream_open.is_set()
                    assert tui._turn_active
                    assert tui._active_response is not None

                    cancel_started = time.monotonic()
                    await pilot.press("escape")
                    await _wait_until_idle(tui, pilot)
                    cancel_elapsed = time.monotonic() - cancel_started
                    assert cancel_elapsed < 2.0
                    assert peer_disconnected.wait(1.0)
                    await pilot.pause(0.05)

                    # Repeated cancel while idle must remain a safe no-op.
                    tui.cancel_active_turn()

                    assert not tui._turn_active
                    assert tui._active_response is None
                    assert tui._active_turn_widget is None
                    assert state.last_tracker is not None
                    assert not state.last_tracker.cancelling
                    assert state.last_tracker.terminal_event == {"event": "interrupted"}
                    assert state.last_tracker.items[0].status == "cancelled"
                    left_plain = tui._render_status_left().plain
                    assert "cancelling" not in left_plain
                    assert "running" not in left_plain
                    footer_plain = state.last_tracker.status_line().plain
                    assert "── Turn summary:" in footer_plain
                    assert "Cancelling turn" not in footer_plain

                    health = client.get("/health")
                    assert health.status_code == 200
                    assert health.json() == {"status": "ok"}
    finally:
        release_server.set()


@pytest.mark.asyncio
async def test_live_tcp_sse_turn_with_keepalive_ping_completes_cleanly():
    def handle_post(handler: Any) -> None:
        assert handler.path == "/sessions/tcp-ping/turn"
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()
        handler._write_chunk(b": ping\n\n")
        events = [
            {"event": "progress", "status": "working", "scope": "root", "checkpoint": 1},
            {"event": "turn_complete", "answer": "ok", "route": "direct", "duration_seconds": 0.1},
            {"event": "done"},
        ]
        for ev in events:
            handler._write_chunk(f"data: {json.dumps(ev)}\n\n".encode())
        handler.wfile.write(b"0\r\n\r\n")
        handler.wfile.flush()

    with _loopback_sse_server(handle_post) as base_url:
        state = SessionState()
        with httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(300.0, connect=10.0),
        ) as client:
            tui = FleetTextualApp(client, "tcp-ping", state)
            async with _menu_test(tui, size=(100, 30)) as (pilot, composer):
                composer.value = "ping test"
                await pilot.press("enter")
                await _wait_until_idle(tui, pilot)
                await pilot.pause(0.05)

                assert not tui._turn_active
                assert state.last_tracker is not None
                assert not state.last_tracker.cancelling
                assert state.last_tracker.status_label is None
                assert state.last_tracker.terminal_event is not None
                assert state.last_tracker.terminal_event["event"] == "turn_complete"
                assert "── Turn summary: route=direct" in state.last_tracker.status_line().plain


def test_turn_tracker_child_agent_result_does_not_overwrite_turn_status():
    tracker = render.TurnTracker()
    tracker.ingest({"event": "turn_status", "phase": "running", "agent_id": "root-1"})
    tracker.ingest({"event": "progress", "status": "synthesizing", "scope": "root", "checkpoint": 2})
    tracker.ingest({"event": "agent_spawn", "agent_id": "child-1", "parent_agent_id": "root-1", "depth": 1})
    tracker.ingest({"event": "agent_spawn", "agent_id": "child-2", "parent_agent_id": "root-1", "depth": 1})

    # Child completion carries status="succeeded" (workspace_io.py:385), which must not hijack Turn status.
    tracker.ingest(
        {
            "event": "agent_result",
            "agent_id": "child-1",
            "parent_agent_id": "root-1",
            "depth": 1,
            "status": "succeeded",
            "artifacts": [],
        }
    )
    # Child-scoped tool/progress updates must not overwrite Turn status either (with or without parent_agent_id).
    tracker.ingest(
        {"event": "tool_start", "agent_id": "child-2", "call_id": "c2", "tool": "sub", "status": "child_busy"}
    )
    tracker.ingest({"event": "progress", "agent_id": "child-2", "parent_agent_id": "root-1", "status": "child_step"})

    assert tracker.status_label == "synthesizing"
    assert tracker.status_scope == "root"
    assert tracker.status_checkpoint == 2
    footer = tracker.status_line().plain
    assert "synthesizing" in footer
    assert "succeeded" not in footer
    assert "child_busy" not in footer
    assert "child_step" not in footer

    # Fallback footer with active children also stays unpolluted when no Turn status_label is set.
    tracker_no_label = render.TurnTracker()
    tracker_no_label.ingest({"event": "turn_status", "phase": "running", "agent_id": "root-1"})
    tracker_no_label.ingest({"event": "agent_spawn", "agent_id": "child-1", "parent_agent_id": "root-1", "depth": 1})
    tracker_no_label.ingest({"event": "agent_spawn", "agent_id": "child-2", "parent_agent_id": "root-1", "depth": 1})
    tracker_no_label.ingest({"event": "agent_result", "agent_id": "child-1", "status": "succeeded"})
    assert tracker_no_label.status_label is None
    fallback_footer = tracker_no_label.status_line().plain
    assert "Running 1 child agent" in fallback_footer
    assert "succeeded" not in fallback_footer

    # Plain CLI event rendering must not print child agent_result status as Turn advisory status.
    buf = io.StringIO()
    old_console = render.CONSOLE
    render.CONSOLE = Console(file=buf, force_terminal=False, no_color=True)
    try:
        render.render_event(
            {
                "event": "agent_result",
                "agent_id": "child-1",
                "parent_agent_id": "root-1",
                "status": "succeeded",
            }
        )
    finally:
        render.CONSOLE = old_console
    assert "Status: succeeded" not in buf.getvalue()

    # Terminal events must clear cancelling state even before `done`.
    tracker.ingest({"event": "cancellation_requested"})
    assert tracker.cancelling
    tracker.ingest({"event": "turn_cancelled", "route": "rlm", "duration_seconds": 1.2})
    assert not tracker.cancelling
