"""Textual-first interactive coding app for ReAct + RLM."""

from __future__ import annotations

import json
import os
import queue
import re
import shlex
import time
from pathlib import Path
from typing import Any, cast

from dspy.primitives.code_interpreter import FinalOutput
from rich.markdown import Markdown as RichMarkdown  # ty: ignore[unresolved-import]
from rich.text import Text as RichText  # ty: ignore[unresolved-import]
from textual import work  # ty: ignore[unresolved-import]
from textual.app import App, ComposeResult  # ty: ignore[unresolved-import]
from textual.binding import Binding  # ty: ignore[unresolved-import]
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import (
    Footer,
    Input,
    RichLog,
    Static,
    TabPane,
    TabbedContent,
)

from fleet_rlm.react_agent import RLMReActChatAgent

from .config import get_profile, set_active_profile
from .models import SessionConfig, StreamEvent, TraceMode, TranscriptEvent, TurnState


class CodeChatTextualApp(App[None]):
    """Interactive Textual runtime with streamed events from ``RLMReActChatAgent``."""

    TITLE = "fleet-rlm code-chat"
    SUB_TITLE = "Textual mode"
    BINDINGS = [
        Binding("ctrl+c", "cancel_turn", "Cancel Turn"),
        Binding("ctrl+l", "clear_panes", "Clear Panes"),
        Binding("f2", "toggle_reasoning", "Toggle Reasoning"),
        Binding("f3", "toggle_tools", "Toggle Tools"),
    ]
    CSS = """
    Screen {
        layout: vertical;
    }
    #topbar {
        height: 1;
        background: $surface;
        color: $text;
        padding: 0 1;
    }
    #main {
        height: 1fr;
    }
    #transcript {
        width: 7fr;
        border: tall $primary;
    }
    #right {
        width: 3fr;
        border: tall $boost;
    }
    #live_stream {
        height: 4;
        border: tall $accent;
        padding: 0 1;
    }
    #tabs {
        height: 1fr;
    }
    #reasoning_log, #tools_log {
        border: round $secondary;
    }
    #stats {
        padding: 1;
    }
    #prompt_input {
        dock: bottom;
        height: 3;
        border: tall $primary;
    }
    #hints {
        dock: bottom;
        height: 2;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        *,
        agent: RLMReActChatAgent,
        config: SessionConfig,
    ) -> None:
        super().__init__()
        self.agent = agent
        self.config = config

        self.trace_mode: TraceMode = config.trace_mode
        self.stream_enabled = config.stream

        self._in_flight = False
        self._cancel_requested = False
        self._turn_number = 0
        self._turn_start = 0.0
        self._last_turn_latency = 0.0
        self._turn_state: TurnState | None = None

        self._python_mode = False
        self._python_lines: list[str] = []

        self._event_queue: queue.Queue[StreamEvent] = queue.Queue()
        self._last_token_flush = 0.0

        history_dir = Path.home() / ".cache" / "fleet-rlm"
        history_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = history_dir / "logs" / "code-chat.log"
        self.transcript_path = history_dir / "transcripts" / "latest.jsonl"
        self._transcript_batch: list[TranscriptEvent] = []
        self._configure_logging()

    def compose(self) -> ComposeResult:
        yield Static("", id="topbar")
        with Horizontal(id="main"):
            yield RichLog(id="transcript", wrap=True, markup=True, auto_scroll=True)
            with Vertical(id="right"):
                yield Static("", id="live_stream")
                with TabbedContent(id="tabs"):
                    with TabPane("Reasoning", id="reasoning-pane"):
                        yield RichLog(
                            id="reasoning_log",
                            wrap=True,
                            markup=True,
                            auto_scroll=True,
                        )
                    with TabPane("Tools", id="tools-pane"):
                        yield RichLog(
                            id="tools_log",
                            wrap=True,
                            markup=True,
                            auto_scroll=True,
                        )
                    with TabPane("Stats", id="stats-pane"):
                        yield Static("", id="stats")
        yield Static(
            "Ctrl+C cancel turn | Ctrl+L clear panes | F2 reasoning | F3 tools",
            id="hints",
        )
        yield Input(
            placeholder="Type a message or /command",
            id="prompt_input",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#prompt_input", Input).focus()
        self._write_transcript_line(
            "system",
            "fleet-rlm code-chat (Textual mode). Type /help for commands.",
        )
        self.set_interval(
            max(10, self.config.stream_refresh_ms) / 1000, self._on_refresh_tick
        )
        self._update_topbar()
        self._update_stats()

    def _configure_logging(self) -> None:
        from loguru import logger

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.remove()
        logger.add(self.log_path, rotation="10 MB", retention=5, level="INFO")

    @property
    def _transcript_log(self) -> RichLog:
        return self.query_one("#transcript", RichLog)

    @property
    def _reasoning_log(self) -> RichLog:
        return self.query_one("#reasoning_log", RichLog)

    @property
    def _tools_log(self) -> RichLog:
        return self.query_one("#tools_log", RichLog)

    @property
    def _live_stream(self) -> Static:
        return self.query_one("#live_stream", Static)

    @property
    def _stats(self) -> Static:
        return self.query_one("#stats", Static)

    def _queue_transcript(self, event: TranscriptEvent) -> None:
        self._transcript_batch.append(event)

    def _flush_transcript_batch(self) -> None:
        if not self._transcript_batch:
            return
        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            event.model_dump_json(ensure_ascii=False) + "\n"
            for event in self._transcript_batch
        ]
        with self.transcript_path.open("a", encoding="utf-8") as handle:
            handle.writelines(lines)
        self._transcript_batch.clear()

    def _on_refresh_tick(self) -> None:
        try:
            self._drain_events()
            self._flush_transcript_batch()
            self._update_topbar()
        except NoMatches:
            # Screen is shutting down while timer callback is still in-flight.
            return

    def _write_transcript_line(self, role: str, message: str) -> None:
        """Write a styled message to the main chat transcript."""
        if role == "user":
            label = RichText.from_markup("[bold cyan]you>[/bold cyan] ")
            label.append(message)
            self._transcript_log.write(label)
        elif role == "assistant":
            self._transcript_log.write(
                RichText.from_markup("[bold green]assistant>[/bold green]")
            )
            self._transcript_log.write(RichMarkdown(message))
        elif role == "error":
            label = RichText.from_markup(f"[bold red]error>[/bold red] {message}")
            self._transcript_log.write(label)
        elif role == "system":
            label = RichText.from_markup(f"[dim]{role}>[/dim] {message}")
            self._transcript_log.write(label)
        elif role == "help":
            label = RichText.from_markup(f"[italic]{message}[/italic]")
            self._transcript_log.write(label)
        else:
            self._transcript_log.write(f"{role}> {message}")

    def _write_data(self, title: str, payload: Any) -> None:
        """Write structured data to the reasoning panel (not the main chat)."""
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        self._reasoning_log.write(f"--- {title} ---")
        self._reasoning_log.write(body)

    def _update_topbar(self) -> None:
        model = os.getenv("DSPY_LM_MODEL", "unknown")
        state = "running" if self._in_flight else "idle"
        latency = (
            time.monotonic() - self._turn_start
            if self._in_flight
            else self._last_turn_latency
        )
        topbar = self.query_one("#topbar", Static)
        topbar.update(
            " | ".join(
                [
                    f"state={state}",
                    f"model={model}",
                    f"profile={self.config.profile_name}",
                    f"trace={self.trace_mode}",
                    f"stream={self.stream_enabled}",
                    f"turns={self._turn_number}",
                    f"latency={latency:.2f}s",
                ]
            )
        )

    def _update_stats(self) -> None:
        state = self._turn_state or TurnState()
        stats = {
            "turns": self._turn_number,
            "stream_enabled": self.stream_enabled,
            "trace_mode": self.trace_mode,
            "in_flight": self._in_flight,
            "last_turn_latency_s": round(self._last_turn_latency, 3),
            "token_count": state.token_count,
            "status_count": len(state.status_messages),
            "tool_events": len(state.tool_timeline),
            "cancelled": state.cancelled,
            "errored": state.errored,
        }
        self._stats.update(json.dumps(stats, ensure_ascii=False, indent=2))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        event.input.value = ""
        if not raw:
            return

        if self._python_mode:
            self._handle_python_input(raw)
            return

        if raw.startswith("/"):
            self._handle_command(raw)
            return

        if self._in_flight:
            self._write_transcript_line(
                "system",
                "A turn is already running. Press Ctrl+C to cancel it first.",
            )
            return

        self._start_turn(raw)

    def _start_turn(self, message: str) -> None:
        self._in_flight = True
        self._cancel_requested = False
        self._turn_number += 1
        self._turn_start = time.monotonic()
        self._turn_state = TurnState()
        self._last_token_flush = time.monotonic()
        self._live_stream.update("")
        self._write_transcript_line("user", message)
        self._queue_transcript(TranscriptEvent(role="user", content=message))
        self._stream_turn_worker(message)

    @work(thread=True, exclusive=True)
    def _stream_turn_worker(self, message: str) -> None:
        try:
            if not self.stream_enabled:
                if self._cancel_requested:
                    self._event_queue.put(
                        StreamEvent(kind="cancelled", text="[cancelled]", payload={})
                    )
                    return
                result = self.agent.chat_turn(message)
                self._event_queue.put(
                    StreamEvent(
                        kind="final",
                        text=str(result.get("assistant_response", "")),
                        payload={
                            "trajectory": result.get("trajectory", {}),
                            "history_turns": result.get(
                                "history_turns", len(self.agent.history.messages)
                            ),
                        },
                    )
                )
                return

            trace_verbose = self.trace_mode == "verbose"
            for stream_event in self.agent.iter_chat_turn_stream(
                message=message,
                trace=trace_verbose,
                cancel_check=lambda: self._cancel_requested,
            ):
                if (
                    stream_event.kind == "reasoning_step"
                    and self.trace_mode != "verbose"
                ):
                    continue
                self._event_queue.put(stream_event)
        except Exception as exc:
            self._event_queue.put(StreamEvent(kind="error", text=str(exc)))

    def _drain_events(self) -> None:
        if self._turn_state is None:
            self._turn_state = TurnState()

        token_update = False
        terminal_seen = False
        while True:
            try:
                event = self._event_queue.get_nowait()
            except queue.Empty:
                break

            self._turn_state.apply(event)
            if event.kind == "assistant_token":
                token_update = True
            elif event.kind in {"status", "reasoning_step"}:
                self._reasoning_log.write(event.text)
            elif event.kind in {"tool_call", "tool_result"}:
                self._tools_log.write(event.text)
            elif event.kind == "error":
                self._write_transcript_line("error", event.text)
                self._queue_transcript(
                    TranscriptEvent(role="system", content=f"error: {event.text}")
                )
                terminal_seen = True
            elif event.kind in {"final", "cancelled"}:
                terminal_seen = True

        now = time.monotonic()
        if token_update and (now - self._last_token_flush >= 0.02 or terminal_seen):
            self._live_stream.update(self._turn_state.transcript_text)
            self._last_token_flush = now

        if terminal_seen:
            self._finalize_turn()

        self._update_stats()

    def _finalize_turn(self) -> None:
        if not self._in_flight:
            return

        state = self._turn_state or TurnState()
        output = state.final_text or state.transcript_text
        marker = ""
        if state.cancelled:
            marker = " [cancelled]"
        elif state.errored:
            marker = " [error]"

        if marker and marker not in output:
            output = f"{output}{marker}".strip()

        if output:
            self._write_transcript_line("assistant", output)
            self._queue_transcript(TranscriptEvent(role="assistant", content=output))
        else:
            self._write_transcript_line("assistant", marker.strip() or "<empty>")

        # Route trajectory data to reasoning/tools panels instead of main chat
        if self.trace_mode != "off" and state.trajectory:
            self._route_trajectory_to_panels(state.trajectory)
            self._queue_transcript(
                TranscriptEvent(role="trace", payload={"trajectory": state.trajectory})
            )

        # Clear the live stream area now that the turn is done
        self._live_stream.update("")

        self._last_turn_latency = max(0.0, time.monotonic() - self._turn_start)
        self._in_flight = False
        self._cancel_requested = False
        self._turn_start = 0.0

    def _route_trajectory_to_panels(self, trajectory: dict[str, Any]) -> None:
        """Parse dspy.ReAct trajectory and route to reasoning/tools panels.

        Trajectory keys follow the pattern ``{field}_{step}`` where step is
        a zero-based integer.  Thoughts and observations go to the reasoning
        log; tool names, args, and outputs go to the tools log.
        """
        # Determine how many steps exist by scanning for numbered keys
        step_indices: set[int] = set()
        step_pat = re.compile(
            r"^(?:next_thought|thought|tool_name|tool_args|observation|tool_output)_(\d+)$"
        )
        for key in trajectory:
            m = step_pat.match(key)
            if m:
                step_indices.add(int(m.group(1)))

        for step in sorted(step_indices):
            # Thoughts → reasoning panel
            thought = trajectory.get(f"next_thought_{step}") or trajectory.get(
                f"thought_{step}"
            )
            if thought:
                self._reasoning_log.write(f"[step {step}] thought: {thought}")

            # Tool calls → tools panel
            tool_name = trajectory.get(f"tool_name_{step}")
            tool_args = trajectory.get(f"tool_args_{step}")
            if tool_name and tool_name != "finish":
                args_str = (
                    json.dumps(tool_args, ensure_ascii=False, default=str)
                    if tool_args
                    else "{}"
                )
                self._tools_log.write(f"[step {step}] call: {tool_name}({args_str})")

            # Observations → reasoning panel
            observation = trajectory.get(f"observation_{step}")
            if observation:
                obs_text = (
                    observation
                    if isinstance(observation, str)
                    else json.dumps(observation, ensure_ascii=False, default=str)
                )
                # Truncate very long observations for readability
                if len(obs_text) > 500:
                    obs_text = obs_text[:500] + "..."
                self._reasoning_log.write(f"[step {step}] observation: {obs_text}")

            # Tool output → tools panel
            tool_output = trajectory.get(f"tool_output_{step}")
            if tool_output:
                out_text = (
                    tool_output
                    if isinstance(tool_output, str)
                    else json.dumps(tool_output, ensure_ascii=False, default=str)
                )
                if len(out_text) > 500:
                    out_text = out_text[:500] + "..."
                self._tools_log.write(f"[step {step}] result: {out_text}")

    def _handle_command(self, raw: str) -> None:
        parts = shlex.split(raw[1:])
        if not parts:
            return

        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "exit":
            self._write_transcript_line("system", "Exiting code-chat.")
            self.exit()
            return
        if cmd == "help":
            self._show_help()
            return
        if cmd == "history":
            self._write_data("history", {"messages": self.agent.history.messages})
            return
        if cmd == "reset":
            self._write_data("reset", self.agent.reset(clear_sandbox_buffers=True))
            return
        if cmd == "tools":
            tool_names = [
                getattr(tool, "__name__", str(tool)) for tool in self.agent.react_tools
            ]
            self._write_data("tools", {"tools": tool_names})
            return
        if cmd == "load":
            if len(args) < 1:
                self._write_transcript_line("error", "Usage: /load <path>")
                return
            self._write_data("load", self.agent.load_document(args[0], alias="active"))
            return
        if cmd == "docs":
            self._write_data("documents", self.agent.list_documents())
            return
        if cmd == "trace":
            self._set_trace_mode(args)
            return
        if cmd == "stream":
            if len(args) != 1 or args[0] not in {"on", "off"}:
                self._write_transcript_line("error", "Usage: /stream on|off")
                return
            self.stream_enabled = args[0] == "on"
            self._write_transcript_line("system", f"stream={self.stream_enabled}")
            return
        if cmd == "clear":
            self.action_clear_panes()
            return
        if cmd == "profile":
            if len(args) == 0 or args[0] == "show":
                self._write_data("profile", get_profile().model_dump())
                return
            if args[0] == "set" and len(args) == 2:
                profile = set_active_profile(args[1])
                self._write_data(
                    "profile",
                    {
                        "active_profile": profile.name,
                        "note": "Profile updated. Restart session to fully apply settings.",
                    },
                )
                return
            self._write_transcript_line("error", "Usage: /profile show|set <name>")
            return
        if cmd == "py":
            self._python_mode = True
            self._python_lines = []
            self._write_transcript_line(
                "system",
                "Python input mode enabled. Enter code lines and submit ':end' to execute.",
            )
            return
        if cmd == "rg":
            if len(args) < 1:
                self._write_transcript_line("error", "Usage: /rg <pattern> [path]")
                return
            search_path = args[1] if len(args) > 1 else "."
            self._write_data("rg", self._run_rg(args[0], search_path))
            return
        if cmd == "save-buffer":
            if len(args) != 2:
                self._write_transcript_line(
                    "error", "Usage: /save-buffer <name> <path>"
                )
                return
            self._write_data(
                "save-buffer", self.agent.save_buffer_to_volume(args[0], args[1])
            )
            return
        if cmd == "load-volume":
            if len(args) < 1:
                self._write_transcript_line(
                    "error", "Usage: /load-volume <path> [alias]"
                )
                return
            alias = args[1] if len(args) > 1 else "active"
            self._write_data(
                "load-volume", self.agent.load_text_from_volume(args[0], alias=alias)
            )
            return

        self._write_transcript_line("error", f"Unknown command: /{cmd}. Type /help.")

    def _set_trace_mode(self, args: list[str]) -> None:
        if len(args) != 1:
            self._write_transcript_line("error", "Usage: /trace compact|verbose|off")
            return

        raw = args[0].lower()
        if raw == "on":
            self.trace_mode = "compact"
        elif raw == "off":
            self.trace_mode = "off"
        elif raw in {"compact", "verbose"}:
            self.trace_mode = cast(TraceMode, raw)
        else:
            self._write_transcript_line("error", "Usage: /trace compact|verbose|off")
            return

        self._write_transcript_line("system", f"trace_mode={self.trace_mode}")

    def _show_help(self) -> None:
        commands = [
            "/exit - exit session",
            "/help - show this help",
            "/history - show chat history",
            "/reset - clear history + sandbox buffers",
            "/tools - list ReAct tools",
            "/load <path> - load document into active context",
            "/docs - show loaded document aliases",
            "/trace compact|verbose|off - set reasoning detail level",
            "/stream on|off - toggle streaming",
            "/clear - clear visible panes",
            "/profile show|set <name> - show current or switch active profile",
            "/py - execute multiline python in Modal interpreter (end with :end)",
            "/rg <pattern> [path] - ripgrep code/doc search",
            "/save-buffer <name> <path> - persist buffer to volume",
            "/load-volume <path> [alias] - load text file from volume",
        ]
        for line in commands:
            self._write_transcript_line("help", line)

    def _handle_python_input(self, raw: str) -> None:
        if raw.strip() == ":end":
            self._python_mode = False
            code = "\n".join(self._python_lines).strip()
            self._python_lines = []
            if not code:
                self._write_transcript_line("system", "No code provided.")
                return
            result = self.agent.interpreter.execute(code)
            if isinstance(result, FinalOutput):
                self._write_data("python-result", result.output)
            else:
                self._write_data("python-output", {"output": str(result)})
            return

        self._python_lines.append(raw)

    def _run_rg(self, pattern: str, path: str) -> dict[str, Any]:
        from ripgrepy import Ripgrepy

        rg = Ripgrepy(pattern, path).json().with_filename().line_number().max_count(50)
        out = rg.run()
        hits = []
        for item in out.as_dict:
            if item.get("type") != "match":
                continue
            data = item.get("data", {})
            path_text = data.get("path", {}).get("text", "")
            line_no = data.get("line_number")
            line_text = data.get("lines", {}).get("text", "").rstrip("\n")
            hits.append({"path": path_text, "line": line_no, "text": line_text})
        return {"pattern": pattern, "path": path, "count": len(hits), "hits": hits[:20]}

    def action_cancel_turn(self) -> None:
        if not self._in_flight:
            return
        self._cancel_requested = True
        self._write_transcript_line(
            "system", "Cancellation requested for current turn."
        )

    def action_clear_panes(self) -> None:
        self._transcript_log.clear()
        self._reasoning_log.clear()
        self._tools_log.clear()
        self._live_stream.update("")

    def action_toggle_reasoning(self) -> None:
        pane = self.query_one("#reasoning-pane", TabPane)
        pane.display = not pane.display

    def action_toggle_tools(self) -> None:
        pane = self.query_one("#tools-pane", TabPane)
        pane.display = not pane.display


def run_code_chat_textual_app(
    *, agent: RLMReActChatAgent, config: SessionConfig
) -> None:
    """Launch Textual interactive app."""
    CodeChatTextualApp(agent=agent, config=config).run()
