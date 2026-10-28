"""The interactive terminal client: Textual chat TUI with collapsible REPL cards and bottom composer."""

from __future__ import annotations

import asyncio
import io
import shlex
import sys
import threading
import time
import uuid
from collections.abc import Generator, Iterable
from contextlib import contextmanager, suppress
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
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import OptionList, Static, TextArea

import fleet_rlm.tui.commands as commands_mod
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
from fleet_rlm.tui.experiments import ExperimentsScreen
from fleet_rlm.tui.mentions import attach_local_mentions, completed_mention_cursor, mention_completions
from fleet_rlm.tui.render import (
    CONSOLE,
    TurnTracker,
    _extract_http_detail,
    print_http_error,
    render_event,
)
from fleet_rlm.tui.stream import _request_turn, _turn_events
from fleet_rlm.tui.widgets import (
    _CommandMenu,
    _GradientRule,
    _PromptInput,
    _TurnWidget,
    _UserPromptCard,
)
from fleet_rlm.workspace import validate_session_id

_LOCAL_PATH_COMMANDS = frozenset({"/attach", "/put"})
_WORKSPACE_PATH_COMMANDS = frozenset({"/cat", "/stat", "/get", "/rm", "/context"})


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
            if cmd in {"/put", "/get"}:
                yield from self._transfer_completions(cmd, arg_text, complete_event)
                return
            if cmd == "/attach":
                yield from self._local_path_completions(arg_text, complete_event)
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

    def _local_path_completions(self, fragment: str, event: CompleteEvent) -> Iterable[Completion]:
        """Yield local path completions with a trailing slash for directories."""
        document = Document(fragment, cursor_position=len(fragment))
        for completion in self._path_completer.get_completions(document, event):
            candidate = fragment[: len(fragment) + completion.start_position] + completion.text
            suffix = "/" if Path(candidate).expanduser().is_dir() and not candidate.endswith("/") else ""
            yield Completion(
                completion.text + suffix,
                start_position=completion.start_position,
                display=completion.display,
                display_meta=completion.display_meta,
            )

    def _transfer_completions(self, command: str, arguments: str, event: CompleteEvent) -> Iterable[Completion]:
        # Transfers take two paths; quote each token so a suggested filename with spaces
        # is exactly the same filename the command handler will receive.
        """Yield quoted path arguments for `/put` or `/get` from local files or cached workspace paths.

        Unclosed quotes and arguments beyond the second path yield no suggestions.
        """
        try:
            words = shlex.split(arguments)
        except ValueError:
            return
        position = len(words) if arguments.endswith(" ") else max(0, len(words) - 1)
        if position > 1:
            return
        fragment = words[position] if position < len(words) else ""
        local = (command == "/put" and position == 0) or (command == "/get" and position == 1)
        completions = (
            self._local_path_completions(fragment, event)
            if local
            else (
                Completion(path, start_position=-len(fragment))
                for path in (self._state.workspace_files if self._state is not None else [])
                if path.startswith(fragment)
            )
        )
        for completion in completions:
            candidate = fragment[: len(fragment) + completion.start_position] + completion.text
            fill = " ".join(shlex.quote(word) for word in [*words[:position], candidate])
            yield Completion(fill, start_position=-len(arguments), display=candidate)


def _completer(state: SessionState | None = None) -> Completer:
    """Build the slash-command and argument completer."""
    arguments = NestedCompleter.from_nested_dict(completion_options())
    base = merge_completers([WordCompleter(list(arguments.options), sentence=True), arguments], deduplicate=True)
    return _SessionCompleter(base, state=state)


class FleetTextualApp(App[str | None]):
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
        max-height: 10;
        margin: 0 1;
        padding: 0 1;
        background: #1f222b;
        border: round #3f3f46;
        scrollbar-size-vertical: 1;
        scrollbar-color: #71717a;
        scrollbar-background: #1f222b;
    }
    #slash-popup > .option-list--option {
        padding: 0;
    }
    #slash-popup > .option-list--option-highlighted {
        color: #34d399;
        background: #272b36;
        text-style: bold;
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
        Binding("tab", "complete_slash", "Complete", show=False),
        Binding("ctrl+o", "toggle_latest_repl", "Toggle REPL", show=False, priority=True),
        Binding("ctrl+t", "toggle_thinking", "Toggle Thinking", show=False, priority=True),
        Binding("alt+a", "toggle_all", "Toggle All", show=False, priority=True),
        Binding("ctrl+k", "cycle_mode", "Cycle Mode", show=False, priority=True),
        Binding("escape", "dismiss_or_cancel", "Cancel/Dismiss", show=False),
        Binding("ctrl+c", "clear_or_cancel", "Clear/Cancel", show=False),
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
        self._optimization_active = False
        self._optimization_stopping = False
        self._optimizer_stop_requested = False
        self._optimizer_snapshot_polling = False
        self._optimization_terminal_status: str | None = None
        self._optimization_progress: dict[str, Any] = {}
        self._optimization_run_id: str | None = None
        self.state.optimizer_launcher = self._start_optimization
        self.state.experiments_opener = self._open_experiments
        self._history_entries: list[str] = []
        self._history_index: int = 0
        self._history_draft = ""
        self._history_recalled: str | None = None
        self._completion_suppressed_for: str | None = None
        self._popup_items: list[tuple[str, str, str]] = []
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
        """Append input to history, reset draft navigation, and persist it when local file access succeeds."""
        self._history_entries.append(text)
        self._history_index = len(self._history_entries)
        self._history_draft = ""
        self._history_recalled = None
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
        yield _CommandMenu(id="slash-popup", markup=False)
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
        self.set_interval(1.0, self._tick_optimizer_snapshot)

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
        """Render Session, context, replay, and status for active work or exit confirmation."""
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
        elif self._optimization_active:
            left.append("  stopping…" if self._optimization_stopping else "  optimizing…", style="bold #fbbf24")
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
        """Return keyboard hints for the open completion menu or the main composer."""
        bar = Text()
        pairs = [
            ("enter", "Send"),
            ("tab", "Complete"),
            ("ctrl+o", "Toggle REPL"),
            ("ctrl+t", "Thinking"),
            ("ctrl+k", "Mode"),
            ("esc", "Cancel" if self._turn_active or self._optimization_active else "Dismiss"),
            ("ctrl+c x2", "Quit"),
        ]
        if any(popup.display for popup in self.query("#slash-popup")):
            pairs = [("↑/↓", "Browse"), ("tab", "Complete"), ("enter", "Accept/run"), ("esc", "Close menu")]
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
                self._active_turn_widget.sync_status()

    def _tick_optimizer_snapshot(self) -> None:
        """Keep observing an active API run while its history screen is closed."""
        if (
            not self._optimization_active
            or isinstance(self.screen, ExperimentsScreen)
            or self._optimizer_snapshot_polling
        ):
            return
        self._optimizer_snapshot_polling = True
        self.run_worker(
            self._poll_optimizer_snapshot(),
            name="optimizer-snapshot",
            group="optimizer-snapshot",
            exit_on_error=False,
        )

    async def _poll_optimizer_snapshot(self) -> None:
        """Refresh the active or last observed run, retaining the last state on handled fetch errors."""
        try:
            response = await asyncio.to_thread(
                self.client.get, f"/sessions/{quote(self.session_id, safe='')}/experiments"
            )
            response.raise_for_status()
            snapshot = response.json()
            if not isinstance(snapshot, dict):
                return
            records = snapshot.get("experiments")
            if not isinstance(records, list):
                return
            active_id = snapshot.get("active_run_id")
            run_id = active_id if isinstance(active_id, str) else self._optimization_run_id
            record = next(
                (item for item in records if isinstance(item, dict) and item.get("run_id") == run_id),
                None,
            )
            if record is not None:
                self._optimizer_snapshot(record)
        except (httpx.HTTPError, ValueError, TypeError):
            # Keep the last known state and retry on the next tick.
            pass
        finally:
            self._optimizer_snapshot_polling = False

    @contextmanager
    def _capture_console(self) -> Generator[tuple[io.StringIO, Console]]:
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
        yield buffer, capture

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
            cmd_part, has_space, _arg_part = stripped.partition(" ")
            if not has_space:
                for name, cmd in COMMANDS.items():
                    if name.startswith(cmd_part):
                        takes_arg = " " in cmd.usage
                        items.append((f"{name} " if takes_arg else name, cmd.usage, cmd.help))
                for extra_name, extra_help in (
                    ("/help", "show all commands"),
                    ("/exit", "quit the CLI"),
                    ("/quit", "quit the CLI"),
                ):
                    if extra_name.startswith(cmd_part):
                        items.append((extra_name, extra_name, extra_help))
            elif cmd_part in COMMANDS:
                # Share argument/path completion with the Prompt Toolkit fallback, including
                # nested choices such as `/config mode rlm` rather than only the first level.
                document = Document(stripped, cursor_position=len(stripped))
                for completion in _completer(self.state).get_completions(document, CompleteEvent()):
                    fill = stripped[: len(stripped) + completion.start_position] + completion.text
                    choices = COMMANDS[cmd_part].completions
                    for word in fill.partition(" ")[2].split():
                        choices = choices.get(word) if isinstance(choices, dict) else None
                    if isinstance(choices, dict) and choices:
                        fill += " "
                    description = "local path" if cmd_part in _LOCAL_PATH_COMMANDS else COMMANDS[cmd_part].help
                    items.append((fill, fill.rstrip(), description))
        elif "@" in val:
            inp = self.query_one("#prompt-input", _PromptInput)
            cursor = inp.cursor_offset
            items = mention_completions(val, cursor, self.state.workspace_files)
        return items

    def _render_popup(self) -> None:
        """Populate the native scrollable list; its highlighted option stays in view."""
        if not self.is_running:
            return
        popup = self.query_one("#slash-popup", OptionList)
        popup.clear_options()
        popup.add_options(
            Text(f"{usage:<34} {desc}", no_wrap=True, overflow="ellipsis") for _fill, usage, desc in self._popup_items
        )
        popup.display = bool(self._popup_items)
        if self._popup_items:
            popup.highlighted = 0
            self.call_after_refresh(popup.scroll_to_highlight)
        self.query_one("#shortcut-bar", Static).update(self._render_shortcut_bar())

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Keep selection and keyboard hints legible without moving focus from the composer."""
        if event.option_list.id == "slash-popup" and self._current_menu_event(event):
            event.option_list.border_title = Text(self._popup_items[event.option_index][1])
            event.option_list.border_subtitle = (
                f"{event.option_index + 1}/{len(self._popup_items)} · ↑↓ browse · Tab/Enter choose · Esc close"
            )

    def _current_menu_event(self, event: OptionList.OptionMessage) -> bool:
        """Return whether the event still refers to the current suggestion at its index."""
        return (
            0 <= event.option_index < len(self._popup_items)
            and event.option_list.get_option_at_index(event.option_index) is event.option
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Clicking a suggestion fills it without executing it or stealing input focus."""
        if event.option_list.id == "slash-popup" and self._current_menu_event(event):
            event.stop()
            self._accept_completion(event.option_index)

    def _sync_input_state(self, val: str) -> None:
        """Sync composer height, hint pills, and the `/` or `@` suggestion popup for `val`."""
        if not self.is_running:
            return
        inp = self.query_one("#prompt-input", _PromptInput)
        inp.styles.height = min(8, max(1, inp.document.line_count))
        hints = self.query_one("#composer-hints", Static)
        hints.display = not bool(val)
        if self._history_recalled is not None and val != self._history_recalled:
            self._history_recalled = None
            self._history_index = len(self._history_entries)
        if val == self._completion_suppressed_for:
            self._popup_items = []
            self._render_popup()
            return
        self._completion_suppressed_for = None
        new_items = self._compute_popup_items(val)
        if new_items != self._popup_items:
            self._popup_items = new_items
            self._render_popup()
        else:
            self.query_one("#slash-popup", OptionList).display = bool(new_items)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Update composer height and the floating `/` or `@` popup when text changes."""
        if event.text_area.id == "prompt-input":
            self._sync_input_state(event.text_area.text)

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        """Keep mention suggestions aligned with the cursor even without a text edit."""
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
        self._completion_suppressed_for = None
        self._sync_input_state(inp.value)
        popup = self.query_one("#slash-popup", OptionList)
        if popup.display and self._popup_items:
            self._accept_completion(popup.highlighted or 0)
            return
        val = inp.value.lstrip()
        if val.startswith("/"):
            doc = Document(val, cursor_position=len(val))
            completions = list(_completer(self.state).get_completions(doc, CompleteEvent()))
            if completions:
                first = completions[0]
                inp.value = val[: len(val) + first.start_position] + first.text

    def _accept_completion(self, index: int) -> None:
        """Fill the indexed suggestion, reset history navigation, and focus the composer at the completion."""
        inp = self.query_one("#prompt-input", _PromptInput)
        self._history_recalled = None
        self._history_index = len(self._history_entries)
        fill = self._popup_items[index][0]
        cursor = len(fill)
        if not inp.value.lstrip().startswith("/"):
            cursor = completed_mention_cursor(inp.value, inp.cursor_offset, fill)
        inp.value = fill
        inp.move_cursor_to_offset(cursor)
        self._sync_input_state(inp.value)
        inp.focus()

    def _complete_on_enter(self) -> bool:
        """Accept incomplete suggestions first; an explicitly complete command still submits."""
        popup = self.query_one("#slash-popup", OptionList)
        if not popup.display or not self._popup_items:
            return False
        value = self.query_one("#prompt-input", _PromptInput).value.lstrip()
        if value in COMMANDS or value in {"/help", "/exit", "/quit"}:
            return False
        if any(value.rstrip() == fill.rstrip() for fill, _usage, _description in self._popup_items):
            return False
        if value.startswith(("/put ", "/get ")):
            with suppress(ValueError):
                if len(shlex.split(value)) >= 3:
                    return False  # Transfer destinations are free-form, not incomplete choices.
        self._accept_completion(popup.highlighted or 0)
        return True

    def action_cursor_up(self) -> None:
        """Navigate up in the `/` popup menu when open, or previous input history."""
        if not self.is_running:
            return
        popup = self.query_one("#slash-popup", OptionList)
        if popup.display and self._popup_items:
            popup.action_cursor_up()
            return
        inp = self.query_one("#prompt-input", _PromptInput)
        if inp.has_focus and self._history_entries and self._history_index > 0:
            if self._history_index == len(self._history_entries):
                self._history_draft = inp.value
            self._history_index -= 1
            self._history_recalled = self._history_entries[self._history_index]
            self._completion_suppressed_for = self._history_recalled
            inp.value = self._history_recalled

    def action_cursor_down(self) -> None:
        """Navigate down in the `/` popup menu when open, or next input history."""
        if not self.is_running:
            return
        popup = self.query_one("#slash-popup", OptionList)
        if popup.display and self._popup_items:
            popup.action_cursor_down()
            return
        inp = self.query_one("#prompt-input", _PromptInput)
        if inp.has_focus and self._history_index < len(self._history_entries):
            self._history_index += 1
            self._history_recalled = (
                self._history_entries[self._history_index] if self._history_index < len(self._history_entries) else None
            )
            value = self._history_recalled if self._history_recalled is not None else self._history_draft
            self._completion_suppressed_for = value
            inp.value = value

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

    def _open_experiments(self) -> None:
        """Open or refresh the Experiments screen for the active Session."""
        if isinstance(self.screen, ExperimentsScreen):
            self.screen.refresh_history()
            return
        self.push_screen(
            ExperimentsScreen(
                self.client,
                self.session_id,
                on_run=self._start_optimization,
                on_cancel=self._request_optimizer_stop,
                on_review=self._review_optimization,
                on_snapshot=self._optimizer_snapshot,
            )
        )

    def _start_optimization(self, options: dict[str, Any] | str) -> None:
        """Submit an API-owned optimization; closing the screen only stops observation."""
        if isinstance(options, str):
            parsed = commands_mod._parse_optimizer_arguments(Context(self.client, self.session_id, self.state), options)
            if parsed is None:
                return
            options, approve_run = parsed
            if approve_run:
                self._review_optimization(approve_run, "approved", str(uuid.uuid4()))
                return
        self._open_experiments()
        self.run_worker(
            self._post_optimization(options),
            name="experiment-start",
            group="experiment-api",
            exit_on_error=False,
        )

    async def _post_optimization(self, options: dict[str, Any]) -> None:
        """Submit a start request and update the observed run or display an HTTP failure.

        Retain the request ID on transport or server errors so retries reuse it; clear
        it on success or a 4xx response. Response decoding errors propagate.
        """
        try:
            response = await asyncio.to_thread(
                self.client.post, f"/sessions/{quote(self.session_id, safe='')}/experiments", json=options
            )
            response.raise_for_status()
            record = response.json().get("experiment", {})
            request_id = options.get("request_id")
            if isinstance(request_id, str):
                commands_mod._finish_optimizer_request(self.state, request_id)
            self._optimizer_snapshot(record)
            self.append_markup(f"[cyan]Optimization {record.get('status', 'started')}:[/cyan] {record.get('run_id')}")
            if isinstance(self.screen, ExperimentsScreen):
                self.screen.refresh_history()
        except httpx.HTTPError as exc:
            request_id = options.get("request_id")
            if (
                isinstance(exc, httpx.HTTPStatusError)
                and 400 <= exc.response.status_code < 500
                and isinstance(request_id, str)
            ):
                commands_mod._finish_optimizer_request(self.state, request_id)
            self.append_markup(f"[red]Optimization could not start ({type(exc).__name__}).[/red]")

    def _review_optimization(self, run_id: str, decision: str, request_id: str) -> None:
        """Schedule a review request using the supplied retry identity without blocking the UI."""
        self.run_worker(
            self._post_review(run_id, decision, request_id),
            name="experiment-review",
            group="experiment-api",
            exit_on_error=False,
        )

    async def _post_review(self, run_id: str, decision: str, request_id: str) -> None:
        """Submit a review and refresh the observed run or display an HTTP failure.

        Keep the review request ID for transport/server failures; release it on success
        or a client error while the history screen is open. Response decoding errors
        propagate.
        """
        try:
            response = await asyncio.to_thread(
                self.client.post,
                f"/sessions/{quote(self.session_id, safe='')}/experiments/{quote(run_id, safe='')}/review",
                json={"decision": decision, "request_id": request_id},
            )
            response.raise_for_status()
            self._optimizer_snapshot(response.json().get("experiment", {}))
            if isinstance(self.screen, ExperimentsScreen):
                self.screen._finish_review_request(run_id, decision)
                self.screen.refresh_history()
        except httpx.HTTPError as exc:
            if (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code < 500
                and isinstance(self.screen, ExperimentsScreen)
            ):
                self.screen._finish_review_request(run_id, decision)
            self.append_markup(f"[red]Experiment review failed ({type(exc).__name__}); retry from history.[/red]")

    def _request_optimizer_stop(self, run_id: str | None = None) -> None:
        """Ask the API-owned optimization to stop, independent of this UI lifetime."""
        run_id = run_id or self._optimization_run_id
        if not run_id or self._optimization_stopping:
            return
        self._optimizer_stop_requested = True
        self._optimization_stopping = True
        self.refresh_chrome()
        self.run_worker(
            self._cancel_optimization(run_id),
            name="experiment-cancel",
            group="experiment-api",
            exit_on_error=False,
        )

    async def _cancel_optimization(self, run_id: str) -> None:
        """Request a cooperative stop, updating the snapshot or restoring retry controls on HTTP failure.

        Refresh the status controls even when response decoding raises.
        """
        try:
            response = await asyncio.to_thread(
                self.client.post,
                f"/sessions/{quote(self.session_id, safe='')}/experiments/{quote(run_id, safe='')}/cancel",
            )
            response.raise_for_status()
            self._optimizer_snapshot(response.json().get("experiment", {}))
            self.append_markup("[yellow]Cancellation requested; the API is cleaning up.[/yellow]")
        except httpx.HTTPError as exc:
            self._optimization_stopping = False
            self._optimizer_stop_requested = False
            self.append_markup(f"[red]Cancellation failed ({type(exc).__name__}); you can retry.[/red]")
        finally:
            self.refresh_chrome()

    def _optimizer_snapshot(self, record: dict[str, Any] | None) -> None:
        """Reflect the selected API run in the main TUI controls."""
        self._optimization_run_id = record.get("run_id") if record else None
        status = record.get("status") if record else None
        self._optimization_active = status in {"running", "approving"}
        self._optimization_stopping = self._optimization_active and (
            self._optimizer_stop_requested
            or status == "cancelling"
            or (record is not None and record.get("stage") in {"stopping", "cancelling"})
        )
        if not self._optimization_active:
            self._optimizer_stop_requested = False
        terminal = {"completed", "failed", "cancelled", "interrupted", "approved", "rejected"}
        self._optimization_terminal_status = status if status in terminal else None
        progress = record.get("progress") if record else None
        self._optimization_progress = (
            dict(progress) if isinstance(progress, dict) else {"stage": record.get("stage")} if record else {}
        )
        if self.is_running:
            self.refresh_chrome()

    def submit_text(self, user_input: str) -> None:
        """Process a submitted command, model selection, or Turn prompt."""
        if self.state.pending_model_choice is not None:
            role, models = self.state.pending_model_choice
            self.state.pending_model_choice = None
            with self._capture_console() as (buffer, console):
                try:
                    apply_model_choice(
                        Context(self.client, self.session_id, self.state, console=console),
                        role,
                        models,
                        user_input,
                    )
                except httpx.HTTPStatusError as exc:
                    print_http_error(exc, console=console)
                except httpx.HTTPError as exc:
                    console.print(f"[red]Fleet API is unavailable:[/red] {type(exc).__name__}")
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

        if user_input.strip() == "/setup" and self.state.setup_available:
            self.exit("setup")
            return

        self._mount_to_chat(_UserPromptCard(user_input))

        if user_input.startswith("/"):
            with self._capture_console() as (buffer, console):
                keep_running = handle_command(
                    self.client, user_input, self.session_id, state=self.state, console=console
                )
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
        with self._capture_console() as (buffer, console):
            handle_command(self.client, f"/mode {next_mode}", self.session_id, state=self.state, console=console)
        self.append_ansi(buffer.getvalue())
        self.refresh_chrome()

    def action_dismiss_or_cancel(self) -> None:
        """Dismiss the slash popup or cancel an active Turn stream (`Escape`)."""
        if self.is_running:
            popup = self.query_one("#slash-popup", OptionList)
            if popup.display:
                self._completion_suppressed_for = self.query_one("#prompt-input", _PromptInput).value
                self._popup_items = []
                self._render_popup()
                return
        if self._turn_active:
            self.cancel_active_turn()
        elif self._optimization_active:
            self._request_optimizer_stop()

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
            self.query_one("#slash-popup", OptionList).display = False
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

    def _restore_attachment_draft(self, text: str) -> None:
        """Keep failed attachment submissions editable without clobbering a newer draft."""
        inp = self.query_one("#prompt-input", _PromptInput)
        if not inp.value:
            self._completion_suppressed_for = text
            inp.value = text

    def _start_turn_stream(self, task_text: str) -> None:
        """Start a background attachment upload and Turn stream with a new activity tracker.

        Restore the draft if attachment preparation fails or is cancelled before a Turn is
        sent. Completed uploads remain in the workspace. Stream failures update the UI
        without claiming a server outcome when no terminal event was received.
        """
        tracker = TurnTracker()
        self.state.last_tracker = tracker
        turn_widget = _TurnWidget(tracker)
        self._active_turn_widget = turn_widget
        self._turn_active = True
        self._cancel_requested = False
        self._mount_to_chat(turn_widget)
        self.refresh_chrome()

        def worker() -> None:
            """Prepare local attachments and consume the Turn stream, reporting failures and clearing active state."""
            stream_started = False
            try:
                prepared_text = attach_local_mentions(
                    Context(self.client, self.session_id, self.state),
                    task_text,
                    cancelled=lambda: self._cancel_requested,
                )
                if self._cancel_requested:
                    self.call_from_thread(
                        self.append_markup,
                        "[yellow]Attachment submission cancelled; no Turn was sent. "
                        "Any completed uploads remain in the workspace.[/yellow]",
                    )
                    return
                stream_started = True
                with _request_turn(self.client, self.session_id, prepared_text, self.state) as response:
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
            except (OSError, ValueError, KeyError) as exc:
                self.call_from_thread(
                    self.append_ansi, f"Attachment submission failed: {exc}" if not stream_started else str(exc)
                )
            finally:
                if not stream_started:
                    self.call_from_thread(self._restore_attachment_draft, task_text)
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


def run_interactive_cli(
    base_url: str, session_id: str, *, settings: FleetSettings | None = None, setup_token: str = ""
) -> str | None:
    """Connect to the API and run the Textual full-screen chat TUI (or non-TTY fallback) until exit."""
    validate_session_id(session_id)
    state = SessionState(mlflow_settings=settings, setup_available=bool(setup_token))
    # FastAPI's SSE routing emits a `: ping` keepalive every 15 seconds while the Turn
    # generator is idle, so the 300-second read timeout is only a safety net.
    with httpx.Client(
        base_url=base_url,
        timeout=httpx.Timeout(300.0, connect=10.0),
    ) as client:
        if sys.stdin.isatty() and CONSOLE.is_terminal:
            return FleetTextualApp(client, session_id, state).run()

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
            if user_input == "/setup" and state.setup_available:
                return "setup"
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
    return None
