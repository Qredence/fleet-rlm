"""Settings and runner functions for the terminal chat interface.

This module provides settings configuration and runner functions
extracted from terminal_chat.py. All functions are stateless and
take required parameters explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import set_key
from rich.panel import Panel

from fleet_rlm.cli import runners
from fleet_rlm.integrations.config.runtime_settings import resolve_env_path

from .ui import _prompt_choice, _prompt_value

# Settings keys that require non-empty values
_SETTINGS_KEYS = (
    "DSPY_LM_MODEL",
    "DSPY_LLM_API_KEY",
    "DSPY_LM_API_BASE",
    "DSPY_LM_MAX_TOKENS",
)


def run_settings(session: Any, section: str) -> None:
    """Run the settings configuration dialog."""
    section_norm = section.strip().lower()
    if not section_norm:
        section_norm = (
            _prompt_choice(
                "Settings section:",
                ["llm", "model"],
                allow_freeform=True,
            )
            .strip()
            .lower()
        )

    if section_norm in {"llm", "model"}:
        settings_llm(session, model_only=section_norm == "model")
        return

    session._print_error("unknown settings section. try: /settings llm|model")


def settings_llm(session: Any, *, model_only: bool) -> None:
    """Configure LLM settings via inline form with sequential fallback.

    Attempts to display a prompt_toolkit Application-based form where all
    fields are visible at once.  If that fails (e.g. non-interactive
    environment), falls back to the sequential prompt flow.

    Args:
        session: The terminal chat session instance.
        model_only: If True, only configure the model name.
    """
    env_path = resolve_env_path()
    current_values = {
        "DSPY_LM_MODEL": os.environ.get("DSPY_LM_MODEL", ""),
        "DSPY_LLM_API_KEY": os.environ.get("DSPY_LLM_API_KEY", ""),
        "DSPY_LM_API_BASE": os.environ.get("DSPY_LM_API_BASE", ""),
        "DSPY_LM_MAX_TOKENS": os.environ.get("DSPY_LM_MAX_TOKENS", ""),
    }

    try:
        updates = _build_settings_form(
            current_values=current_values,
            model_only=model_only,
        )
    except Exception:
        updates = _sequential_settings_llm(
            session,
            current_values=current_values,
            model_only=model_only,
        )

    if not updates:
        session._print_warning("No changes made.")
        return

    from .commands import _confirm

    if not _confirm(f"Write {len(updates)} update(s) to {env_path}?"):
        session._print_warning("Settings update cancelled.")
        return

    _write_env_updates(env_path=env_path, updates=updates)
    session.console.print(f"[green]Updated[/] {', '.join(sorted(updates))} in [bold]{env_path}[/]")


def _build_settings_form(
    *,
    current_values: dict[str, str],
    model_only: bool,
) -> dict[str, str] | None:
    """Display an inline settings form using a prompt_toolkit Application.

    All editable fields are shown at once.  The user can Tab between them,
    press Ctrl+S to save, or Escape to cancel.

    Args:
        current_values: Current environment values to pre-fill.
        model_only: If True, only show the model field.

    Returns:
        Dictionary of updated (non-empty) values, or ``None`` if cancelled.
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.layout import Layout as PTLayout
    from prompt_toolkit.widgets import Frame, Label, TextArea

    fields: dict[str, TextArea] = {}

    fields["DSPY_LM_MODEL"] = TextArea(
        text=current_values.get("DSPY_LM_MODEL", ""),
        height=1,
        multiline=False,
    )

    if not model_only:
        fields["DSPY_LLM_API_KEY"] = TextArea(
            text="",  # Never pre-fill secrets
            height=1,
            multiline=False,
        )
        fields["DSPY_LM_API_BASE"] = TextArea(
            text=current_values.get("DSPY_LM_API_BASE", ""),
            height=1,
            multiline=False,
        )
        fields["DSPY_LM_MAX_TOKENS"] = TextArea(
            text=current_values.get("DSPY_LM_MAX_TOKENS", ""),
            height=1,
            multiline=False,
        )

    kb = KeyBindings()

    @kb.add("escape")
    def _cancel(event: Any) -> None:
        event.app.exit(result=None)

    @kb.add("c-s")
    def _save(event: Any) -> None:
        event.app.exit(result="save")

    rows: list[HSplit | Window | Label] = []
    for key, field in fields.items():
        label_text = "API Key (hidden): " if "API_KEY" in key else f"{key}: "
        rows.append(HSplit([Label(f" {label_text}"), field]))
    rows.append(Window(height=1))
    rows.append(Label(" Tab=next field  Ctrl+S=save  Esc=cancel"))

    body = Frame(HSplit(rows), title="LLM Settings")

    app: Application[str | None] = Application(
        layout=PTLayout(body),
        key_bindings=kb,
        full_screen=False,
    )

    outcome = app.run()
    if outcome == "save":
        return {key: field.text.strip() for key, field in fields.items() if field.text.strip()}
    return None


def _sequential_settings_llm(
    session: Any,
    *,
    current_values: dict[str, str],
    model_only: bool,
) -> dict[str, str] | None:
    """Fallback: prompt for each value sequentially.

    Used when the prompt_toolkit Application form cannot run (e.g. in a
    non-interactive or headless environment).

    Args:
        session: The terminal chat session instance.
        current_values: Current environment values to show as defaults.
        model_only: If True, only prompt for the model name.

    Returns:
        Dictionary of updated (non-empty) values, or ``None`` if no changes.
    """
    updates: dict[str, str] = {}

    session.console.print(Panel("Update LLM configuration in local .env", title="settings"))

    model_value = _prompt_value(
        key="DSPY_LM_MODEL",
        default=current_values.get("DSPY_LM_MODEL", ""),
        secret=False,
    )
    if model_value:
        updates["DSPY_LM_MODEL"] = model_value

    if not model_only:
        api_key = _prompt_value(
            key="DSPY_LLM_API_KEY",
            default="",
            secret=True,
        )
        if api_key:
            updates["DSPY_LLM_API_KEY"] = api_key

        api_base = _prompt_value(
            key="DSPY_LM_API_BASE",
            default=current_values.get("DSPY_LM_API_BASE", ""),
            secret=False,
        )
        if api_base:
            updates["DSPY_LM_API_BASE"] = api_base

        max_tokens = _prompt_value(
            key="DSPY_LM_MAX_TOKENS",
            default=current_values.get("DSPY_LM_MAX_TOKENS", ""),
            secret=False,
        )
        if max_tokens:
            updates["DSPY_LM_MAX_TOKENS"] = max_tokens

    return updates or None


def run_long_context(session: Any, arg_text: str) -> None:
    """Run a long-context processing task.

    Args:
        session: The terminal chat session instance.
        arg_text: The argument text (docs_path query [mode]).
    """
    from .commands import _authorize_command, _safe_split

    if not _authorize_command(session, command="run-long-context"):
        return

    args = _safe_split(arg_text)
    if not args:
        docs_path = _prompt_value(key="docs_path", default="", secret=False)
        query = _prompt_value(key="query", default="", secret=False)
        mode = _prompt_choice(
            "Mode:",
            ["summarize"],
            allow_freeform=False,
        )
    else:
        docs_path = args[0]
        mode = "summarize"
        query_parts = args[1:]
        if query_parts and query_parts[-1] in {"analyze", "summarize"}:
            mode = query_parts[-1]
            query_parts = query_parts[:-1]
        query = " ".join(query_parts)

    if not docs_path or not query:
        session._print_error("usage: /run-long-context <docs_path> <query> [summarize]")
        return

    with session.console.status("[cyan]Running long-context task...[/]", spinner="line"):
        try:
            result = runners.run_long_context(
                docs_path=docs_path,
                query=query,
                mode=mode,
                max_iterations=session.config.rlm_settings.max_iterations,
                max_llm_calls=session.config.rlm_settings.max_llm_calls,
                verbose=session.config.rlm_settings.verbose,
                timeout=session.config.interpreter.timeout,
                secret_name=session.secret_name,
                volume_name=session.volume_name,
            )
        except Exception as exc:  # pragma: no cover - runtime path
            session._print_error(str(exc))
            return

    session._print_result(result, title="run-long-context")


def _write_env_updates(*, env_path: Path, updates: dict[str, str]) -> None:
    """Write updates to the .env file.

    Args:
        env_path: Path to the .env file.
        updates: Dictionary of key-value pairs to write.
    """
    env_path.touch(exist_ok=True)
    for key, value in updates.items():
        if key in _SETTINGS_KEYS and not value:
            continue
        set_key(str(env_path), key, value)
        os.environ[key] = value
