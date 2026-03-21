"""Action helpers for the terminal chat session."""

from __future__ import annotations

import os
from typing import Any

from rich.table import Table

from fleet_rlm import runners
from fleet_rlm.utils.modal import get_default_volume_name, load_modal_config

from .commands import _authorize_command, _print_unknown_command, print_command_palette
from .settings import (
    check_secret,
    check_secret_key,
    run_long_context,
    run_settings,
    settings_llm,
    settings_modal,
)
from .ui import _badge


def run_settings_action(session: Any, section: str) -> None:
    run_settings(session, section)


def settings_llm_action(session: Any, *, model_only: bool) -> None:
    settings_llm(session, model_only=model_only)


def settings_modal_action(session: Any) -> None:
    settings_modal(session)


def run_long_context_action(session: Any, arg_text: str) -> None:
    run_long_context(session, arg_text)


def check_secret_action(session: Any) -> None:
    check_secret(session)


def check_secret_key_action(session: Any, *, key: str) -> None:
    check_secret_key(session, key=key)


def print_status(session: Any, agent: Any) -> None:
    """Print the current session and agent status."""
    has_model = bool(os.environ.get("DSPY_LM_MODEL"))
    has_api_key = bool(
        os.environ.get("DSPY_LLM_API_KEY") or os.environ.get("DSPY_LM_API_KEY")
    )
    llm_ready = has_model and has_api_key

    modal_cfg = load_modal_config()
    modal_from_env = bool(
        os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET")
    )
    modal_from_profile = bool(
        modal_cfg.get("token_id") and modal_cfg.get("token_secret")
    )
    modal_ready = modal_from_env or modal_from_profile

    docs_result = agent.list_documents()
    docs_loaded = len(docs_result.get("documents", []))
    active_alias = str(docs_result.get("active_alias", ""))

    secret_check: dict[str, Any]
    secret_ok = False
    try:
        secret_check = runners.check_secret_presence(secret_name=session.secret_name)
        if secret_check:
            secret_ok = all(bool(v) for v in secret_check.values())
    except Exception as exc:  # pragma: no cover - runtime path
        secret_check = {"error": str(exc)}

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
        "Modal credentials",
        _badge(modal_ready),
        f"env={'yes' if modal_from_env else 'no'}, profile={'yes' if modal_from_profile else 'no'}",
    )
    table.add_row(
        f"Modal secret ({session.secret_name})",
        _badge(secret_ok),
        ", ".join(f"{k}={'yes' if bool(v) else 'no'}" for k, v in secret_check.items()),
    )
    table.add_row(
        "Volume",
        "[green]configured[/]",
        f"configured={session.volume_name}, default={get_default_volume_name()}",
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
                "secret_check": secret_check,
                "session_id": session.session_id,
                "trace_mode": session.trace_mode,
                "permissions": dict(sorted(session.command_permissions.items())),
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
