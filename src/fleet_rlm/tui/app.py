"""The interactive terminal client: Textual chat TUI with collapsible REPL cards and bottom composer."""

from __future__ import annotations

import io
import json
import sys
import threading
import time
import uuid
from collections.abc import Callable, Generator, Iterable
from contextlib import AbstractContextManager, contextmanager, nullcontext, suppress
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote

import httpx
from prompt_toolkit.completion import (
    CompleteEvent,
    Completer,
    Completion,
    NestedCompleter,
    PathCompleter,
    WordCompleter,
    merge_completers,
)
from prompt_toolkit.document import Document
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static, TextArea

import fleet_rlm.tui.commands as commands_mod
import fleet_rlm.tui.render as render_mod
from fleet_rlm.config import FleetSettings
from fleet_rlm.tui.commands import (
    COMMANDS,
    MODES,
    Context,
    SessionState,
    apply_model_choice,
    completion_options,
    handle_command,
)
from fleet_rlm.tui.render import (
    CONSOLE,
    CollapsibleItem,
    TurnTracker,
    _extract_http_detail,
    _format_tool_payload,
    _is_omitted_payload,
    confidence_bar_text,
    print_http_error,
    render_event,
)
from fleet_rlm.workspace import validate_session_id

_LOCAL_PATH_COMMANDS = frozenset({"/attach", "/put"})
_WORKSPACE_PATH_COMMANDS = frozenset({"/cat", "/stat", "/get", "/rm", "/context"})
_GRADIENT_STOPS = (
    (82, 82, 91),  # zinc
    (161, 161, 170),  # silver
    (52, 211, 153),  # emerald
    (251, 191, 36),  # amber
    (161, 161, 170),  # silver
    (82, 82, 91),  # zinc
)


class _SessionCompleter(Completer):
    """Combine slash-command completion with local file and workspace path completion."""

    def __init__(self, base: Completer, state: SessionState | None = None) -> None:
        self._base = base
        self._state = state
        self._path_completer = PathCompleter(expanduser=True)

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Iterable[Completion]:
        """Yield command completions, local paths, or cached workspace file paths when input starts with `/`."""
        text = document.text_before_cursor
        stripped = text.lstrip()
        if not stripped.startswith("/"):
            return
        if " " in stripped:
            cmd, _, rest = stripped.partition(" ")
            arg_text = rest.lstrip()
            if cmd in _LOCAL_PATH_COMMANDS and " " not in arg_text:
                sub_doc = Document(arg_text, cursor_position=len(arg_text))
                yield from self._path_completer.get_completions(sub_doc, complete_event)
                return
            if cmd in _WORKSPACE_PATH_COMMANDS and " " not in arg_text and self._state is not None:
                candidates = list(self._state.workspace_files)
                if cmd == "/context" and "clear" not in candidates:
                    candidates.insert(0, "clear")
                for candidate in candidates:
                    if candidate.startswith(arg_text):
                        yield Completion(candidate, start_position=-len(arg_text))
                if candidates:
                    return
        yield from self._base.get_completions(document, complete_event)


def _completer(state: SessionState | None = None) -> Completer:
    """Build the slash-command and argument completer."""
    arguments = NestedCompleter.from_nested_dict(completion_options())
    base = merge_completers([WordCompleter(list(arguments.options), sentence=True), arguments], deduplicate=True)
    return _SessionCompleter(base, state=state)


def _parse_sse_line(line: str, state: SessionState) -> tuple[dict[str, Any] | None, bool]:
    """Parse one `data:` SSE line and sync `state`; return `(event, malformed)`."""
    if not line.startswith("data:"):
        return None, False
    try:
        event = json.loads(line[5:].lstrip())
    except json.JSONDecodeError:
        return None, True
    if not isinstance(event, dict) or not isinstance(event.get("payload", {}), dict):
        return None, True
    if event.get("turn_id"):
        state.last_turn_id = str(event["turn_id"])
    payload = {**event, **event.get("payload", {})}
    if payload.get("trace_id"):
        state.last_trace_id = str(payload["trace_id"])
    if payload.get("event") == "triage_decision" and payload.get("mode"):
        state.mode = str(payload["mode"])
    return event, False


def _request_turn(
    client: httpx.Client, session_id: str, task: str, state: SessionState
) -> AbstractContextManager[httpx.Response]:
    """Open the shared Turn HTTP contract; the caller owns response lifetime and cancellation."""
    return client.stream(
        "POST",
        f"/sessions/{quote(session_id, safe='')}/turn",
        json={"task": task, "idempotency_key": uuid.uuid4().hex, "record_replay": state.record_replay},
    )


def _turn_events(
    response: httpx.Response,
    state: SessionState,
    tracker: TurnTracker,
    *,
    lock: AbstractContextManager[Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Generator[dict[str, Any] | None, None, None]:
    """Update shared Turn state from SSE, yielding events or None for malformed data lines."""
    for line in response.iter_lines():
        if cancelled is not None and cancelled():
            break
        with lock if lock is not None else nullcontext():
            event, malformed = _parse_sse_line(line, state)
            if event is not None:
                tracker.ingest(event)
        if malformed:
            yield None
        elif event is not None:
            yield event


def _interpolate_gradient(width: int) -> Text:
    """Build a 1-line smooth RGB gradient rule across `width` columns."""
    w = max(20, width)
    line = Text()
    segments = len(_GRADIENT_STOPS) - 1
    for col in range(w):
        pos = (col / max(1, w - 1)) * segments
        idx = min(segments - 1, int(pos))
        frac = pos - idx
        r1, g1, b1 = _GRADIENT_STOPS[idx]
        r2, g2, b2 = _GRADIENT_STOPS[idx + 1]
        r = int(r1 + (r2 - r1) * frac)
        g = int(g1 + (g2 - g1) * frac)
        b = int(b1 + (b2 - b1) * frac)
        line.append("━", style=f"rgb({r},{g},{b})")
    return line


class _GradientRule(Static):
    """Top 1-line gradient accent bar."""

    def render(self) -> RenderableType:
        width = self.size.width or 100
        return _interpolate_gradient(width)


class _UserPromptCard(Static):
    """Elevated dark card displaying a submitted user prompt or command."""

    def __init__(self, prompt_text: str) -> None:
        super().__init__()
        self.prompt_text = prompt_text

    def render(self) -> RenderableType:
        text = Text()
        text.append("\u203a ", style="bold #a1a1aa")
        text.append(self.prompt_text, style="#f4f4f5")
        return text


class _PromptInput(TextArea):
    """Multi-line prompt composer that preserves multi-paragraph pastes and submits on Enter."""

    def __init__(self, *, placeholder: str = "", id: str | None = None) -> None:  # noqa: A002
        super().__init__(
            "",
            placeholder=placeholder,
            show_line_numbers=False,
            soft_wrap=True,
            tab_behavior="focus",
            id=id,
        )

    @property
    def value(self) -> str:
        """Return the full multi-line prompt text."""
        return self.text

    @value.setter
    def value(self, text: str) -> None:
        """Replace the prompt text, move the cursor to the end, and sync composer state."""
        self.load_text(text)
        self.move_cursor(self.document.end)
        app = self.app
        if isinstance(app, FleetTextualApp) and app.is_running:
            app._sync_input_state(text)

    async def _on_paste(self, event: events.Paste) -> None:
        """Insert the full pasted text (including all paragraphs and line breaks) at the cursor."""
        event.stop()
        event.prevent_default()
        if event.text and not self.read_only:
            self.insert(event.text)

    async def _on_key(self, event: events.Key) -> None:
        """Submit on Enter; insert newline on Shift+Enter, Alt+Enter, or Ctrl+J; navigate menu/history."""
        app = self.app
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            if isinstance(app, FleetTextualApp):
                text = self.text.strip()
                self.value = ""
                app.query_one("#slash-popup", Static).display = False
                app.submit_text(text)
            return
        if event.key in {"shift+enter", "alt+enter", "ctrl+j"}:
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if isinstance(app, FleetTextualApp):
            popup_open = app.query_one("#slash-popup", Static).display and bool(app._popup_items)
            if event.key == "up" and (popup_open or self.cursor_location[0] == 0):
                event.stop()
                event.prevent_default()
                app.action_cursor_up()
                return
            if event.key == "down" and (popup_open or self.cursor_location[0] >= self.document.line_count - 1):
                event.stop()
                event.prevent_default()
                app.action_cursor_down()
                return
        await super()._on_key(event)


class _StepWidget(Static):
    """Clickable collapsible widget for one REPL cell, Thinking block, or Tool invocation."""

    def __init__(self, item: CollapsibleItem, tracker: TurnTracker, on_toggle: Callable[[], None]) -> None:
        super().__init__()
        self.item = item
        self.tracker = tracker
        self._on_toggle = on_toggle

    def on_click(self, event: events.Click) -> None:
        """Toggle this step open or closed on left mouse click."""
        event.stop()
        app = self.app
        if isinstance(app, FleetTextualApp):
            with app._lock:
                self.tracker.toggle_item(self.item.item_id)
        else:
            self.tracker.toggle_item(self.item.item_id)
        self.refresh(layout=True)
        self._on_toggle()

    def render(self) -> RenderableType:
        item = self.item
        arrow = "▾" if item.expanded else "▸"
        header = Text()
        if item.error:
            arrow_color = "bold #f87171"
        elif item.status in {"running", "cancelled", "interrupted"}:
            arrow_color = "bold #fbbf24"
        else:
            arrow_color = "bold #a1a1aa"
        header.append(f"{arrow} ", style=arrow_color)

        if item.kind == "repl":
            label = f"Python #{item.index}" if item.phase == "task" else "Bootstrap"
            header.append(label, style="bold #34d399" if item.phase == "task" else "#71717a")
            header.append(f" · {item.agent_label}", style="#a1a1aa")
            if item.namespace:
                header.append(f" · {item.namespace}", style="#52525b")
        elif item.kind == "thinking":
            header.append(f"Thinking #{item.index}", style="bold #d4d4d8")
            header.append(f" · {item.agent_label}", style="#71717a")
        else:
            header.append(f"Tool · {item.title}", style="bold #e4e4e7")
            header.append(f" · {item.agent_label}", style="#71717a")

        if item.status == "running":
            header.append("   running…", style="#fbbf24")
        elif item.status in {"cancelled", "interrupted"}:
            header.append(f"   {item.status}", style="#fbbf24")
        elif item.error:
            err = f" ({item.error_type})" if item.error_type else ""
            header.append(f"   failed{err}", style="bold #f87171")
        else:
            header.append("   ✓", style="#34d399" if item.kind == "repl" else "#71717a")

        if item.duration_ms is not None:
            header.append(f" {item.duration_ms} ms", style="#71717a")
        if item.usage_text:
            header.append(f" · {item.usage_text}", style="#71717a")

        if not item.expanded and item.summary:
            header.append(f"   {item.summary}", style="#52525b")

        if not item.expanded:
            return header

        parts: list[RenderableType] = [header]
        if item.kind == "repl":
            if item.code:
                parts.append(
                    Panel(
                        Syntax(
                            item.code[:20000],
                            "python",
                            theme="monokai",
                            background_color="default",
                            line_numbers=True,
                            word_wrap=True,
                        ),
                        title=f"Python · Daytona · {item.phase}",
                        title_align="left",
                        border_style="#3f3f46" if not item.error else "#f87171",
                        padding=(0, 1),
                    )
                )
            if item.status != "running" or item.stdout or item.stderr or item.value:
                out_text = item.stdout + item.stderr
                if item.value not in (None, "null", ""):
                    out_text += ("\n" if out_text else "") + f"Return value: {item.value}"
                status_label = (
                    f"failed ({item.error_type or 'Python error'})"
                    if item.error
                    else ("running" if item.status == "running" else "completed")
                )
                duration_part = f" · {item.duration_ms} ms" if item.duration_ms is not None else ""
                parts.append(
                    Panel(
                        Text(out_text[-20000:] or "(no output)", style="#e4e4e7"),
                        title=f"Output · {status_label}{duration_part}",
                        title_align="left",
                        border_style="#f87171" if item.error else "#3f3f46",
                        padding=(0, 1),
                    )
                )
        elif item.kind == "thinking":
            parts.append(
                Panel(
                    Text(item.reasoning, style="#a1a1aa"),
                    title=f"Thinking · {item.agent_label}",
                    title_align="left",
                    border_style="#27272a",
                    padding=(0, 1),
                )
            )
        elif item.kind == "tool":
            if item.tool_inputs and not _is_omitted_payload(item.tool_inputs):
                parts.append(
                    Panel(
                        Text(_format_tool_payload(item.tool_inputs), style="#d4d4d8"),
                        title="Arguments",
                        title_align="left",
                        border_style="#27272a",
                        padding=(0, 1),
                    )
                )
            if item.tool_output and not _is_omitted_payload(item.tool_output):
                parts.append(
                    Panel(
                        Text(_format_tool_payload(item.tool_output), style="#e4e4e7"),
                        title="Result",
                        title_align="left",
                        border_style="#f87171" if item.error else "#27272a",
                        padding=(0, 1),
                    )
                )
        return Group(*parts)


class _TurnWidget(Vertical):
    """Reactive container for one Turn's routing badge, collapsible steps, answer prose, and status."""

    def __init__(self, tracker: TurnTracker) -> None:
        super().__init__(classes="turn-container")
        self.tracker = tracker
        self._header_static = Static(classes="turn-meta")
        self._steps_box = Vertical(classes="turn-steps")
        self._answer_static = Static(classes="turn-answer")
        self._footer_static = Static(classes="turn-footer")
        self._mounted_step_ids: list[str] = []

    def compose(self) -> ComposeResult:
        yield self._header_static
        yield self._steps_box
        yield self._answer_static
        yield self._footer_static

    def on_mount(self) -> None:
        self.sync_from_tracker()

    def sync_from_tracker(self) -> None:
        """Synchronize child widgets with `self.tracker` state."""
        t = self.tracker
        meta_lines: list[RenderableType] = []
        if t.mode or t.route:
            badge = Text()
            badge.append(f" {t.route or t.mode} ", style="bold #f4f4f5 on #27272a")
            if t.mode and t.route:
                badge.append(f"  mode={t.mode}", style="#71717a")
            if t.route_override:
                badge.append(f" · override={t.route_override}", style="#fbbf24")
            if t.route_confidence is not None:
                badge.append(" · confidence ", style="#71717a")
                badge.append_text(confidence_bar_text(t.route_confidence))
            if t.preloaded_skills is not None:
                skills_str = ", ".join(t.preloaded_skills) or "none"
                badge.append(f" · skills: {skills_str}", style="#34d399")
            meta_lines.append(badge)
            if t.route_summary:
                meta_lines.append(Text(f"{t.route_summary}", style="#71717a"))

        if meta_lines:
            self._header_static.update(Group(*meta_lines))
            self._header_static.display = True
        else:
            self._header_static.display = False

        current_ids = [item.item_id for item in t.items]
        if current_ids != self._mounted_step_ids:
            for item in t.items[len(self._mounted_step_ids) :]:
                self._steps_box.mount(_StepWidget(item, t, self.sync_from_tracker))
            self._mounted_step_ids = current_ids
        for child in self._steps_box.children:
            if isinstance(child, _StepWidget):
                child.refresh(layout=True)

        body_parts: list[RenderableType] = list(t.notices)
        if t.terminal_event is not None:
            ev = t.terminal_event
            ev_type = ev.get("event")
            if ev_type == "turn_complete":
                answer_text = str(ev.get("answer", ""))
                prefix = "\n" if body_parts else ""
                body_parts.append(Text(prefix + answer_text, style="#f4f4f5"))
                if ev.get("learned_skill_note"):
                    note = Text()
                    note.append("\nNote: ", style="bold #a1a1aa")
                    note.append(str(ev["learned_skill_note"]), style="#a1a1aa")
                    body_parts.append(note)
                if ev.get("artifacts"):
                    art = Text()
                    art.append("\nArtifacts: ", style="bold #fbbf24")
                    art.append(", ".join(str(a) for a in ev["artifacts"]), style="#d4d4d8")
                    body_parts.append(art)
                body_parts.append(Text(f"\n{render_mod.TURN_PERSISTED}", style="#34d399"))
            elif ev_type == "turn_cancelled":
                body_parts.append(
                    Text(f"Turn cancelled: {ev.get('error', 'Cancellation confirmed.')}", style="bold #fbbf24")
                )
            elif ev_type == "error":
                body_parts.append(Text(f"Turn failed: {ev.get('error', 'Unknown error')}", style="bold #f87171"))

        if body_parts:
            self._answer_static.update(Group(*body_parts))
            self._answer_static.display = True
        else:
            self._answer_static.display = False

        footer = t.status_line()
        if t.terminal_event is not None and t.terminal_event.get("trace_id"):
            footer.append(f" · trace {t.terminal_event['trace_id']}", style="#71717a")
        self._footer_static.update(footer)


class FleetTextualApp(App[None]):
    """Full-screen Textual chat TUI with bottom-docked composer, status pills, and collapsible REPL steps."""

    CSS: ClassVar[str] = """
    Screen {
        background: ansi_default;
        color: #e4e4e7;
    }
    #top-gradient {
        height: 1;
        dock: top;
    }
    #chat-scroll {
        height: 1fr;
        padding: 1 2 0 2;
        background: ansi_default;
        scrollbar-color: #3f3f46;
        scrollbar-background: ansi_default;
        scrollbar-size-vertical: 1;
    }
    .welcome-banner {
        color: #8b93a6;
        padding: 0 1 1 1;
        border-bottom: solid #27272a;
        margin-bottom: 1;
    }
    _UserPromptCard {
        background: #232630;
        color: #f4f4f5;
        padding: 1 2;
        margin: 1 0;
        border-left: thick #52525b;
    }
    .cmd-output {
        background: ansi_default;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    .turn-container {
        height: auto;
        margin: 0 0 1 0;
        padding: 0 1;
    }
    .turn-meta {
        height: auto;
        margin: 0 0 1 0;
    }
    .turn-steps {
        height: auto;
        margin: 0;
    }
    _StepWidget {
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    _StepWidget:hover {
        background: #1f222b;
    }
    .turn-answer {
        height: auto;
        padding: 0 1;
        margin: 1 0 1 0;
    }
    .turn-footer {
        height: auto;
        color: #71717a;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    #slash-popup {
        display: none;
        height: auto;
        max-height: 9;
        margin: 0 1;
        padding: 0 1;
        background: #1f222b;
        border: round #3f3f46;
    }
    #bottom-chrome {
        height: auto;
        dock: bottom;
        background: ansi_default;
        padding: 0 1;
    }
    #composer-box {
        height: auto;
        min-height: 3;
        max-height: 10;
        border: tall #3f3f46;
        background: ansi_default;
        padding: 0 1;
        margin: 0 0;
        align: left top;
    }
    #composer-box:focus-within {
        border: tall #71717a;
    }
    #prompt-glyph {
        width: 2;
        height: 1;
        color: #a1a1aa;
        text-style: bold;
    }
    #prompt-input {
        width: 1fr;
        height: 1;
        min-height: 1;
        max-height: 8;
        border: none;
        background: transparent;
        color: #f4f4f5;
        padding: 0;
        scrollbar-color: #3f3f46;
        scrollbar-background: ansi_default;
        scrollbar-size-vertical: 1;
    }
    #prompt-input:focus {
        border: none;
    }
    #prompt-input .text-area--cursor-line {
        background: transparent;
    }
    #composer-hints {
        width: auto;
        height: 1;
        color: #71717a;
        padding-left: 1;
    }
    #status-row {
        height: 1;
        margin: 0 0;
        align: left middle;
    }
    #status-left {
        width: 1fr;
    }
    #status-right {
        width: auto;
    }
    #shortcut-bar {
        height: 1;
        margin: 0 0;
        color: #71717a;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("tab", "complete_slash", "Complete", show=False, priority=True),
        Binding("ctrl+o", "toggle_latest_repl", "Toggle REPL", show=False, priority=True),
        Binding("ctrl+t", "toggle_thinking", "Toggle Thinking", show=False, priority=True),
        Binding("alt+a", "toggle_all", "Toggle All", show=False, priority=True),
        Binding("ctrl+k", "cycle_mode", "Cycle Mode", show=False, priority=True),
        Binding("escape", "dismiss_or_cancel", "Cancel/Dismiss", show=False, priority=True),
        Binding("ctrl+c", "clear_or_cancel", "Clear/Cancel", show=False, priority=True),
        Binding("ctrl+d", "quit_if_idle", "Quit", show=False, priority=True),
    ]

    def __init__(self, client: httpx.Client, session_id: str, state: SessionState) -> None:
        super().__init__(ansi_color=True)
        self.client = client
        self.session_id = session_id
        self.state = state
        self.state.fullscreen = True
        self._lock = threading.Lock()
        self._turn_active: bool = False
        self._cancel_requested: bool = False
        self._active_response: httpx.Response | None = None
        self._active_turn_widget: _TurnWidget | None = None
        self._history_entries: list[str] = []
        self._history_index: int = 0
        self._popup_items: list[tuple[str, str, str]] = []
        self._popup_index: int = 0
        self._last_ctrl_c: float = 0.0
        self._load_history()

    def _load_history(self) -> None:
        history_path = Path(".fleet/history") / f"{self.session_id}.txt"
        if history_path.is_file():
            with suppress(OSError):
                entries: list[str] = []
                current: list[str] = []
                for line in history_path.read_text(errors="replace").splitlines():
                    if line.startswith("+"):
                        current.append(line[1:])
                    elif current:
                        entry = "\n".join(current).strip()
                        if entry:
                            entries.append(entry)
                        current = []
                if current:
                    entry = "\n".join(current).strip()
                    if entry:
                        entries.append(entry)
                self._history_entries = entries[-200:]
                self._history_index = len(self._history_entries)

    def _append_history(self, text: str) -> None:
        self._history_entries.append(text)
        self._history_index = len(self._history_entries)
        history_path = Path(".fleet/history") / f"{self.session_id}.txt"
        with suppress(OSError):
            history_path.parent.mkdir(parents=True, exist_ok=True)
            encoded = "\n".join(f"+{line}" for line in text.splitlines())
            with history_path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n# {time.strftime('%Y-%m-%d %H:%M:%S')}\n{encoded}\n")

    def compose(self) -> ComposeResult:
        """Assemble the top gradient rule, scrollable chat viewport, popup, and bottom composer."""
        yield _GradientRule(id="top-gradient")
        with VerticalScroll(id="chat-scroll"):
            welcome = Text()
            welcome.append("Fleet-RLM Lite", style="bold #f4f4f5")
            welcome.append(f"  ·  Session {self.session_id}\n", style="#a1a1aa")
            welcome.append(
                "Type a task below or `/` for commands. Click any `▸` step or press `Ctrl+O` "
                "to expand Daytona Python runs.",
                style="#71717a",
            )
            yield Static(welcome, classes="welcome-banner")
        yield Static(id="slash-popup")
        with Vertical(id="bottom-chrome"):
            with Horizontal(id="composer-box"):
                yield Static("\u203a ", id="prompt-glyph")
                yield _PromptInput(placeholder="What would you like to do?", id="prompt-input")
                yield Static(self._render_composer_hints(), id="composer-hints")
            with Horizontal(id="status-row"):
                yield Static(self._render_status_left(), id="status-left")
                yield Static(self._render_status_right(), id="status-right")
            yield Static(self._render_shortcut_bar(), id="shortcut-bar")

    def on_mount(self) -> None:
        """Focus the composer input and start the live spinner refresh timer."""
        self.query_one("#prompt-input", _PromptInput).focus()
        self.set_interval(0.2, self._tick_live_turn)

    def _render_composer_hints(self) -> Text:
        hints = Text()
        hints.append(" / ", style="bold #d4d4d8 on #27272a")
        hints.append(" commands  ", style="#71717a")
        hints.append(" @ ", style="bold #d4d4d8 on #27272a")
        hints.append(" context  ", style="#71717a")
        hints.append(" ctrl+o ", style="bold #d4d4d8 on #27272a")
        hints.append(" repl", style="#71717a")
        return hints

    def _render_status_left(self) -> Text:
        left = Text()
        left.append(" Fleet-RLM ", style="bold #f4f4f5 on #27272a")
        left.append(f"  {self.session_id}", style="bold #a1a1aa")
        if self.state.context_path:
            left.append(f"  @{self.state.context_path}", style="#34d399")
        if self.state.record_replay:
            left.append("  ", style="")
            left.append(" replay: on ", style="bold #34d399 on #14291f")
        if self._turn_active:
            left.append("  running…", style="bold #fbbf24")
        elif self._last_ctrl_c and (time.monotonic() - self._last_ctrl_c) <= 2.5:
            left.append("  press ctrl+c again to exit", style="bold #fbbf24")
        return left

    def _render_status_right(self) -> Text:
        right = Text()
        short_root = self.state.root_model.removeprefix("openai/") if self.state.root_model else "default"
        right.append(f" {short_root} ", style="#a1a1aa on #1f222b")
        right.append(" ")
        mode_colors = {
            "hybrid": "bold #f4f4f5 on #27272a",
            "rlm": "bold #34d399 on #14291f",
            "flex": "bold #fbbf24 on #292014",
        }
        right.append(f" {self.state.mode.title()} ", style=mode_colors.get(self.state.mode, "bold on #27272a"))
        return right

    def _render_shortcut_bar(self) -> Text:
        bar = Text()
        pairs = [
            ("enter", "Send"),
            ("tab", "Complete"),
            ("ctrl+o", "Toggle REPL"),
            ("ctrl+t", "Thinking"),
            ("ctrl+k", "Mode"),
            ("esc", "Cancel" if self._turn_active else "Dismiss"),
            ("ctrl+c x2", "Quit"),
        ]
        for idx, (key, label) in enumerate(pairs):
            if idx > 0:
                bar.append("  ")
            bar.append(key, style="bold #a1a1aa")
            bar.append(f" {label}", style="#71717a")
        return bar

    def refresh_chrome(self) -> None:
        """Update the bottom status pills and shortcut bar."""
        if not self.is_running:
            return
        self.query_one("#status-left", Static).update(self._render_status_left())
        self.query_one("#status-right", Static).update(self._render_status_right())
        self.query_one("#shortcut-bar", Static).update(self._render_shortcut_bar())
        inp = self.query_one("#prompt-input", _PromptInput)
        if self.state.pending_model_choice is not None:
            role, models = self.state.pending_model_choice
            inp.placeholder = f"Select {role} model [1-{len(models)}; Enter cancels]…"
        else:
            inp.placeholder = "What would you like to do?"

    def _tick_live_turn(self) -> None:
        if self._turn_active and self._active_turn_widget is not None:
            with self._lock:
                self._active_turn_widget.tracker.advance_spinner()
            self._active_turn_widget.sync_from_tracker()

    @contextmanager
    def _capture_console(self) -> Generator[io.StringIO]:
        buffer = io.StringIO()
        width = max(60, (self.size.width or 100) - 6)
        capture = Console(
            file=buffer,
            width=width,
            force_terminal=True,
            color_system="truecolor",
            no_color=False,
            _environ={"TERM": "xterm-256color"},
        )
        prev_render = render_mod.CONSOLE
        prev_commands = commands_mod.CONSOLE
        prev_app = CONSOLE
        render_mod.CONSOLE = capture
        commands_mod.CONSOLE = capture
        globals()["CONSOLE"] = capture
        try:
            yield buffer
        finally:
            render_mod.CONSOLE = prev_render
            commands_mod.CONSOLE = prev_commands
            globals()["CONSOLE"] = prev_app

    def _mount_to_chat(self, widget: Any) -> None:
        if not self.is_running:
            return
        chat = self.query_one("#chat-scroll", VerticalScroll)
        chat.mount(widget)
        chat.scroll_end(animate=False)

    def append_ansi(self, ansi_text: str) -> None:
        """Mount captured ANSI command output into the chat scroll container."""
        cleaned = ansi_text.strip("\n")
        if not cleaned or not self.is_running:
            return
        self._mount_to_chat(Static(Text.from_ansi(cleaned), classes="cmd-output"))

    def append_markup(self, markup: str) -> None:
        """Mount Rich markup text into the chat scroll container."""
        if not self.is_running:
            return
        self._mount_to_chat(Static(Text.from_markup(markup), classes="cmd-output"))

    def _compute_popup_items(self, val: str) -> list[tuple[str, str, str]]:
        """Return `(completion_text, display_label, description)` items matching `val`."""
        stripped = val.lstrip()
        items: list[tuple[str, str, str]] = []
        if stripped.startswith("/"):
            cmd_part, has_space, arg_part = stripped.partition(" ")
            if not has_space:
                for name, cmd in COMMANDS.items():
                    if name.startswith(cmd_part):
                        takes_arg = " " in cmd.usage
                        items.append((f"{name} " if takes_arg else name, cmd.usage, cmd.help))
                for extra_name, extra_help in (("/help", "show all commands"), ("/exit", "quit the CLI")):
                    if extra_name.startswith(cmd_part):
                        items.append((extra_name, extra_name, extra_help))
            elif cmd_part in _WORKSPACE_PATH_COMMANDS and self.state.workspace_files:
                candidates = list(self.state.workspace_files)
                if cmd_part == "/context":
                    candidates.insert(0, "clear")
                for cand in candidates:
                    if cand.startswith(arg_part.lstrip()):
                        items.append((f"{cmd_part} {cand}", f"{cmd_part} {cand}", "workspace file"))
            elif cmd_part in COMMANDS and isinstance(COMMANDS[cmd_part].completions, dict):
                for opt in COMMANDS[cmd_part].completions:
                    if opt.startswith(arg_part.lstrip()):
                        items.append((f"{cmd_part} {opt}", f"{cmd_part} {opt}", COMMANDS[cmd_part].help))
        elif "@" in stripped and self.state.workspace_files:
            prefix, _, after_at = stripped.rpartition("@")
            if " " not in after_at:
                for path in self.state.workspace_files:
                    if path.startswith(after_at):
                        items.append((f"{prefix}@{path} ", f"@{path}", "workspace file"))
        return items[:8]

    def _render_popup(self) -> None:
        """Render `self._popup_items` into `#slash-popup` with `self._popup_index` highlighted."""
        if not self.is_running:
            return
        popup = self.query_one("#slash-popup", Static)
        if not self._popup_items:
            popup.display = False
            return
        self._popup_index = max(0, min(self._popup_index, len(self._popup_items) - 1))
        lines = Text()
        for idx, (_fill, usage, desc) in enumerate(self._popup_items):
            if idx > 0:
                lines.append("\n")
            selected = idx == self._popup_index
            prefix = "▸ " if selected else "  "
            usage_style = "bold #34d399 on #272b36" if selected else "bold #f4f4f5"
            desc_style = "#d4d4d8 on #272b36" if selected else "#71717a"
            lines.append(prefix, style="bold #34d399" if selected else "#71717a")
            lines.append(f"{usage:<34}", style=usage_style)
            if desc:
                lines.append(f" {desc}", style=desc_style)
        popup.update(lines)
        popup.display = True

    def _sync_input_state(self, val: str) -> None:
        """Sync composer height, hint pills, and the `/` or `@` suggestion popup for `val`."""
        if not self.is_running:
            return
        inp = self.query_one("#prompt-input", _PromptInput)
        inp.styles.height = min(8, max(1, inp.document.line_count))
        hints = self.query_one("#composer-hints", Static)
        hints.display = not bool(val)
        new_items = self._compute_popup_items(val)
        if new_items != self._popup_items:
            self._popup_items = new_items
            self._popup_index = 0
        self._render_popup()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Update composer height and the floating `/` or `@` popup when text changes."""
        if event.text_area.id == "prompt-input":
            self._sync_input_state(event.text_area.text)

    def on_paste(self, event: events.Paste) -> None:
        """Redirect paste events anywhere in the screen into the bottom prompt composer."""
        if not self.is_running or not event.text:
            return
        inp = self.query_one("#prompt-input", _PromptInput)
        if not inp.has_focus:
            inp.focus()
            inp.insert(event.text)
            event.stop()

    def action_complete_slash(self) -> None:
        """Autocomplete the currently highlighted `/` or `@` menu item into the input box (`Tab`)."""
        if not self.is_running:
            return
        inp = self.query_one("#prompt-input", _PromptInput)
        if not inp.has_focus:
            inp.focus()
        if self._popup_items:
            idx = max(0, min(self._popup_index, len(self._popup_items) - 1))
            inp.value = self._popup_items[idx][0]
            return
        val = inp.value.lstrip()
        if val.startswith("/"):
            doc = Document(val, cursor_position=len(val))
            completions = list(_completer(self.state).get_completions(doc, CompleteEvent()))
            if completions:
                first = completions[0]
                inp.value = val[: len(val) + first.start_position] + first.text

    def action_cursor_up(self) -> None:
        """Navigate up in the `/` popup menu when open, or previous input history."""
        if not self.is_running:
            return
        popup = self.query_one("#slash-popup", Static)
        if popup.display and self._popup_items:
            self._popup_index = (self._popup_index - 1) % len(self._popup_items)
            self._render_popup()
            return
        inp = self.query_one("#prompt-input", _PromptInput)
        if inp.has_focus and self._history_entries and self._history_index > 0:
            self._history_index -= 1
            inp.value = self._history_entries[self._history_index]

    def action_cursor_down(self) -> None:
        """Navigate down in the `/` popup menu when open, or next input history."""
        if not self.is_running:
            return
        popup = self.query_one("#slash-popup", Static)
        if popup.display and self._popup_items:
            self._popup_index = (self._popup_index + 1) % len(self._popup_items)
            self._render_popup()
            return
        inp = self.query_one("#prompt-input", _PromptInput)
        if inp.has_focus and self._history_entries:
            if self._history_index < len(self._history_entries) - 1:
                self._history_index += 1
                inp.value = self._history_entries[self._history_index]
            else:
                self._history_index = len(self._history_entries)
                inp.value = ""

    def on_key(self, event: events.Key) -> None:
        """Support Alt+1..9 REPL step toggles."""
        if not self.is_running:
            return
        if event.key in {"alt+1", "alt+2", "alt+3", "alt+4", "alt+5", "alt+6", "alt+7", "alt+8", "alt+9"}:
            digit = int(event.key[-1])
            if self.state.last_tracker is not None:
                with self._lock:
                    self.state.last_tracker.toggle_repl_index(digit)
                self._refresh_last_turn_widget()
                event.stop()

    def submit_text(self, user_input: str) -> None:
        """Process a submitted command, model selection, or Turn prompt."""
        if self.state.pending_model_choice is not None:
            role, models = self.state.pending_model_choice
            self.state.pending_model_choice = None
            with self._capture_console() as buffer:
                try:
                    apply_model_choice(Context(self.client, self.session_id, self.state), role, models, user_input)
                except httpx.HTTPStatusError as exc:
                    print_http_error(exc)
                except httpx.HTTPError as exc:
                    render_mod.CONSOLE.print(f"[red]Fleet API is unavailable:[/red] {type(exc).__name__}")
            self.append_ansi(buffer.getvalue())
            self.refresh_chrome()
            return

        if not user_input:
            return

        self._append_history(user_input)

        if self._turn_active:
            self.append_markup(
                "[yellow]A Turn is currently running; wait for completion or press Esc / Ctrl+C to cancel.[/yellow]"
            )
            return

        self._mount_to_chat(_UserPromptCard(user_input))

        if user_input.startswith("/"):
            with self._capture_console() as buffer:
                keep_running = handle_command(self.client, user_input, self.session_id, state=self.state)
            self.append_ansi(buffer.getvalue())
            self.refresh_chrome()
            if not keep_running:
                self.exit()
            return

        self._start_turn_stream(user_input)

    def _refresh_last_turn_widget(self) -> None:
        if not self.is_running:
            return
        for widget in reversed(list(self.query(_TurnWidget))):
            widget.sync_from_tracker()
            break
        self.refresh_chrome()

    def action_toggle_latest_repl(self) -> None:
        """Toggle the latest REPL cell open or closed (`Ctrl+O`)."""
        if self.state.last_tracker is not None:
            with self._lock:
                self.state.last_tracker.toggle_latest_repl()
            self._refresh_last_turn_widget()

    def action_toggle_thinking(self) -> None:
        """Toggle all Thinking blocks open or closed (`Ctrl+T`)."""
        if self.state.last_tracker is not None:
            with self._lock:
                self.state.last_tracker.toggle_thinking()
            self._refresh_last_turn_widget()

    def action_toggle_all(self) -> None:
        """Toggle all collapsible blocks in the latest Turn (`Alt+A`)."""
        if self.state.last_tracker is not None:
            with self._lock:
                self.state.last_tracker.toggle_all()
            self._refresh_last_turn_widget()

    def action_cycle_mode(self) -> None:
        """Cycle the execution mode locally and persist it when idle (`Ctrl+K`)."""
        if self._turn_active:
            return
        idx = (MODES.index(self.state.mode) + 1) % len(MODES) if self.state.mode in MODES else 0
        next_mode = MODES[idx]
        with self._capture_console() as buffer:
            handle_command(self.client, f"/mode {next_mode}", self.session_id, state=self.state)
        self.append_ansi(buffer.getvalue())
        self.refresh_chrome()

    def action_dismiss_or_cancel(self) -> None:
        """Dismiss the slash popup or cancel an active Turn stream (`Escape`)."""
        if self.is_running:
            popup = self.query_one("#slash-popup", Static)
            if popup.display:
                popup.display = False
                return
        if self._turn_active:
            self.cancel_active_turn()

    def action_clear_or_cancel(self) -> None:
        """Cancel an active Turn stream, clear input on first Ctrl+C, or exit on second Ctrl+C."""
        if self._turn_active:
            self._last_ctrl_c = 0.0
            self.cancel_active_turn()
            return
        now = time.monotonic()
        if self._last_ctrl_c and (now - self._last_ctrl_c) <= 2.5:
            self.exit()
            return
        self._last_ctrl_c = now
        self.state.pending_model_choice = None
        if self.is_running:
            inp = self.query_one("#prompt-input", _PromptInput)
            inp.value = ""
            self._popup_items = []
            self.query_one("#slash-popup", Static).display = False
        self.refresh_chrome()
        self.set_timer(2.6, self.refresh_chrome)

    def action_quit_if_idle(self) -> None:
        """Exit when the input box is empty and no Turn is running (`Ctrl+D`)."""
        if self._turn_active:
            return
        if self.is_running and self.query_one("#prompt-input", _PromptInput).value:
            return
        self.exit()

    def cancel_active_turn(self) -> None:
        """Request cancellation of the active Turn stream by closing the HTTP stream."""
        self._cancel_requested = True
        resp = self._active_response
        if resp is not None:
            with suppress(Exception):
                resp.close()

    def _notify_turn_update(self, turn_widget: _TurnWidget, *, scroll: bool = False) -> None:
        def update_ui() -> None:
            turn_widget.sync_from_tracker()
            self.refresh_chrome()
            if scroll and self.is_running:
                self.query_one("#chat-scroll", VerticalScroll).scroll_end(animate=False)

        with suppress(Exception):
            self.call_from_thread(update_ui)

    def _start_turn_stream(self, task_text: str) -> None:
        tracker = TurnTracker()
        self.state.last_tracker = tracker
        turn_widget = _TurnWidget(tracker)
        self._active_turn_widget = turn_widget
        self._turn_active = True
        self._cancel_requested = False
        self._mount_to_chat(turn_widget)
        self.refresh_chrome()

        def worker() -> None:
            try:
                with _request_turn(self.client, self.session_id, task_text, self.state) as response:
                    self._active_response = response
                    response.raise_for_status()
                    for event in _turn_events(
                        response,
                        self.state,
                        tracker,
                        lock=self._lock,
                        cancelled=lambda: self._cancel_requested,
                    ):
                        if event is None:
                            self.call_from_thread(self.append_markup, "[yellow]Ignoring malformed SSE event.[/yellow]")
                            continue
                        self._notify_turn_update(turn_widget, scroll=True)
                if self._cancel_requested and tracker.terminal_event is None:
                    with self._lock:
                        tracker.finished_at = time.monotonic()
                        tracker.finalize_running_items("cancelled")
                    self.call_from_thread(
                        self.append_markup,
                        "[yellow]Turn stream closed; server cancels on disconnect. "
                        "Check /history for outcome.[/yellow]",
                    )
                elif tracker.terminal_event is None:
                    with self._lock:
                        tracker.finished_at = time.monotonic()
                        tracker.finalize_running_items("interrupted")
                    self.call_from_thread(
                        self.append_markup,
                        "[yellow]Stream ended without a final outcome. Check /history before retrying.[/yellow]",
                    )
            except httpx.HTTPStatusError as exc:
                detail = _extract_http_detail(exc.response)
                with self._lock:
                    tracker.finished_at = time.monotonic()
                    tracker.finalize_running_items("interrupted")
                self.call_from_thread(
                    self.append_markup,
                    f"[bold red]HTTP {exc.response.status_code}:[/bold red] {detail}",
                )
            except httpx.HTTPError as exc:
                with self._lock:
                    tracker.finished_at = time.monotonic()
                    tracker.finalize_running_items("cancelled" if self._cancel_requested else "interrupted")
                if self._cancel_requested:
                    self.call_from_thread(
                        self.append_markup,
                        "[yellow]Turn stream closed; server cancels on disconnect. "
                        "Check /history for outcome.[/yellow]",
                    )
                elif tracker.terminal_event is None:
                    self.call_from_thread(self.append_markup, f"[red]Turn stream failed:[/red] {type(exc).__name__}")
            finally:
                self._active_response = None
                with self._lock:
                    self._turn_active = False
                    if tracker.finished_at is None:
                        tracker.finished_at = time.monotonic()
                    if tracker.terminal_event is None:
                        tracker.terminal_event = {"event": "interrupted"}
                    tracker.finalize_running_items("interrupted")
                self._notify_turn_update(turn_widget, scroll=True)

        threading.Thread(target=worker, name="fleet-textual-turn", daemon=True).start()


def run_interactive_cli(base_url: str, session_id: str, *, settings: FleetSettings | None = None) -> None:
    """Connect to the API and run the Textual full-screen chat TUI (or non-TTY fallback) until exit."""
    validate_session_id(session_id)
    state = SessionState(mlflow_settings=settings)
    # FastAPI's SSE routing emits a `: ping` keepalive every 15 seconds while the Turn
    # generator is idle, so the 300-second read timeout is only a safety net.
    with httpx.Client(base_url=base_url, timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        if sys.stdin.isatty() and CONSOLE.is_terminal:
            FleetTextualApp(client, session_id, state).run()
            return

        CONSOLE.print(
            Panel.fit(
                f"[bold cyan]Fleet-RLM Lite[/bold cyan] | Session: [yellow]{session_id}[/yellow]\n"
                "Enter `/help` for commands; `/replay on|off` selects bounded replay capture; `/exit` to quit."
            )
        )
        while True:
            try:
                user_input = CONSOLE.input("\n[bold green]fleet>[/bold green] ").strip()
            except KeyboardInterrupt:
                CONSOLE.print("[dim]Input cleared. Use /exit to quit.[/dim]")
                continue
            except EOFError:
                CONSOLE.print()
                break
            if not user_input:
                continue
            if user_input.startswith("/"):
                if not handle_command(client, user_input, session_id, state=state):
                    break
                continue
            tracker = TurnTracker()
            state.last_tracker = tracker
            try:
                with _request_turn(client, session_id, user_input, state) as response:
                    response.raise_for_status()
                    for event in _turn_events(response, state, tracker):
                        if event is None:
                            CONSOLE.print("[yellow]Ignoring malformed SSE event.[/yellow]")
                            continue
                        render_event(event)
                if tracker.terminal_event is None:
                    CONSOLE.print(
                        "[yellow]Stream ended without a final outcome. Check /history before retrying.[/yellow]"
                    )
            except httpx.HTTPStatusError as exc:
                print_http_error(exc)
            except httpx.HTTPError as exc:
                CONSOLE.print(f"[red]Turn stream failed:[/red] {type(exc).__name__}")
            except KeyboardInterrupt:
                CONSOLE.print(
                    "[yellow]Turn stream closed; server cancels on disconnect. Check /history for outcome.[/yellow]"
                )
