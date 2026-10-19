from __future__ import annotations

import io
import json
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

    output = io.StringIO()
    console = Console(file=output, width=100)
    user_inputs = iter(("/replay on", "summarize the file", "/replay off", "/help", "/exit"))

    def read_input(prompt: str) -> str:
        console.print(prompt, end="")
        return next(user_inputs)

    monkeypatch.setattr(console, "input", read_input)
    use_console(monkeypatch, console)

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
    output = io.StringIO()
    use_console(monkeypatch, Console(file=output, width=100))

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


def test_memory_and_trace_commands_use_session_history_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/memory"):
            return httpx.Response(200, json={"memories": [{"memory_id": "m-1", "content": "Use cents."}]})
        return httpx.Response(200, json={"turns": [{"task": "prior", "answer": "ok"}]})

    output = io.StringIO()
    use_console(monkeypatch, Console(file=output, width=100))
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
    output = io.StringIO()
    use_console(monkeypatch, Console(file=output, width=100))
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
    output = io.StringIO()
    use_console(monkeypatch, Console(file=output, width=100))

    render.render_event(event)

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

    completions = app._completer().get_completions(Document(text), CompleteEvent())
    assert expected in [item.text for item in completions]


@pytest.mark.parametrize(("choice", "selected"), [("2", True), ("", False), ("0", False), ("oops", False)])
def test_interactive_model_choice_and_settings(monkeypatch, choice: str, selected: bool) -> None:
    output = io.StringIO()
    console = Console(file=output, width=100)
    use_console(monkeypatch, console)
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
    output = io.StringIO()
    console = Console(file=output, width=120)
    use_console(monkeypatch, console)
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
    output = io.StringIO()
    use_console(monkeypatch, Console(file=output, width=120))
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
    output = io.StringIO()
    use_console(monkeypatch, Console(file=output, width=100))
    assert commands.handle_command(None, "/nope", "s1") is True
    assert "Unknown command" in output.getvalue()
    assert commands.handle_command(None, "/exit", "s1") is False


def test_optimize_passes_its_arguments_to_fleet_optimize(monkeypatch) -> None:
    from types import SimpleNamespace

    calls: list[list[str]] = []
    monkeypatch.setattr(commands.subprocess, "run", lambda command, **_kwargs: calls.append(command))
    ctx = SimpleNamespace(session_id="sess_tui")
    commands.COMMANDS["/optimize"].run(ctx, "--approve 'sess_tui-abc'")
    commands.COMMANDS["/optimize"].run(ctx, "")
    assert calls[0][-5:] == [
        "optimize",
        "--session",
        "sess_tui",
        "--approve",
        "sess_tui-abc",
    ]
    assert calls[1][-3:] == ["optimize", "--session", "sess_tui"]


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
    assert tracker.handle_key("3") is True
    assert cell3.expanded is False and cell3.user_toggled is True
    tracker.ingest(
        {"event": "repl_output", "execution_id": "task-3", "phase": "task", "error": True, "error_type": "ValueError"}
    )
    assert cell3.expanded is False


def test_turn_tracker_shortcuts_and_confidence_bar() -> None:
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

    assert tracker.handle_key("\x14") is True and thinking.expanded is True
    assert tracker.handle_key("\x0f") is True and repl_cell.expanded is True
    assert tracker.handle_key("a") is True and thinking.expanded is False and repl_cell.expanded is False
    assert tracker.handle_key("a") is True and thinking.expanded is True and repl_cell.expanded is True

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
    output = io.StringIO()
    use_console(monkeypatch, Console(file=output, width=110))

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

    output = io.StringIO()
    use_console(monkeypatch, Console(file=output, width=110))
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
        with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(handle)) as client:
            tui = app.FleetTextualApp(client, "sess_fs", state)
            async with tui.run_test(size=(100, 30)) as pilot:
                inp = tui.query_one("#prompt-input", app._PromptInput)
                assert inp.placeholder == "What would you like to do?"

                # Typing '/mo' + Tab autocompletes the first popup item ('/mode ') directly;
                # typing '/mo' + Down + Tab selects the second popup item ('/models ').
                inp.value = "/mo"
                await pilot.pause(0.05)
                assert tui._popup_index == 0 and tui._popup_items[0][0] == "/mode "
                await pilot.press("tab")
                await pilot.pause(0.05)
                assert inp.value == "/mode "

                inp.value = "/mo"
                await pilot.pause(0.05)
                await pilot.press("down")
                assert tui._popup_index == 1 and tui._popup_items[1][0] == "/models "
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
