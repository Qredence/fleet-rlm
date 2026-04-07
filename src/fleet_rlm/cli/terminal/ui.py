"""UI and rendering functions for the terminal chat interface.

This module provides UI and rendering components extracted from terminal_chat.py.
All functions are stateless and take required parameters explicitly.
"""

from __future__ import annotations

import getpass
import json
import re
from pathlib import Path
from typing import Any

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.shortcuts import input_dialog, radiolist_dialog
from prompt_toolkit.styles import Style
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text


class _FleetCompleter(Completer):
    """Slash + file mention completer used by the chat composer.

    This completer provides autocomplete for slash commands and file mentions
    (using @ prefix) in the terminal chat interface.
    """

    def __init__(
        self,
        command_specs: list[tuple[str, str]],
        command_dispatch_names: list[str] | None = None,
    ) -> None:
        """Initialize the completer.

        Args:
            command_specs: List of (command_name, summary) tuples.
            command_dispatch_names: List of command dispatch names for tool commands.
        """
        dispatch_names = command_dispatch_names or []
        self._slash_entries: list[tuple[str, str]] = sorted(
            command_specs
            + [(f"/{name}", "tool command") for name in sorted(dispatch_names)],
            key=lambda item: item[0],
        )

    def get_completions(self, document: Any, complete_event: Any):
        """Generate completions for the current input."""
        text = document.text_before_cursor

        if text.startswith("/"):
            token = text.split(maxsplit=1)[0]
            for command, summary in self._slash_entries:
                if command.startswith(token):
                    yield Completion(
                        command,
                        start_position=-len(token),
                        display=command,
                        display_meta=summary,
                    )
            if text.startswith("/settings "):
                sub = (
                    text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
                )
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


def _bottom_toolbar(*, is_processing: bool) -> HTML:
    """Return the bottom toolbar HTML.

    Args:
        is_processing: Whether the session is currently processing a request.

    Returns:
        HTML formatted toolbar text.
    """
    mode = "thinking" if is_processing else "idle"
    return HTML(
        "<trace>"
        " Type @ to mention files, / for commands, or ? for shortcuts "
        f" · mode={mode}"
        "</trace>"
    )


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
            prefix_dir = (
                f"{as_path.parent.as_posix().rstrip('/')}/"
                if as_path.parent.as_posix()
                else ""
            )
            name_prefix = as_path.name
        elif "/" in query:
            maybe_dir, name_prefix = query.rsplit("/", 1)
            resolved = (base / maybe_dir).resolve()
            if resolved.exists() and resolved.is_dir():
                root = resolved
                prefix_dir = maybe_dir.rstrip("/") + "/"

    suggestions: list[str] = []
    try:
        entries = sorted(
            root.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower())
        )
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
    except (
        Exception
    ):  # prompt_toolkit dialog unavailable; fall back to plain print-based menu
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
) -> None:
    """Render the shell UI layout.

    Args:
        console: The Rich console instance.
        session_id: Current session identifier.
        model: Current model name.
        trace_mode: Current trace mode.
        last_status: Last status message.
        transcript: List of (role, content) transcript entries.
        is_processing: Whether currently processing a request.
        draft_assistant: Draft assistant response text.
    """
    header = Panel(
        Text.from_markup(
            f"[bold]fleet[/]  "
            f"[dim]session[/]={session_id}  "
            f"[dim]model[/]={model}  "
            f"[dim]trace[/]={trace_mode}  "
            f"[dim]status[/]={last_status}"
        ),
        border_style="cyan",
        padding=(0, 1),
    )

    body_text = Text()
    for role, content in transcript[-30:]:
        role_style = {
            "you": "bold white",
            "assistant": "bold cyan",
            "status": "dim",
            "tool": "magenta",
            "thinking": "dim",
            "warning": "yellow",
            "error": "red",
            "result": "blue",
        }.get(role, "white")
        body_text.append(f"{role}> ", style=role_style)
        body_text.append(content + "\n\n")

    if is_processing and draft_assistant:
        body_text.append("assistant> ", style="bold cyan")
        body_text.append(draft_assistant + "\n")

    transcript_panel = Panel(
        body_text if body_text.plain.strip() else Text("No messages yet.", style="dim"),
        border_style="bright_black",
        title="chat",
    )

    hint = (
        "Enter=send • Shift+Enter=newline • /=command palette • "
        "Ctrl+C=interrupt • /help=commands"
    )
    footer = Panel(Text(hint, style="dim"), border_style="bright_black")

    layout = Layout()
    layout.split_column(
        Layout(header, size=3),
        Layout(transcript_panel, ratio=1),
        Layout(footer, size=3),
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


# Role style mapping for transcript rendering
ROLE_STYLES: dict[str, str] = {
    "you": "bold white",
    "assistant": "bold cyan",
    "status": "dim",
    "tool": "magenta",
    "thinking": "dim",
    "warning": "yellow",
    "error": "red",
    "result": "blue",
}
