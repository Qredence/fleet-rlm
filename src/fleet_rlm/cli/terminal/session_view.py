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
        model=session.config.agent.model,
        planner_ready=planner_ready,
        workspace=Path.cwd(),
    )


def bottom_toolbar(session: Any) -> HTML:
    """Return the bottom toolbar HTML."""
    return _bottom_toolbar(is_processing=session.is_processing)


def print_warning(session: Any, message: str) -> None:
    """Print a warning message."""
    append_transcript(session, "warning", message)
    session.last_status = "warning"
    render_shell(session)


def print_error(session: Any, message: str) -> None:
    """Print an error message."""
    append_transcript(session, "error", message)
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


def render_shell(session: Any, *, draft_assistant: str = "") -> None:
    """Render the shell UI layout."""
    _render_shell(
        console=session.console,
        session_id=session.session_id,
        model=session.config.agent.model,
        trace_mode=session.trace_mode,
        last_status=session.last_status,
        transcript=session.transcript,
        is_processing=session.is_processing,
        draft_assistant=draft_assistant,
    )
