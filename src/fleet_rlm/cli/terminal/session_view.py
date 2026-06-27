"""Rendering and transcript helpers for the terminal chat session."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prompt_toolkit.formatted_text import HTML

from .ui import _bottom_toolbar, _print_banner, _render_shell


def print_result(session: Any, result: dict[str, Any], *, title: str) -> None:
    """Print a result dictionary as transcript JSON."""
    rendered = json.dumps(result, indent=2, sort_keys=True, default=str)
    append_transcript(session, "result", f"{title}\n{rendered}")
    session.last_status = f"{title} complete"
    render_shell(session)


def print_banner(session: Any, *, planner_ready: bool) -> None:
    """Print the startup banner."""
    _print_banner(
        console=session.console,
        session_id=session.session_id,
        model=session.config.llm.model,
        planner_ready=planner_ready,
        workspace=Path.cwd(),
    )


def bottom_toolbar(
    session: Any,
    *,
    model: str = "",
    docs_count: int = 0,
    trace_mode: str = "compact",
) -> HTML:
    """Return the bottom toolbar HTML."""
    return _bottom_toolbar(
        is_processing=session.is_processing,
        model=model,
        docs_count=docs_count,
        trace_mode=trace_mode,
    )


def print_warning(session: Any, message: str) -> None:
    """Print a warning message."""
    append_transcript(session, "warning", message)
    session.last_status = "warning"
    render_shell(session)


def print_error(session: Any, message: str, *, hint: str | None = None) -> None:
    """Print an error message with an optional recovery hint."""
    display = message
    if hint:
        display = f"{message}\nhint: {hint}"
    append_transcript(session, "error", display)
    session.last_status = "error"
    render_shell(session)


def append_transcript(session: Any, role: str, content: str) -> None:
    """Append a message to the transcript buffer."""
    text = content.strip()
    if not text:
        return
    session.transcript.append((role, text))
    if len(session.transcript) > 200:
        session.transcript = session.transcript[-200:]
    # Auto-scroll to bottom on new content
    if hasattr(session, "_scroll_offset"):
        session._scroll_offset = 0


def render_shell(session: Any, *, draft_assistant: str = "") -> None:
    """Render the shell UI layout.

    If the session has an active Live instance (_live), uses differential
    updates for flicker-free rendering. Otherwise falls back to clear+print.
    """
    import os

    from .ui import build_shell_layout, get_theme

    prefs = getattr(session, "_preferences", None)
    theme = get_theme(prefs.theme) if prefs is not None else None

    scroll_offset = getattr(session, "_scroll_offset", 0)
    console = session.console
    width, height = console.size.width, console.size.height
    in_tmux = bool(os.environ.get("TMUX"))

    live = getattr(session, "_live", None)
    if live is not None and live.is_started:
        live.update(
            build_shell_layout(
                session_id=session.session_id,
                model=session.config.llm.model,
                trace_mode=session.trace_mode,
                last_status=session.last_status,
                transcript=session.transcript,
                is_processing=session.is_processing,
                draft_assistant=draft_assistant,
                console_width=width,
                console_height=height,
                scroll_offset=scroll_offset,
                in_tmux=in_tmux,
                theme=theme,
            )
        )
    else:
        _render_shell(
            console=console,
            session_id=session.session_id,
            model=session.config.llm.model,
            trace_mode=session.trace_mode,
            last_status=session.last_status,
            transcript=session.transcript,
            is_processing=session.is_processing,
            draft_assistant=draft_assistant,
            console_width=width,
            console_height=height,
            scroll_offset=scroll_offset,
            in_tmux=in_tmux,
            theme=theme,
        )
