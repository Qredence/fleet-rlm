"""Textual widgets for the interactive Fleet RLM chat TUI."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import OptionList, Static, TextArea

import fleet_rlm.tui.render as render_mod
from fleet_rlm.tui.render import (
    CollapsibleItem,
    TurnTracker,
    _format_tool_payload,
    _is_omitted_payload,
    confidence_bar_text,
)

if TYPE_CHECKING:
    from fleet_rlm.tui.app import FleetTextualApp

_GRADIENT_STOPS = (
    (82, 82, 91),  # zinc
    (161, 161, 170),  # silver
    (52, 211, 153),  # emerald
    (251, 191, 36),  # amber
    (161, 161, 170),  # silver
    (82, 82, 91),  # zinc
)


def _as_fleet_app(app: App[Any]) -> FleetTextualApp | None:
    return cast("FleetTextualApp", app) if hasattr(app, "_sync_input_state") else None


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
        app = _as_fleet_app(self.app)
        if app is not None and app.is_running:
            app._sync_input_state(text)

    @property
    def cursor_offset(self) -> int:
        """Return the cursor's character offset using the DocumentBase line contract."""
        row, column = self.cursor_location
        return sum(len(line) + len(self.document.newline) for line in self.document.lines[:row]) + column

    def move_cursor_to_offset(self, offset: int) -> None:
        """Move to a character offset without depending on a concrete document subclass."""
        lines = self.text[:offset].split(self.document.newline)
        self.move_cursor((len(lines) - 1, len(lines[-1])))

    async def _on_paste(self, event: events.Paste) -> None:
        """Insert the full pasted text (including all paragraphs and line breaks) at the cursor."""
        event.stop()
        event.prevent_default()
        if event.text and not self.read_only:
            self.insert(event.text)

    async def _on_key(self, event: events.Key) -> None:
        """Complete or submit on Enter; preserve ordered editing, menu, and history keys."""
        app = _as_fleet_app(self.app)
        if app is not None and event.key in {
            "enter",
            "tab",
            "escape",
            "ctrl+c",
            "up",
            "down",
            "pageup",
            "pagedown",
        }:
            # Changed notifications reach the App asynchronously; navigation must see the
            # text already inserted by preceding keys, not the last rendered suggestions.
            app._sync_input_state(self.text)
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            if app is not None:
                if app._complete_on_enter():
                    return
                text = self.text.strip()
                self.value = ""
                app.query_one("#slash-popup", OptionList).display = False
                app.submit_text(text)
            return
        if event.key in {"shift+enter", "alt+enter", "ctrl+j"}:
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if app is not None:
            # Keep editing/menu keys on this widget's FIFO queue. App-level priority
            # bindings can overtake earlier arrow or printable keys in a terminal burst.
            if event.key in {"tab", "escape", "ctrl+c"}:
                event.stop()
                event.prevent_default()
                if event.key == "tab":
                    app.action_complete_slash()
                elif event.key == "escape":
                    app.action_dismiss_or_cancel()
                else:
                    app.action_clear_or_cancel()
                return
            popup = app.query_one("#slash-popup", OptionList)
            popup_open = popup.display and bool(app._popup_items)
            if popup_open and event.key in {"pageup", "pagedown"}:
                event.stop()
                event.prevent_default()
                if event.key == "pageup":
                    popup.action_page_up()
                else:
                    popup.action_page_down()
                return
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


class _CommandMenu(OptionList, can_focus=False):
    """Scrollable suggestions; typing and keyboard navigation keep the composer focused."""


class _StepWidget(Static):
    """Clickable collapsible widget for one REPL cell, Thinking block, or Tool invocation."""

    def __init__(self, item: CollapsibleItem, on_toggle: Callable[[], None]) -> None:
        super().__init__()
        self.item = item
        self._on_toggle = on_toggle

    def on_click(self, event: events.Click) -> None:
        """Toggle this step open or closed on left mouse click."""
        event.stop()
        app = _as_fleet_app(self.app)
        if app is not None:
            with app._lock:
                self.item.toggle()
        else:
            self.item.toggle()
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
                self._steps_box.mount(_StepWidget(item, self.sync_from_tracker))
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

        self.sync_status()

    def sync_status(self) -> None:
        """Update elapsed time and spinner without invalidating unchanged step renderings."""
        t = self.tracker
        footer = t.status_line()
        if t.terminal_event is not None and t.terminal_event.get("trace_id"):
            footer.append(f" · trace {t.terminal_event['trace_id']}", style="#71717a")
        self._footer_static.update(footer)
