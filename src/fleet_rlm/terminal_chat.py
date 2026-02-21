"""Standalone terminal chat runtime for the `fleet` entrypoint."""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import dspy
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts.prompt import CompleteStyle
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.table import Table

from . import runners
from .config import AppConfig
from .core.config import get_planner_lm_from_env
from .models import TraceMode
from .react.commands import COMMAND_DISPATCH
from .terminal import (
    _FleetCompleter,
    _badge,
    _bottom_toolbar,
    _history_path,
    _normalize_trace_mode,
    _print_banner,
    _prompt_label,
    handle_slash_command,
    run_long_context,
    run_settings,
    settings_llm,
    settings_modal,
)
from .utils.modal import get_default_volume_name, load_modal_config


@dataclass(slots=True)
class TerminalChatOptions:
    """User-facing options for the standalone terminal chat loop."""

    docs_path: Path | None = None
    trace_mode: TraceMode = "compact"


@dataclass(frozen=True, slots=True)
class SlashCommandSpec:
    """Metadata for command palette rendering and completion."""

    name: str
    summary: str
    category: str


_COMMAND_SPECS: tuple[SlashCommandSpec, ...] = (
    SlashCommandSpec("/", "Show command palette", "core"),
    SlashCommandSpec("/help", "Show command reference", "core"),
    SlashCommandSpec("/status", "Show runtime and config status", "core"),
    SlashCommandSpec("/settings", "Configure local .env values", "core"),
    SlashCommandSpec("/trace", "Set trace mode", "core"),
    SlashCommandSpec("/clear", "Clear terminal", "core"),
    SlashCommandSpec("/reset", "Reset agent state and buffers", "core"),
    SlashCommandSpec("/exit", "Exit chat", "core"),
    SlashCommandSpec("/docs", "Load document (alias: /load)", "documents"),
    SlashCommandSpec("/active", "Set active document alias", "documents"),
    SlashCommandSpec("/list", "List loaded documents", "documents"),
    SlashCommandSpec("/chunk", "Chunk active document", "documents"),
    SlashCommandSpec("/analyze", "Analyze active document", "documents"),
    SlashCommandSpec("/summarize", "Summarize active document", "documents"),
    SlashCommandSpec("/extract", "Extract from logs", "documents"),
    SlashCommandSpec("/semantic", "Parallel semantic map", "documents"),
    SlashCommandSpec("/buffer", "Read sandbox buffer", "buffers"),
    SlashCommandSpec("/clear-buffer", "Clear one/all buffers", "buffers"),
    SlashCommandSpec("/save-buffer", "Persist buffer to volume", "buffers"),
    SlashCommandSpec("/load-volume", "Load volume text as document", "buffers"),
    SlashCommandSpec("/run-long-context", "Runner wrapper", "runners"),
    SlashCommandSpec("/check-secret", "Runner wrapper", "runners"),
    SlashCommandSpec("/check-secret-key", "Runner wrapper", "runners"),
    SlashCommandSpec("/permissions", "Show permission policy state", "security"),
    SlashCommandSpec("/permissions-reset", "Reset permission policy state", "security"),
    SlashCommandSpec("/model", "Shortcut for /settings model", "settings"),
    SlashCommandSpec("/?", "Show keyboard shortcuts", "core"),
)

_COMMAND_TEMPLATES: dict[str, str] = {
    "/docs": "path=README.md alias=active",
    "/chunk": "headers 200000",
    "/analyze": "Extract architecture decisions",
    "/summarize": "key points",
    "/semantic": 'query="find auth flows" chunk_strategy=headers max_chunks=24',
    "/run-long-context": 'docs/architecture.md "What are key decisions?" analyze',
    "/check-secret-key": "DSPY_LLM_API_KEY",
    "/trace": "compact",
}


def run_terminal_chat(*, config: AppConfig, options: TerminalChatOptions) -> None:
    """Start standalone in-process terminal chat (no FastAPI backend required)."""
    session = _TerminalChatSession(config=config, options=options)
    session.run()


def _build_completer() -> _FleetCompleter:
    """Build a fleet completer with command specs."""
    specs = [(spec.name, spec.summary) for spec in _COMMAND_SPECS]
    dispatch_names = list(COMMAND_DISPATCH.keys())
    return _FleetCompleter(command_specs=specs, command_dispatch_names=dispatch_names)


class _TerminalChatSession:
    """Terminal chat session handling user interaction and agent communication."""

    def __init__(self, *, config: AppConfig, options: TerminalChatOptions) -> None:
        self.config = config
        self.options = options
        self.trace_mode: TraceMode = cast(
            TraceMode, _normalize_trace_mode(options.trace_mode)
        )
        self.session_id = uuid.uuid4().hex[:8]
        self.secret_name = (
            config.interpreter.secrets[0] if config.interpreter.secrets else "LITELLM"
        )
        self.volume_name = config.interpreter.volume_name or "rlm-volume-dspy"
        self.console = Console()
        self.last_status = "ready"
        self.is_processing = False
        self.transcript: list[tuple[str, str]] = []
        self.command_permissions: dict[str, str] = {}

        history_path = _history_path()
        history_path.parent.mkdir(parents=True, exist_ok=True)
        self.prompt_session = PromptSession(
            history=FileHistory(str(history_path)),
            completer=_build_completer(),
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
            style=Style.from_dict(
                {
                    "prompt": "ansicyan bold",
                    "trace": "ansibrightblack",
                }
            ),
        )

    def run(self) -> None:
        """Run the interactive prompt loop."""
        planner_lm = get_planner_lm_from_env(model_name=self.config.agent.model)
        self._print_banner(planner_ready=planner_lm is not None)
        if planner_lm is None:
            self.console.print(
                "[yellow]Planner LM not configured.[/] Use [bold]/settings[/] and "
                "[bold]/status[/] before sending chat prompts."
            )

        agent_context = runners.build_react_chat_agent(
            docs_path=self.options.docs_path,
            react_max_iters=self.config.rlm_settings.max_iters,
            rlm_max_iterations=self.config.agent.rlm_max_iterations,
            rlm_max_llm_calls=self.config.rlm_settings.max_llm_calls,
            max_depth=self.config.rlm_settings.max_depth,
            timeout=self.config.interpreter.timeout,
            secret_name=self.secret_name,
            volume_name=self.volume_name,
            planner_lm=planner_lm,
            interpreter_async_execute=self.config.interpreter.async_execute,
            guardrail_mode=self.config.agent.guardrail_mode,
            max_output_chars=self.config.rlm_settings.max_output_chars,
            min_substantive_chars=self.config.agent.min_substantive_chars,
        )

        lm_context = dspy.context(lm=planner_lm) if planner_lm else nullcontext()
        screen_ctx = self.console.screen(hide_cursor=False)
        with screen_ctx, lm_context, agent_context as agent:
            while True:
                self._render_shell()
                try:
                    line = self.prompt_session.prompt(
                        _prompt_label(),
                        complete_style=CompleteStyle.MULTI_COLUMN,
                        bottom_toolbar=self._bottom_toolbar(),
                    ).strip()
                except EOFError:
                    self.console.print("[dim]bye[/dim]")
                    return
                except KeyboardInterrupt:
                    self.console.print("\n[dim]Use /exit to quit.[/dim]")
                    continue

                if not line:
                    continue
                if line.startswith("/"):
                    should_exit = handle_slash_command(self, agent, line)
                    if should_exit:
                        return
                    continue
                if line == "?":
                    self._show_shortcuts()
                    continue
                if planner_lm is None:
                    self._print_error("Planner LM not configured. Run /settings first.")
                    continue

                try:
                    asyncio.run(self._run_chat_turn(agent, line))
                except KeyboardInterrupt:
                    self._print_warning("Turn cancelled by user.")
                except Exception as exc:  # pragma: no cover - runtime path
                    self._print_error(str(exc))

    async def _run_chat_turn(self, agent: Any, message: str) -> None:
        """Run a single chat turn with streaming output."""
        trace_enabled = self.trace_mode != "off"
        assistant_chunks: list[str] = []
        tool_calls: list[str] = []
        final_text = ""
        final_payload: dict[str, Any] = {}
        self._append_transcript("you", message)
        self.is_processing = True
        self.last_status = "thinking..."
        self._render_shell()
        token_since_render = 0

        try:
            async for event in agent.aiter_chat_turn_stream(
                message=message,
                trace=trace_enabled,
            ):
                kind = event.kind
                text = event.text or ""
                stripped = text.strip()

                if kind == "assistant_token":
                    assistant_chunks.append(text)
                    token_since_render += 1
                    if token_since_render >= 24:
                        self._render_shell(draft_assistant="".join(assistant_chunks))
                        token_since_render = 0
                    continue

                if kind == "status" and stripped:
                    self.last_status = stripped
                    if self.trace_mode == "verbose":
                        self._append_transcript("status", stripped)
                        self._render_shell(draft_assistant="".join(assistant_chunks))
                    continue

                if kind == "tool_call" and stripped:
                    tool_calls.append(stripped)
                    self.last_status = stripped
                    if self.trace_mode != "off":
                        self._append_transcript("tool", f"-> {stripped}")
                        self._render_shell(draft_assistant="".join(assistant_chunks))
                    continue

                if kind == "tool_result" and stripped and self.trace_mode == "verbose":
                    self._append_transcript("tool", f"* {stripped}")
                    self._render_shell(draft_assistant="".join(assistant_chunks))
                    continue

                if (
                    kind == "reasoning_step"
                    and stripped
                    and self.trace_mode == "verbose"
                ):
                    self._append_transcript("thinking", stripped)
                    self._render_shell(draft_assistant="".join(assistant_chunks))
                    continue

                if kind == "final":
                    final_text = text.strip()
                    payload = event.payload if isinstance(event.payload, dict) else {}
                    final_payload = dict(payload)
                    break

                if kind == "cancelled":
                    final_text = text.strip()
                    payload = event.payload if isinstance(event.payload, dict) else {}
                    final_payload = dict(payload)
                    self._print_warning("Turn cancelled.")
                    break

                if kind == "error":
                    raise RuntimeError(stripped or "streaming error")
        finally:
            self.is_processing = False

        assistant_response = final_text or "".join(assistant_chunks).strip()
        if not assistant_response:
            assistant_response = "[no response]"
        if self.trace_mode == "compact" and tool_calls:
            self._append_transcript(
                "status",
                f"{len(tool_calls)} tool actions - use /trace verbose for details",
            )
        self._append_transcript("assistant", assistant_response)
        self.last_status = "ready"
        self._render_shell()

        warnings = list(final_payload.get("guardrail_warnings", []) or [])
        for warning in warnings:
            self._print_warning(str(warning))

    def _handle_slash(self, agent: Any, line: str) -> bool:
        """Handle slash commands (delegates to terminal.commands module)."""
        return handle_slash_command(self, agent, line)

    def _run_settings(self, section: str) -> None:
        """Run settings configuration (delegates to terminal.settings module)."""
        run_settings(self, section)

    def _settings_llm(self, *, model_only: bool) -> None:
        """Configure LLM settings (delegates to terminal.settings module)."""
        settings_llm(self, model_only=model_only)

    def _settings_modal(self) -> None:
        """Configure Modal credentials (delegates to terminal.settings module)."""
        settings_modal(self)

    def _run_long_context(self, arg_text: str) -> None:
        """Run long-context task (delegates to terminal.settings module)."""
        run_long_context(self, arg_text)

    def _check_secret(self) -> None:
        """Check Modal secret (delegates to terminal.settings module)."""
        from .terminal import check_secret

        check_secret(self)

    def _check_secret_key(self, *, key: str) -> None:
        """Check Modal secret key (delegates to terminal.settings module)."""
        from .terminal import check_secret_key

        check_secret_key(self, key=key)

    def _print_status(self, agent: Any) -> None:
        """Print the current session and agent status."""
        import os

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
            secret_check = runners.check_secret_presence(secret_name=self.secret_name)
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
            f"Modal secret ({self.secret_name})",
            _badge(secret_ok),
            ", ".join(
                f"{k}={'yes' if bool(v) else 'no'}" for k, v in secret_check.items()
            ),
        )
        table.add_row(
            "Volume",
            "[green]configured[/]",
            f"configured={self.volume_name}, default={get_default_volume_name()}",
        )
        table.add_row(
            "Documents",
            "[green]ok[/]",
            f"loaded={docs_loaded}, active={active_alias or 'none'}",
        )
        allowed = sorted(
            command
            for command, policy in self.command_permissions.items()
            if policy == "allow"
        )
        denied = sorted(
            command
            for command, policy in self.command_permissions.items()
            if policy == "deny"
        )
        table.add_row(
            "Permissions",
            "[green]ok[/]",
            f"allow_session={len(allowed)}, denied={len(denied)}",
        )

        self.console.print(table)
        if self.trace_mode == "verbose":
            self._print_result(
                {
                    "secret_check": secret_check,
                    "session_id": self.session_id,
                    "trace_mode": self.trace_mode,
                    "permissions": dict(sorted(self.command_permissions.items())),
                },
                title="status payload",
            )

    def _print_command_palette(self, agent: Any) -> bool:
        """Print command palette (delegates to terminal.commands module)."""
        from .terminal import print_command_palette

        return print_command_palette(self, agent)

    def _print_unknown_command(self, command: str) -> None:
        """Print unknown command error (delegates to terminal.commands module)."""
        from .terminal import _print_unknown_command

        _print_unknown_command(self, command)

    def _print_result(self, result: dict[str, Any], *, title: str) -> None:
        """Print a result dictionary as JSON."""
        rendered = json.dumps(result, indent=2, sort_keys=True, default=str)
        self._append_transcript("result", f"{title}\n{rendered}")
        self.last_status = f"{title} complete"
        self._render_shell()

    def _print_banner(self, *, planner_ready: bool) -> None:
        """Print the startup banner."""
        _print_banner(
            console=self.console,
            session_id=self.session_id,
            model=self.config.agent.model,
            planner_ready=planner_ready,
            workspace=Path.cwd(),
        )

    def _bottom_toolbar(self) -> HTML:
        """Return the bottom toolbar HTML."""
        return _bottom_toolbar(is_processing=self.is_processing)

    def _print_warning(self, message: str) -> None:
        """Print a warning message."""
        self._append_transcript("warning", message)
        self.last_status = "warning"
        self._render_shell()

    def _print_error(self, message: str) -> None:
        """Print an error message."""
        self._append_transcript("error", message)
        self.last_status = "error"
        self._render_shell()

    def _print_permissions(self) -> None:
        """Print the current permission policies."""
        table = Table(title="command permissions")
        table.add_column("Command", style="cyan")
        table.add_column("Policy", style="bold")
        if not self.command_permissions:
            table.add_row("*", "ask (default)")
        else:
            for command, policy in sorted(self.command_permissions.items()):
                table.add_row(command, policy)
        self.console.print(table)

    def _authorize_command(self, *, command: str) -> bool:
        """Authorize a command based on session policy."""
        from .terminal import _prompt_choice

        policy = self.command_permissions.get(command, "ask")
        if policy == "deny":
            self._print_error(f"Command denied by session policy: {command}")
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
            self.command_permissions[command] = "allow"
            return True
        if choice == "deny":
            self.command_permissions[command] = "deny"
            self._print_warning(f"Denied command: {command}")
            return False
        return False

    def _show_shortcuts(self) -> None:
        """Show keyboard shortcuts."""
        self._append_transcript(
            "status",
            (
                "Shortcuts: / opens command palette - @ mentions files - "
                "Ctrl+C interrupts - /trace compact|verbose|off"
            ),
        )
        self._render_shell()

    def _append_transcript(self, role: str, content: str) -> None:
        """Append a message to the transcript."""
        text = content.strip()
        if not text:
            return
        self.transcript.append((role, text))
        if len(self.transcript) > 200:
            self.transcript = self.transcript[-200:]

    def _render_shell(self, *, draft_assistant: str = "") -> None:
        """Render the shell UI layout."""
        from .terminal import _render_shell

        _render_shell(
            console=self.console,
            session_id=self.session_id,
            model=self.config.agent.model,
            trace_mode=self.trace_mode,
            last_status=self.last_status,
            transcript=self.transcript,
            is_processing=self.is_processing,
            draft_assistant=draft_assistant,
        )
