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
    """Configure LLM settings.

    Args:
        session: The terminal chat session instance.
        model_only: If True, only configure the model name.
    """
    env_path = _resolve_env_path()
    updates: dict[str, str] = {}

    session.console.print(
        Panel("Update LLM configuration in local .env", title="settings")
    )

    model_value = _prompt_value(
        key="DSPY_LM_MODEL",
        default=os.environ.get("DSPY_LM_MODEL", ""),
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
            default=os.environ.get("DSPY_LM_API_BASE", ""),
            secret=False,
        )
        if api_base:
            updates["DSPY_LM_API_BASE"] = api_base

        max_tokens = _prompt_value(
            key="DSPY_LM_MAX_TOKENS",
            default=os.environ.get("DSPY_LM_MAX_TOKENS", ""),
            secret=False,
        )
        if max_tokens:
            updates["DSPY_LM_MAX_TOKENS"] = max_tokens

    if not updates:
        session._print_warning("No changes made.")
        return

    from .commands import _confirm

    if not _confirm(f"Write {len(updates)} update(s) to {env_path}?"):
        session._print_warning("Settings update cancelled.")
        return

    _write_env_updates(env_path=env_path, updates=updates)
    session.console.print(
        f"[green]Updated[/] {', '.join(sorted(updates))} in [bold]{env_path}[/]"
    )


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

    with session.console.status(
        "[cyan]Running long-context task...[/]", spinner="line"
    ):
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


def _resolve_env_path() -> Path:
    """Resolve the path to the .env file.

    Searches upward from the current directory for a pyproject.toml
    and places .env alongside it. Falls back to current directory.

    Returns:
        Path to the .env file.
    """
    for parent in [Path.cwd(), *Path.cwd().parents]:
        if (parent / "pyproject.toml").exists():
            return parent / ".env"
    return Path.cwd() / ".env"


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
