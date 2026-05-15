"""UI and rendering functions for the terminal chat interface.

This module provides UI and rendering components extracted from terminal_chat.py.
All functions are stateless and take required parameters explicitly.
"""

from __future__ import annotations

import getpass
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.shortcuts import input_dialog, radiolist_dialog
from prompt_toolkit.styles import Style
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text

if TYPE_CHECKING:
    from rich.console import Console


class _FleetCompleter(Completer):
    """Slash + file mention completer used by the chat composer.

    Provides inline filter-as-you-type palette for slash commands and
    file mentions (using @ prefix).
    """

    def __init__(
        self,
        command_specs: list[tuple[str, str]] | list[tuple[str, str, str]],
        command_dispatch_names: list[str] | None = None,
    ) -> None:
        """Initialize the completer.

        Args:
            command_specs: List of (name, summary) or (name, summary, category) tuples.
            command_dispatch_names: List of command dispatch names for tool commands.
        """
        dispatch_names = command_dispatch_names or []
        # Normalize to 3-tuples (name, summary, category)
        normalized: list[tuple[str, str, str]] = []
        for spec in command_specs:
            match spec:
                case (name, summary, category):
                    normalized.append((name, summary, category))
                case (name, summary):
                    normalized.append((name, summary, ""))
        for name in sorted(dispatch_names):
            normalized.append((f"/{name}", "tool command", "tools"))
        self._slash_entries: list[tuple[str, str, str]] = sorted(normalized, key=lambda item: item[0])

    def get_completions(self, document: Any, complete_event: Any):
        """Generate completions for the current input."""
        text = document.text_before_cursor

        if text.startswith("/"):
            token = text.split(maxsplit=1)[0]
            for command, summary, category in self._slash_entries:
                # Show ALL commands when token is just "/" (inline palette)
                if token == "/" or command.startswith(token):
                    meta = f"{summary}  [{category}]" if category else summary
                    yield Completion(
                        command,
                        start_position=-len(token),
                        display=command,
                        display_meta=meta,
                    )
            if text.startswith("/settings "):
                sub = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
                for choice in ("llm", "model"):
                    if choice.startswith(sub):
                        yield Completion(
                            choice,
                            start_position=-len(sub),
                            display=choice,
                            display_meta="settings scope",
                        )
            return

        mention = re.search(r"@(\S*)$", text)
        if not mention:
            return
        prefix = mention.group(1)
        for candidate in _iter_mention_paths(prefix):
            yield Completion(
                candidate,
                start_position=-len(prefix),
                display=f"@{candidate}",
                display_meta="file path",
            )


def _history_path() -> Path:
    """Return the path to the chat history file.

    Returns:
        Path to ~/.fleet/history.txt
    """
    return Path.home() / ".fleet" / "history.txt"


# ---------------------------------------------------------------------------
# Persistent user preferences
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field  # noqa: E402


class TerminalPreferences(BaseModel):
    """Persisted user preferences for the terminal chat."""

    trace_mode: str = "compact"
    command_permissions: dict[str, str] = Field(default_factory=dict)
    theme: str = "dark"


def _preferences_path() -> Path:
    """Return the path to the preferences file.

    Returns:
        Path to ~/.fleet/preferences.json
    """
    return Path.home() / ".fleet" / "preferences.json"


def load_preferences() -> TerminalPreferences:
    """Load preferences from disk, returning defaults if file missing/corrupt."""
    path = _preferences_path()
    if path.exists():
        try:
            return TerminalPreferences.model_validate_json(path.read_text())
        except Exception:
            pass
    return TerminalPreferences()


def save_preferences(prefs: TerminalPreferences) -> None:
    """Persist preferences to disk."""
    path = _preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prefs.model_dump_json(indent=2))


def _badge(ok: bool) -> str:
    """Return a status badge string.

    Args:
        ok: Whether the status is OK.

    Returns:
        Rich markup string for the badge.
    """
    return "[green]OK[/]" if ok else "[yellow]WARN[/]"


def _mask_secret(value: str) -> str:
    """Mask a secret value for display.

    Args:
        value: The secret value to mask.

    Returns:
        Masked string showing only first 3 and last 2 characters.
    """
    if not value:
        return ""
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}...{value[-2:]}"


def _prompt_label() -> HTML:
    """Return the prompt label HTML.

    Returns:
        HTML formatted prompt label.
    """
    return HTML("<prompt>❯ </prompt>")


def _bottom_toolbar(
    *,
    is_processing: bool,
    model: str = "",
    docs_count: int = 0,
    trace_mode: str = "compact",
) -> HTML:
    """Return the bottom toolbar HTML.

    Args:
        is_processing: Whether the session is currently processing a request.
        model: Current model name.
        docs_count: Number of loaded documents.
        trace_mode: Current trace mode.

    Returns:
        HTML formatted toolbar text.
    """
    truncated_model = (model[:20] + "...") if len(model) > 20 else model
    model_part = f"model={_escape_html(truncated_model)}" if truncated_model else "model=--"
    docs_part = f"docs={docs_count}"
    trace_part = f"trace={_escape_html(trace_mode)}"
    if is_processing:
        status_part = "<b>thinking...</b>"
    else:
        status_part = "ready"
    hints_part = "<b>/</b> cmds  <b>@</b> files"
    return HTML(f"<trace> {model_part} | {docs_part} | {trace_part} | {status_part} | {hints_part}</trace>")


def _escape_html(text: str) -> str:
    """Escape HTML special characters for prompt_toolkit HTML.

    Args:
        text: The text to escape.

    Returns:
        HTML-safe string.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _iter_mention_paths(prefix: str, *, limit: int = 40) -> list[str]:
    """Iterate over file paths matching a prefix for @ mention completion.

    Args:
        prefix: The path prefix to match.
        limit: Maximum number of suggestions to return.

    Returns:
        List of matching path suggestions.
    """
    base = Path.cwd()
    query = prefix.strip()
    prefix_dir = ""
    name_prefix = query
    root = base

    if query:
        as_path = Path(query)
        if as_path.is_absolute():
            parent = as_path.parent if as_path.parent.as_posix() else Path("/")
            root = parent if parent.exists() else Path("/")
            prefix_dir = f"{as_path.parent.as_posix().rstrip('/')}/" if as_path.parent.as_posix() else ""
            name_prefix = as_path.name
        elif "/" in query:
            maybe_dir, name_prefix = query.rsplit("/", 1)
            resolved = (base / maybe_dir).resolve()
            if resolved.exists() and resolved.is_dir():
                root = resolved
                prefix_dir = maybe_dir.rstrip("/") + "/"

    suggestions: list[str] = []
    try:
        entries = sorted(root.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower()))
    except Exception:  # directory may not exist or be unreadable
        return suggestions

    if not query:
        suggestions.append(str(base))

    lowered = name_prefix.lower()
    for entry in entries:
        if lowered and not entry.name.lower().startswith(lowered):
            continue
        suffix = "/" if entry.is_dir() else ""
        suggestion = f"{prefix_dir}{entry.name}{suffix}"
        suggestions.append(suggestion)
        if len(suggestions) >= limit:
            break
    return suggestions


def _prompt_value(*, key: str, default: str, secret: bool) -> str:
    """Prompt the user for a value with optional secret masking.

    Args:
        key: The configuration key name.
        default: Default value to show.
        secret: Whether to mask the input.

    Returns:
        The user's input value.
    """
    shown_default = _mask_secret(default) if secret else default
    suffix = f" [{shown_default}]" if shown_default else ""
    try:
        if secret:
            raw = getpass.getpass(f"{key}{suffix}: ").strip()
        else:
            raw = input(f"{key}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):  # pragma: no cover - interactive path
        return ""
    return raw


def _prompt_choice(
    prompt: str,
    choices: list[str],
    *,
    allow_freeform: bool,
) -> str:
    """Prompt the user to select from a list of choices.

    Args:
        prompt: The prompt text to display.
        choices: List of choices to present.
        allow_freeform: Whether to allow custom input.

    Returns:
        The selected choice or custom input.
    """
    try:
        values = [(str(index), choice) for index, choice in enumerate(choices, start=1)]
        if allow_freeform:
            values.append(("0", "Custom input"))
        picked = radiolist_dialog(
            title="Select option",
            text=prompt,
            values=values,
            style=_dialog_style(),
        ).run()
        if picked is None:
            return ""
        if picked == "0":
            custom = input_dialog(
                title="Custom input",
                text=prompt,
                style=_dialog_style(),
            ).run()
            return (custom or "").strip()
        if picked.isdigit():
            number = int(picked)
            if 1 <= number <= len(choices):
                return choices[number - 1]
    except Exception:  # prompt_toolkit dialog unavailable; fall back to plain print-based menu
        pass

    print(prompt)
    for index, choice in enumerate(choices, start=1):
        print(f"  {index}) {choice}")
    if allow_freeform:
        print("  0) custom input")
    while True:
        selection = input("Select option: ").strip()
        if selection.isdigit():
            number = int(selection)
            if 1 <= number <= len(choices):
                return choices[number - 1]
            if allow_freeform and number == 0:
                return input("Custom value: ").strip()
        if allow_freeform and selection:
            return selection
        print("Invalid selection.")


def _dialog_style() -> Style:
    """Return the dialog style for prompt_toolkit dialogs.

    Returns:
        Style object with dark theme colors.
    """
    return Style.from_dict(
        {
            "dialog": "bg:#101114",
            "dialog frame.label": "fg:#77d6ff bold",
            "dialog.body": "fg:#f0f3f7",
            "dialog shadow": "bg:#050607",
            "button.focused": "bg:#2d7ff9 #ffffff",
        }
    )


def _build_header(
    *,
    session_id: str,
    model: str,
    trace_mode: str,
    last_status: str,
    width: int,
    styles: dict[str, str],
) -> Panel | None:
    """Build adaptive header panel based on terminal width."""
    if width < 60:
        return None
    border = styles.get("header_border", "cyan")
    if width < 80:
        text = Text.from_markup(f"[bold]fleet[/]  [dim]model[/]={model}  [dim]status[/]={last_status}")
    elif width < 100:
        text = Text.from_markup(
            f"[bold]fleet[/]  [dim]model[/]={model}  [dim]trace[/]={trace_mode}  [dim]status[/]={last_status}"
        )
    else:
        text = Text.from_markup(
            f"[bold]fleet[/]  [dim]session[/]={session_id}  "
            f"[dim]model[/]={model}  [dim]trace[/]={trace_mode}  "
            f"[dim]status[/]={last_status}"
        )
    return Panel(text, border_style=border, padding=(0, 1))


def _build_footer(*, width: int, styles: dict[str, str]) -> Panel | None:
    """Build adaptive footer panel based on terminal width."""
    if width < 60:
        return None
    border = styles.get("panel_border", "bright_black")
    if width < 80:
        hint = "/ cmds  ? help  ^C stop"
    else:
        hint = "Enter=send • Shift+Enter=newline • /=command palette • Ctrl+C=interrupt • /help=commands"
    return Panel(Text(hint, style=styles.get("hint", "dim")), border_style=border)


def _header_size(width: int) -> int:
    """Return header layout size (0 when hidden)."""
    return 0 if width < 60 else 3


def _footer_size(width: int) -> int:
    """Return footer layout size (0 when hidden)."""
    return 0 if width < 60 else 3


def build_shell_layout(
    *,
    session_id: str,
    model: str,
    trace_mode: str,
    last_status: str,
    transcript: list[tuple[str, str]],
    is_processing: bool,
    draft_assistant: str = "",
    console_width: int = 120,
    console_height: int = 40,
    scroll_offset: int = 0,
    in_tmux: bool = False,
    theme: dict[str, str] | None = None,
) -> Layout:
    """Build the shell UI layout as a renderable (no side effects).

    Adapts header, footer, and viewport height based on terminal dimensions.
    """
    styles = theme or THEMES["dark"]

    # Compute dynamic viewport height
    h_size = _header_size(console_width)
    f_size = _footer_size(console_width)
    reserved = h_size + f_size + 2  # borders/margins
    if in_tmux:
        reserved += 2
    viewport_height = max(5, console_height - reserved)

    # Build adaptive header/footer
    header = _build_header(
        session_id=session_id,
        model=model,
        trace_mode=trace_mode,
        last_status=last_status,
        width=console_width,
        styles=styles,
    )
    footer = _build_footer(width=console_width, styles=styles)

    # Build transcript body
    body_text = Text()
    if scroll_offset > 0:
        end_idx = len(transcript) - scroll_offset
        start_idx = max(0, end_idx - viewport_height)
        visible = transcript[start_idx:end_idx]
        body_text.append(
            f"[scrolled {scroll_offset} messages back — press End to return]\n\n",
            style=styles.get("hint", "dim"),
        )
    else:
        visible = transcript[-viewport_height:]

    for role, content in visible:
        role_style = styles.get(role, "white")
        body_text.append(f"{role}> ", style=role_style)
        body_text.append(content + "\n\n")

    if is_processing and draft_assistant and scroll_offset == 0:
        body_text.append("assistant> ", style=styles.get("assistant", "bold cyan"))
        body_text.append(draft_assistant + "\n")

    transcript_panel = Panel(
        body_text if body_text.plain.strip() else Text("No messages yet.", style=styles.get("status", "dim")),
        border_style=styles.get("panel_border", "bright_black"),
        title="chat",
    )

    # Assemble layout dynamically
    children = []
    if header is not None:
        children.append(Layout(header, size=3))
    children.append(Layout(transcript_panel, ratio=1))
    if footer is not None:
        children.append(Layout(footer, size=3))

    layout = Layout()
    layout.split_column(*children)
    return layout


def _render_shell(
    *,
    console: Any,
    session_id: str,
    model: str,
    trace_mode: str,
    last_status: str,
    transcript: list[tuple[str, str]],
    is_processing: bool,
    draft_assistant: str = "",
    console_width: int = 120,
    console_height: int = 40,
    scroll_offset: int = 0,
    in_tmux: bool = False,
    theme: dict[str, str] | None = None,
) -> None:
    """Legacy render: clear + print. Used when Live is not active."""
    layout = build_shell_layout(
        session_id=session_id,
        model=model,
        trace_mode=trace_mode,
        last_status=last_status,
        transcript=transcript,
        is_processing=is_processing,
        draft_assistant=draft_assistant,
        console_width=console_width,
        console_height=console_height,
        scroll_offset=scroll_offset,
        in_tmux=in_tmux,
        theme=theme,
    )
    console.clear()
    console.print(layout)


def _print_banner(
    *,
    console: Any,
    session_id: str,
    model: str,
    planner_ready: bool,
    workspace: Path,
) -> None:
    """Print the startup banner.

    Args:
        console: The Rich console instance.
        session_id: Current session identifier.
        model: Current model name.
        planner_ready: Whether the planner LM is configured.
        workspace: Current workspace path.
    """
    planner_text = "[green]ready[/]" if planner_ready else "[yellow]not configured[/]"
    content = (
        "[bold cyan]fleet[/]  [dim]Copilot-style mode[/]\n"
        "Describe a task to get started.\n\n"
        "Use [bold]/model[/], [bold]/settings[/], [bold]/status[/], and "
        "[bold]/[/] for the command palette.\n"
        f"[dim]session={session_id}  planner={planner_text}[/]"
    )
    console.print(Panel(content, title="fleet chat", border_style="cyan", expand=False))
    console.print(f"[dim]• workspace:[/] {workspace}    [dim]model:[/] {model}")


def _print_result_inline(
    *,
    console: Any,
    result: dict[str, Any],
    title: str,
) -> None:
    """Print a result as a panel.

    Args:
        console: The Rich console instance.
        result: Result dictionary to print.
        title: Title for the result.
    """
    rendered = json.dumps(result, indent=2, sort_keys=True, default=str)
    console.print(Panel(rendered, title=title, border_style="blue"))


def render_onboarding_banner(console: "Console") -> None:
    """Display a one-time onboarding guide for new users."""
    content = Text()
    content.append("Welcome to Fleet! ", style="bold cyan")
    content.append("Here's how to get started:\n\n")
    content.append("  1. ", style="bold")
    content.append("/settings llm", style="bold green")
    content.append("  — configure your LLM provider\n")
    content.append("  2. ", style="bold")
    content.append("/docs <path>", style="bold green")
    content.append("    — load a document for analysis\n")
    content.append("  3. ", style="bold")
    content.append("Type a question", style="bold green")
    content.append("  — start chatting with the agent\n")
    content.append("  4. ", style="bold")
    content.append("/", style="bold green")
    content.append("                — browse all commands\n\n")
    content.append("This message only appears once. ", style="dim")
    content.append("Use ", style="dim")
    content.append("?", style="dim bold")
    content.append(" for keyboard shortcuts.", style="dim")

    console.print(Panel(content, border_style="cyan", title="[bold]Getting Started[/]", padding=(1, 2)))


# ---------------------------------------------------------------------------
# Theme definitions
# ---------------------------------------------------------------------------

THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "you": "bold white",
        "assistant": "bold cyan",
        "status": "dim",
        "tool": "magenta",
        "thinking": "dim",
        "warning": "yellow",
        "error": "red",
        "result": "blue",
        "header_border": "cyan",
        "panel_border": "bright_black",
        "hint": "dim italic",
    },
    "light": {
        "you": "bold black",
        "assistant": "bold blue",
        "status": "bright_black",
        "tool": "dark_violet",
        "thinking": "bright_black",
        "warning": "dark_orange",
        "error": "dark_red",
        "result": "dark_blue",
        "header_border": "blue",
        "panel_border": "grey50",
        "hint": "italic bright_black",
    },
}

# Backward-compatible alias so any remaining references still work.
ROLE_STYLES: dict[str, str] = THEMES["dark"]


def get_theme(name: str = "dark") -> dict[str, str]:
    """Get theme dict by name, falling back to dark."""
    return THEMES.get(name, THEMES["dark"])


def detect_terminal_theme() -> str:
    """Attempt to detect if terminal is light or dark.

    Uses the ``COLORFGBG`` environment variable set by some terminals
    (iTerm2, xterm) to guess the background brightness.  Falls back to
    ``"dark"`` when the variable is absent or unparseable.
    """
    import os

    colorfgbg = os.environ.get("COLORFGBG", "")
    if colorfgbg:
        parts = colorfgbg.split(";")
        if len(parts) >= 2:
            try:
                bg = int(parts[-1])
                return "light" if bg > 8 else "dark"
            except ValueError:
                pass
    return "dark"
