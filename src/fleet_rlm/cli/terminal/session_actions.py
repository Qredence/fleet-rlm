"""Action helpers for the terminal chat session."""

from __future__ import annotations

import os
from typing import Any

from rich.table import Table

from fleet_rlm.integrations.daytona import DaytonaConfigError, resolve_daytona_config

from .commands import _authorize_command, _print_unknown_command, print_command_palette
from .settings import (
    run_long_context,
    run_settings,
    settings_llm,
)
from .ui import _badge


def run_settings_action(session: Any, section: str) -> None:
    run_settings(session, section)


def settings_llm_action(session: Any, *, model_only: bool) -> None:
    settings_llm(session, model_only=model_only)


def run_long_context_action(session: Any, arg_text: str) -> None:
    run_long_context(session, arg_text)


def print_status(session: Any, agent: Any) -> None:
    """Print the current session and agent status."""
    has_model = bool(os.environ.get("DSPY_LM_MODEL"))
    has_api_key = bool(
        os.environ.get("DSPY_LLM_API_KEY") or os.environ.get("DSPY_LM_API_KEY")
    )
    llm_ready = has_model and has_api_key

    try:
        resolve_daytona_config()
        daytona_ready = True
        daytona_detail = "api_key=yes, api_url=yes, target=yes"
    except DaytonaConfigError as exc:
        daytona_ready = False
        daytona_detail = str(exc)

    docs_result = agent.list_documents()
    docs_loaded = len(docs_result.get("documents", []))
    active_alias = str(docs_result.get("active_alias", ""))

    table = Table(title="fleet status", show_lines=True)
    table.add_column("Component", style="bold")
    table.add_column("State", style="bold")
    table.add_column("Details")

    table.add_row(
        "Planner LM",
        _badge(llm_ready),
        f"model={'set' if has_model else 'missing'}, api_key={'set' if has_api_key else 'missing'}",
    )
    table.add_row(
        "Daytona runtime",
        _badge(daytona_ready),
        daytona_detail,
    )
    table.add_row(
        "Volume",
        "[green]configured[/]",
        f"configured={session.volume_name or 'unset'}",
    )
    table.add_row(
        "Documents",
        "[green]ok[/]",
        f"loaded={docs_loaded}, active={active_alias or 'none'}",
    )
    allowed = sorted(
        command
        for command, policy in session.command_permissions.items()
        if policy == "allow"
    )
    denied = sorted(
        command
        for command, policy in session.command_permissions.items()
        if policy == "deny"
    )
    table.add_row(
        "Permissions",
        "[green]ok[/]",
        f"allow_session={len(allowed)}, denied={len(denied)}",
    )

    session.console.print(table)
    if session.trace_mode == "verbose":
        session._print_result(
            {
                "session_id": session.session_id,
                "trace_mode": session.trace_mode,
                "permissions": dict(sorted(session.command_permissions.items())),
                "daytona_ready": daytona_ready,
            },
            title="status payload",
        )


def print_command_palette_action(session: Any, agent: Any) -> bool:
    return print_command_palette(session, agent)


def print_unknown_command_action(session: Any, command: str) -> None:
    _print_unknown_command(session, command)


def print_permissions(session: Any) -> None:
    """Print the current permission policies."""
    table = Table(title="command permissions")
    table.add_column("Command", style="cyan")
    table.add_column("Policy", style="bold")
    if not session.command_permissions:
        table.add_row("*", "ask (default)")
    else:
        for command, policy in sorted(session.command_permissions.items()):
            table.add_row(command, policy)
    session.console.print(table)


def authorize_command(session: Any, *, command: str) -> bool:
    return _authorize_command(session, command=command)


def show_shortcuts(session: Any) -> None:
    session._append_transcript(
        "status",
        (
            "Shortcuts: / opens command palette - @ mentions files - "
            "Ctrl+C interrupts - /trace compact|verbose|off"
        ),
    )
    session._render_shell()
