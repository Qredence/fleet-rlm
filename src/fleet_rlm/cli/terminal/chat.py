"""Standalone terminal chat runtime for the `fleet` entrypoint."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts.prompt import CompleteStyle
from prompt_toolkit.styles import Style
from rich.console import Console

from fleet_rlm.runtime.agent.commands import COMMAND_DISPATCH
from fleet_rlm.runtime.config import (
    build_dspy_context,
    get_delegate_lm_from_env,
    get_planner_lm_from_env,
)
from fleet_rlm.runtime.factory import build_chat_agent
from fleet_rlm.runtime.models import TraceMode
from fleet_rlm.integrations.config.env import AppConfig

from .commands import _normalize_trace_mode, handle_slash_command
from .session_actions import (
    authorize_command as _authorize_command_impl,
    print_command_palette_action as _print_command_palette_impl,
    print_permissions as _print_permissions_impl,
    print_status as _print_status_impl,
    print_unknown_command_action as _print_unknown_command_impl,
    run_long_context_action as _run_long_context_impl,
    run_settings_action as _run_settings_impl,
    settings_llm_action as _settings_llm_impl,
    show_shortcuts as _show_shortcuts_impl,
)
from .session_view import (
    append_transcript as _append_transcript_impl,
    bottom_toolbar as _bottom_toolbar_impl,
    print_banner as _print_banner_impl,
    print_error as _print_error_impl,
    print_result as _print_result_impl,
    print_warning as _print_warning_impl,
    render_shell as _render_shell_impl,
)
from .ui import _FleetCompleter, _history_path, _prompt_label


@dataclass(slots=True)
class TerminalChatOptions:
    """User-facing options for the standalone terminal chat loop."""

    docs_path: Path | None = None
    trace_mode: TraceMode = "compact"
    volume_name: str | None = None


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
    SlashCommandSpec("/summarize", "Summarize active document", "documents"),
    SlashCommandSpec("/extract", "Extract from logs", "documents"),
    SlashCommandSpec("/semantic", "Parallel semantic map", "documents"),
    SlashCommandSpec("/buffer", "Read sandbox buffer", "buffers"),
    SlashCommandSpec("/clear-buffer", "Clear one/all buffers", "buffers"),
    SlashCommandSpec("/save-buffer", "Persist buffer to volume", "buffers"),
    SlashCommandSpec("/load-volume", "Load volume text as document", "buffers"),
    SlashCommandSpec("/run-long-context", "Runner wrapper", "runners"),
    SlashCommandSpec("/permissions", "Show permission policy state", "security"),
    SlashCommandSpec("/permissions-reset", "Reset permission policy state", "security"),
    SlashCommandSpec("/model", "Shortcut for /settings model", "settings"),
    SlashCommandSpec("/?", "Show keyboard shortcuts", "core"),
)

_COMMAND_TEMPLATES: dict[str, str] = {
    "/docs": "path=README.md alias=active",
    "/chunk": "headers 200000",
    "/summarize": "key points",
    "/semantic": 'query="find auth flows" chunk_strategy=headers max_chunks=24',
    "/run-long-context": 'docs/architecture.md "What are key decisions?" summarize',
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
        self.volume_name = (
            options.volume_name or config.interpreter.volume_name or "rlm-volume-dspy"
        )
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
        delegate_lm = get_delegate_lm_from_env(
            model_name=self.config.agent.delegate_model,
            default_max_tokens=self.config.agent.delegate_max_tokens,
        )
        self._print_banner(planner_ready=planner_lm is not None)
        if planner_lm is None:
            self.console.print(
                "[yellow]Planner LM not configured.[/] Use [bold]/settings[/] and "
                "[bold]/status[/] before sending chat prompts."
            )

        agent_context = build_chat_agent(
            docs_path=self.options.docs_path,
            react_max_iters=self.config.rlm_settings.max_iters,
            deep_react_max_iters=self.config.rlm_settings.deep_max_iters,
            enable_adaptive_iters=self.config.rlm_settings.enable_adaptive_iters,
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
            delegate_lm=delegate_lm,
            delegate_max_calls_per_turn=self.config.rlm_settings.delegate_max_calls_per_turn,
            delegate_result_truncation_chars=self.config.rlm_settings.delegate_result_truncation_chars,
        )

        lm_context = build_dspy_context(lm=planner_lm) if planner_lm else nullcontext()
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
        """Run settings configuration."""
        _run_settings_impl(self, section)

    def _settings_llm(self, *, model_only: bool) -> None:
        """Configure LLM settings."""
        _settings_llm_impl(self, model_only=model_only)

    def _run_long_context(self, arg_text: str) -> None:
        """Run long-context task."""
        _run_long_context_impl(self, arg_text)

    def _print_status(self, agent: Any) -> None:
        """Print the current session and agent status."""
        _print_status_impl(self, agent)

    def _print_command_palette(self, agent: Any) -> bool:
        """Print the command palette."""
        return _print_command_palette_impl(self, agent)

    def _print_unknown_command(self, command: str) -> None:
        """Print unknown command error."""
        _print_unknown_command_impl(self, command)

    def _print_result(self, result: dict[str, Any], *, title: str) -> None:
        """Print a result dictionary as JSON."""
        _print_result_impl(self, result, title=title)

    def _print_banner(self, *, planner_ready: bool) -> None:
        """Print the startup banner."""
        _print_banner_impl(self, planner_ready=planner_ready)

    def _bottom_toolbar(self):
        """Return the bottom toolbar HTML."""
        return _bottom_toolbar_impl(self)

    def _print_warning(self, message: str) -> None:
        """Print a warning message."""
        _print_warning_impl(self, message)

    def _print_error(self, message: str) -> None:
        """Print an error message."""
        _print_error_impl(self, message)

    def _print_permissions(self) -> None:
        """Print the current permission policies."""
        _print_permissions_impl(self)

    def _authorize_command(self, *, command: str) -> bool:
        """Authorize a command based on session policy."""
        return _authorize_command_impl(self, command=command)

    def _show_shortcuts(self) -> None:
        """Show keyboard shortcuts."""
        _show_shortcuts_impl(self)

    def _append_transcript(self, role: str, content: str) -> None:
        """Append a message to the transcript."""
        _append_transcript_impl(self, role, content)

    def _render_shell(self, *, draft_assistant: str = "") -> None:
        """Render the shell UI layout."""
        _render_shell_impl(self, draft_assistant=draft_assistant)
