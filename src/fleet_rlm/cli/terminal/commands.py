"""Command handling for the terminal chat interface.

This module provides slash command processing extracted from terminal_chat.py.
All functions are stateless and take required parameters explicitly.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from typing import Any

from prompt_toolkit.shortcuts import input_dialog, radiolist_dialog, yes_no_dialog

from .ui import _dialog_style, _prompt_choice


# Valid trace modes
_TRACE_MODES: set[str] = {"compact", "verbose", "off"}


def handle_slash_command(
    session: Any,
    agent: Any,
    line: str,
    *,
    trace_modes: set[str] | None = None,
) -> bool:
    """Handle a slash command from the terminal.

    Args:
        session: The terminal chat session instance.
        agent: The agent instance.
        line: The command line input.
        trace_modes: Valid trace modes (defaults to {"compact", "verbose", "off"}).

    Returns:
        True if the session should exit, False otherwise.
    """
    trace_modes = trace_modes or _TRACE_MODES
    command, arg_text = _split_slash_command(line)

    if command in {"/", "/help", "/commands"}:
        return print_command_palette(session, agent)

    if command == "/?":
        _show_shortcuts(session)
        return False

    if command in {"/exit", "/quit"}:
        session.console.print("[dim]bye[/dim]")
        return True

    if command == "/clear":
        session.console.clear()
        session._print_banner(planner_ready=True)
        return False

    if command == "/reset":
        return handle_reset_command(session, agent)

    if command == "/trace":
        return handle_trace_command(session, arg_text, trace_modes)

    if command == "/status":
        session._print_status(agent)
        return False

    if command == "/settings":
        session._run_settings(arg_text.strip().lower())
        return False

    if command == "/model":
        session._run_settings("model")
        return False

    if command == "/permissions":
        session._print_permissions()
        return False

    if command == "/permissions-reset":
        session.command_permissions.clear()
        session._print_warning("Permission policy reset.")
        return False

    if command == "/run-long-context":
        session._run_long_context(arg_text)
        return False

    if command == "/check-secret":
        session._check_secret()
        return False

    if command == "/check-secret-key":
        key = arg_text.strip() or "DSPY_LLM_API_KEY"
        session._check_secret_key(key=key)
        return False

    alias_result = handle_alias_command(session, agent, command, arg_text)
    if alias_result is not None:
        return alias_result

    # Try dispatch to agent commands
    from ...runtime.agent.commands import COMMAND_DISPATCH

    canonical = command.lstrip("/")
    if canonical in COMMAND_DISPATCH:
        try:
            payload = _parse_command_payload(arg_text)
        except Exception as exc:
            session._print_error(str(exc))
            return False
        _execute_agent_command(session, agent, canonical, payload)
        return False

    _print_unknown_command(session, command)
    return False


def handle_trace_command(
    session: Any,
    arg_text: str,
    trace_modes: set[str] | None = None,
) -> bool:
    """Handle the /trace command.

    Args:
        session: The terminal chat session instance.
        arg_text: The argument text.
        trace_modes: Valid trace modes.

    Returns:
        False (never exits).
    """
    trace_modes = trace_modes or _TRACE_MODES
    mode = arg_text.strip().lower()
    if mode not in trace_modes:
        session._print_error("usage: /trace <compact|verbose|off>")
        return False
    session.trace_mode = _normalize_trace_mode(mode)
    session.console.print(f"[green]Trace mode set to {session.trace_mode}[/]")
    return False


def handle_reset_command(session: Any, agent: Any) -> bool:
    """Handle the /reset command.

    Args:
        session: The terminal chat session instance.
        agent: The agent instance.

    Returns:
        False (never exits).
    """
    if not _confirm("Reset agent history and clear sandbox buffers?"):
        session._print_warning("Reset cancelled.")
        return False
    result = agent.reset(clear_sandbox_buffers=True)
    session._print_result(result, title="reset")
    return False


def handle_alias_command(
    session: Any,
    agent: Any,
    command: str,
    arg_text: str,
) -> bool | None:
    """Handle alias commands (/docs, /active, /list, etc.).

    Args:
        session: The terminal chat session instance.
        agent: The agent instance.
        command: The command name.
        arg_text: The argument text.

    Returns:
        False if handled, None if not an alias command.
    """
    args = _safe_split(arg_text)

    if command in {"/docs", "/load"}:
        if not args:
            session._print_error("usage: /docs <path> [alias]")
            return False
        payload: dict[str, Any] = {"path": args[0]}
        if len(args) > 1:
            payload["alias"] = args[1]
        _execute_agent_command(session, agent, "load_document", payload)
        return False

    if command == "/active":
        if not args:
            session._print_error("usage: /active <alias>")
            return False
        _execute_agent_command(
            session, agent, "set_active_document", {"alias": args[0]}
        )
        return False

    if command == "/list":
        _execute_agent_command(session, agent, "list_documents", {})
        return False

    if command == "/chunk":
        if not args:
            session._print_error(
                "usage: /chunk <size|headers|timestamps|json> [chunk_size]"
            )
            return False
        payload: dict[str, Any] = {"strategy": args[0]}
        if len(args) > 1 and args[1].isdigit():
            payload["size"] = int(args[1])
        _execute_agent_command(session, agent, "chunk_host", payload)
        return False

    if command == "/analyze":
        query = arg_text.strip()
        if not query:
            session._print_error("usage: /analyze <query>")
            return False
        _execute_agent_command(session, agent, "analyze_document", {"query": query})
        return False

    if command == "/summarize":
        focus = arg_text.strip()
        if not focus:
            session._print_error("usage: /summarize <focus>")
            return False
        _execute_agent_command(session, agent, "summarize_document", {"focus": focus})
        return False

    if command == "/extract":
        query = arg_text.strip()
        if not query:
            session._print_error("usage: /extract <query>")
            return False
        _execute_agent_command(session, agent, "extract_logs", {"query": query})
        return False

    if command == "/semantic":
        query = arg_text.strip()
        if not query:
            session._print_error("usage: /semantic <query>")
            return False
        _execute_agent_command(
            session, agent, "parallel_semantic_map", {"query": query}
        )
        return False

    if command == "/buffer":
        if not args:
            session._print_error("usage: /buffer <name>")
            return False
        _execute_agent_command(session, agent, "read_buffer", {"name": args[0]})
        return False

    if command == "/clear-buffer":
        payload: dict[str, Any] = {"name": args[0]} if args else {}
        if not args and not _confirm("Clear all buffers?"):
            session._print_warning("clear-buffer cancelled.")
            return False
        _execute_agent_command(session, agent, "clear_buffer", payload)
        return False

    if command == "/save-buffer":
        if len(args) < 2:
            session._print_error("usage: /save-buffer <buffer_name> <volume_path>")
            return False
        _execute_agent_command(
            session,
            agent,
            "save_buffer",
            {"name": args[0], "path": args[1]},
        )
        return False

    if command == "/load-volume":
        if not args:
            session._print_error("usage: /load-volume <path> [alias]")
            return False
        payload: dict[str, Any] = {"path": args[0]}
        if len(args) > 1:
            payload["alias"] = args[1]
        _execute_agent_command(session, agent, "load_volume", payload)
        return False

    return None


def print_command_palette(session: Any, agent: Any) -> bool:
    """Print and handle the command palette dialog.

    Args:
        session: The terminal chat session instance.
        agent: The agent instance.

    Returns:
        False (never exits).
    """
    from ..terminal.chat import _COMMAND_SPECS, _COMMAND_TEMPLATES

    query = input_dialog(
        title="Command palette",
        text="Filter commands (optional):",
        style=_dialog_style(),
        default="",
    ).run()
    if query is None:
        return False

    query_norm = query.strip().lower()
    specs = [
        spec
        for spec in sorted(_COMMAND_SPECS, key=lambda item: (item.category, item.name))
        if (
            not query_norm
            or query_norm in spec.name.lower()
            or query_norm in spec.summary.lower()
            or query_norm in spec.category.lower()
        )
    ]
    if not specs:
        session._print_warning("No commands match that filter.")
        return False

    values = [
        (spec.name, f"{spec.name:<20} {spec.summary}  [{spec.category}]")
        for spec in specs
    ]
    selected = radiolist_dialog(
        title="Slash command palette",
        text="Select a command (up/down, Enter):",
        values=values,
        style=_dialog_style(),
    ).run()
    if not selected:
        return False

    template = _COMMAND_TEMPLATES.get(selected, "")
    quick = input_dialog(
        title=f"{selected} arguments",
        text=f"Arguments (template: {template or 'none'}):",
        style=_dialog_style(),
        default=template,
    ).run()
    if quick is None:
        return False
    quick_line = selected if not quick.strip() else f"{selected} {quick.strip()}"
    return handle_slash_command(session, agent, quick_line)


def _execute_agent_command(
    session: Any,
    agent: Any,
    command: str,
    args: dict[str, Any],
) -> None:
    """Execute an agent command with authorization.

    Args:
        session: The terminal chat session instance.
        agent: The agent instance.
        command: The command name.
        args: The command arguments.
    """
    if not _authorize_command(session, command=command):
        return
    confirm_message = _confirmation_message(command=command, args=args)
    if confirm_message and not _confirm(confirm_message):
        session._print_warning("Command cancelled.")
        return

    try:
        result = asyncio.run(agent.execute_command(command, args))
        session._print_result(result, title=command)
    except Exception as exc:  # pragma: no cover - runtime path
        session._print_error(str(exc))


def _authorize_command(session: Any, *, command: str) -> bool:
    """Authorize a command based on session policy.

    Args:
        session: The terminal chat session instance.
        command: The command name.

    Returns:
        True if authorized, False if denied.
    """
    policy = session.command_permissions.get(command, "ask")
    if policy == "deny":
        session._print_error(f"Command denied by session policy: {command}")
        return False
    if policy == "allow":
        return True

    choice = _prompt_choice(
        f"Allow command `{command}`?",
        ["allow once", "allow for session", "deny"],
        allow_freeform=False,
    )
    if choice == "allow once":
        return True
    if choice == "allow for session":
        session.command_permissions[command] = "allow"
        return True
    if choice == "deny":
        session.command_permissions[command] = "deny"
        session._print_warning(f"Denied command: {command}")
        return False
    return False


def _print_unknown_command(session: Any, command: str) -> None:
    """Print an unknown command error with suggestions.

    Args:
        session: The terminal chat session instance.
        command: The unknown command.
    """
    from ...runtime.agent.commands import COMMAND_DISPATCH
    from ..terminal.chat import _COMMAND_SPECS

    options = sorted({spec.name for spec in _COMMAND_SPECS})
    options.extend(f"/{name}" for name in sorted(COMMAND_DISPATCH))
    suggestions = [opt for opt in options if opt.startswith(command)][:6]
    if suggestions:
        session._print_error(
            f"Unknown command: {command}. Did you mean: {', '.join(suggestions)}"
        )
        return
    session._print_error(f"Unknown command: {command}. Type /help for commands.")


def _show_shortcuts(session: Any) -> None:
    """Show keyboard shortcuts.

    Args:
        session: The terminal chat session instance.
    """
    session._append_transcript(
        "status",
        (
            "Shortcuts: / opens command palette - @ mentions files - "
            "Ctrl+C interrupts - /trace compact|verbose|off"
        ),
    )
    session._render_shell()


def _confirmation_message(*, command: str, args: dict[str, Any]) -> str | None:
    """Generate a confirmation message for a command.

    Args:
        command: The command name.
        args: The command arguments.

    Returns:
        Confirmation message or None if no confirmation needed.
    """
    if command == "write_to_file" and not bool(args.get("append")):
        path = str(args.get("path", "<unknown>"))
        return f"Overwrite file at {path}?"
    if command == "clear_buffer" and not args.get("name"):
        return "Clear all sandbox buffers?"
    return None


def _confirm(question: str) -> bool:
    """Show a confirmation dialog.

    Args:
        question: The question to ask.

    Returns:
        True if confirmed, False otherwise.
    """
    try:
        answer = yes_no_dialog(
            title="Confirmation",
            text=question,
            style=_dialog_style(),
        ).run()
        return bool(answer)
    except Exception:
        answer = _prompt_choice(question, ["yes", "no"], allow_freeform=False)
        return answer == "yes"


def _normalize_trace_mode(value: str) -> str:
    """Normalize a trace mode value.

    Args:
        value: The trace mode value.

    Returns:
        Normalized trace mode (defaults to "compact" if invalid).
    """
    return value if value in _TRACE_MODES else "compact"


def _split_slash_command(line: str) -> tuple[str, str]:
    """Split a slash command into command and arguments.

    Args:
        line: The command line input.

    Returns:
        Tuple of (command, arg_text).
    """
    stripped = line.strip()
    if not stripped:
        return "/", ""
    parts = stripped.split(maxsplit=1)
    command = parts[0].lower()
    arg_text = parts[1] if len(parts) > 1 else ""
    return command, arg_text


def _safe_split(arg_text: str) -> list[str]:
    """Safely split an argument string.

    Args:
        arg_text: The argument text.

    Returns:
        List of split arguments.
    """
    try:
        return shlex.split(arg_text)
    except ValueError:
        return arg_text.split()


def _parse_command_payload(arg_text: str) -> dict[str, Any]:
    """Parse a command payload from argument text.

    Args:
        arg_text: The argument text.

    Returns:
        Parsed payload dictionary.

    Raises:
        ValueError: If the payload format is invalid.
    """
    text = arg_text.strip()
    if not text:
        return {}

    if text.startswith("{"):
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be an object.")
        return payload

    payload: dict[str, Any] = {}
    for token in _safe_split(text):
        if "=" not in token:
            raise ValueError(
                "Use key=value pairs or JSON object payload for canonical commands."
            )
        key, value = token.split("=", 1)
        payload[key] = _coerce_value(value)
    return payload


def _coerce_value(value: str) -> Any:
    """Coerce a string value to appropriate type.

    Args:
        value: The string value.

    Returns:
        Coerced value (bool, int, float, None, or original string).
    """
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if lowered.isdigit():
        return int(lowered)
    try:
        return float(value)
    except ValueError:
        return value
