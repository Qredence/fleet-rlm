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
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts.prompt import CompleteStyle
from prompt_toolkit.styles import Style
from rich.console import Console

from fleet_rlm.integrations.config.env import AppConfig
from fleet_rlm.runtime.agent.commands import COMMAND_DISPATCH
from fleet_rlm.runtime.config import (
    build_dspy_context,
    get_delegate_lm_from_env,
    get_planner_lm_from_env,
)
from fleet_rlm.runtime.factory import build_chat_agent
from fleet_rlm.runtime.schemas import TraceMode

from .commands import _normalize_trace_mode, handle_slash_command
from .session_actions import (
    authorize_command as _authorize_command_impl,
)
from .session_actions import (
    print_command_palette_action as _print_command_palette_impl,
)
from .session_actions import (
    print_permissions as _print_permissions_impl,
)
from .session_actions import (
    print_status as _print_status_impl,
)
from .session_actions import (
    print_unknown_command_action as _print_unknown_command_impl,
)
from .session_actions import (
    run_long_context_action as _run_long_context_impl,
)
from .session_actions import (
    run_settings_action as _run_settings_impl,
)
from .session_actions import (
    settings_llm_action as _settings_llm_impl,
)
from .session_actions import (
    show_shortcuts as _show_shortcuts_impl,
)
from .session_view import (
    append_transcript as _append_transcript_impl,
)
from .session_view import (
    bottom_toolbar as _bottom_toolbar_impl,
)
from .session_view import (
    print_banner as _print_banner_impl,
)
from .session_view import (
    print_error as _print_error_impl,
)
from .session_view import (
    print_result as _print_result_impl,
)
from .session_view import (
    print_warning as _print_warning_impl,
)
from .session_view import (
    render_shell as _render_shell_impl,
)
from .ui import (
    _FleetCompleter,
    _history_path,
    _prompt_label,
    load_preferences,
    render_onboarding_banner,
    save_preferences,
)


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
    help_text: str = ""


_COMMAND_SPECS: tuple[SlashCommandSpec, ...] = (
    SlashCommandSpec("/", "Show command palette", "core"),
    SlashCommandSpec(
        "/help",
        "Show command reference",
        "core",
        help_text="Usage: /help [command]\n\nShows the command reference.\n  /help        - list all commands grouped by category\n  /help <cmd>  - show detailed usage for a specific command",
    ),
    SlashCommandSpec("/status", "Show runtime and config status", "core"),
    SlashCommandSpec(
        "/settings",
        "Configure local .env values",
        "core",
        help_text="Usage: /settings [llm|model]\n\nOpens interactive configuration.\n  /settings llm - configure all LLM settings\n  /settings model - change model only",
    ),
    SlashCommandSpec(
        "/trace",
        "Set trace mode",
        "core",
        help_text="Usage: /trace <compact|verbose|off>\n\nControls how much agent reasoning is shown.\n  compact: tool call summaries only\n  verbose: full reasoning + tool results\n  off: only final response",
    ),
    SlashCommandSpec("/clear", "Clear terminal", "core"),
    SlashCommandSpec("/reset", "Reset agent state and buffers", "core"),
    SlashCommandSpec("/exit", "Exit chat", "core"),
    SlashCommandSpec(
        "/docs",
        "Load document (alias: /load)",
        "documents",
        help_text="Usage: /docs path=<file> [alias=<name>]\n\nLoads a document into the agent's context.\nExamples:\n  /docs path=README.md alias=readme\n  /docs path=src/main.py",
    ),
    SlashCommandSpec("/active", "Set active document alias", "documents"),
    SlashCommandSpec("/list", "List loaded documents", "documents"),
    SlashCommandSpec(
        "/chunk",
        "Chunk active document",
        "documents",
        help_text="Usage: /chunk <strategy> [size]\n\nChunks the active document.\nStrategies: headers, tokens, semantic\nExamples:\n  /chunk headers 200000\n  /chunk tokens 4096",
    ),
    SlashCommandSpec(
        "/summarize",
        "Summarize active document",
        "documents",
        help_text="Usage: /summarize [focus]\n\nSummarizes the active document, optionally focusing on a topic.\nExamples:\n  /summarize\n  /summarize key architectural decisions",
    ),
    SlashCommandSpec("/extract", "Extract from logs", "documents"),
    SlashCommandSpec(
        "/semantic",
        "Parallel semantic map",
        "documents",
        help_text='Usage: /semantic query=<text> [chunk_strategy=headers] [max_chunks=24]\n\nRuns parallel semantic search across document chunks.\nExamples:\n  /semantic query="find auth flows"\n  /semantic query="error handling" max_chunks=12',
    ),
    SlashCommandSpec("/buffer", "Read sandbox buffer", "buffers"),
    SlashCommandSpec("/clear-buffer", "Clear one/all buffers", "buffers"),
    SlashCommandSpec("/save-buffer", "Persist buffer to volume", "buffers"),
    SlashCommandSpec("/load-volume", "Load volume text as document", "buffers"),
    SlashCommandSpec(
        "/run-long-context",
        "Runner wrapper",
        "runners",
        help_text='Usage: /run-long-context <docs_path> <query> [mode]\n\nRuns long-context RLM processing on a document.\nExamples:\n  /run-long-context docs/arch.md "What are key decisions?" summarize',
    ),
    SlashCommandSpec("/permissions", "Show permission policy state", "security"),
    SlashCommandSpec("/permissions-reset", "Reset permission policy state", "security"),
    SlashCommandSpec("/model", "Shortcut for /settings model", "settings"),
    SlashCommandSpec(
        "/theme",
        "Set color theme (dark/light/auto)",
        "settings",
        help_text="Usage: /theme <dark|light|auto>\n\nSwitches the terminal color theme.\n  dark: bright text on dark background (default)\n  light: dark text on light background\n  auto: detect from COLORFGBG env variable",
    ),
    SlashCommandSpec("/?", "Show keyboard shortcuts", "core"),
)

_COMMAND_TEMPLATES: dict[str, str] = {
    "/docs": "path=README.md alias=active",
    "/chunk": "headers 200000",
    "/summarize": "key points",
    "/semantic": 'query="find auth flows" chunk_strategy=headers max_chunks=24',
    "/run-long-context": 'docs/architecture.md "What are key decisions?" summarize',
    "/trace": "compact",
    "/theme": "dark",
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


def _error_hint(exc: Exception) -> str | None:
    """Return an actionable hint for common exceptions."""
    msg = str(exc).lower()
    if isinstance(exc, ConnectionError) or "connection" in msg:
        return "Check DSPY_LM_API_BASE and network connectivity. Run /settings to reconfigure."
    if "api_key" in msg or "authentication" in msg or "401" in msg:
        return "API key may be invalid or expired. Run /settings llm to update."
    if "timeout" in msg or "timed out" in msg:
        return "Request timed out. Try again or check service availability."
    if isinstance(exc, ValueError) and ("argument" in msg or "expected" in msg):
        return "Check command syntax. Use /help <command> for usage examples."
    if "not configured" in msg:
        return "Run /settings to configure the required component."
    return None


class _TerminalChatSession:
    """Terminal chat session handling user interaction and agent communication."""

    def __init__(self, *, config: AppConfig, options: TerminalChatOptions) -> None:
        self.config = config
        self.options = options
        self.trace_mode: TraceMode = cast(TraceMode, _normalize_trace_mode(options.trace_mode))
        self.session_id = uuid.uuid4().hex[:8]
        self.secret_name = config.interpreter.secrets[0] if config.interpreter.secrets else "LITELLM"
        self.volume_name = options.volume_name or config.interpreter.volume_name or "rlm-volume-dspy"
        self.console = Console()
        self.last_status = "ready"
        self.is_processing = False
        self.transcript: list[tuple[str, str]] = []
        self.command_permissions: dict[str, str] = {}
        self._live = None  # Set during streaming when Rich Live is active
        self._scroll_offset = 0  # Transcript scroll position (0 = tail)

        # Hydrate from persistent preferences
        self._preferences = load_preferences()
        if self._preferences.trace_mode in ("compact", "verbose", "off"):
            self.trace_mode = cast(TraceMode, self._preferences.trace_mode)
        self.command_permissions = dict(self._preferences.command_permissions)

        history_path = _history_path()
        history_path.parent.mkdir(parents=True, exist_ok=True)

        kb = KeyBindings()

        @kb.add("c-l")
        def _clear_screen(event):
            """Clear the screen and re-render."""
            event.app.renderer.clear()

        @kb.add("pageup")
        def _scroll_up(event):
            """Scroll transcript back."""
            max_offset = max(0, len(self.transcript) - 10)
            self._scroll_offset = min(self._scroll_offset + 10, max_offset)
            self._render_shell()

        @kb.add("pagedown")
        def _scroll_down(event):
            """Scroll transcript forward."""
            self._scroll_offset = max(0, self._scroll_offset - 10)
            self._render_shell()

        @kb.add("end")
        def _scroll_to_bottom(event):
            """Jump to latest messages."""
            self._scroll_offset = 0
            self._render_shell()

        self.prompt_session = PromptSession(
            history=FileHistory(str(history_path)),
            completer=_build_completer(),
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
            key_bindings=kb,
            style=Style.from_dict(
                {
                    "prompt": "ansicyan bold",
                    "trace": "ansibrightblack",
                }
            ),
        )

    def _save_preferences(self) -> None:
        """Persist current session preferences to disk."""
        self._preferences.trace_mode = self.trace_mode
        self._preferences.command_permissions = dict(self.command_permissions)
        save_preferences(self._preferences)

    def run(self) -> None:
        """Run the interactive prompt loop."""
        planner_lm = get_planner_lm_from_env(model_name=self.config.agent.model)
        delegate_lm = get_delegate_lm_from_env(
            model_name=self.config.agent.delegate_model,
            default_max_tokens=self.config.agent.delegate_max_tokens,
        )
        self._print_banner(planner_ready=planner_lm is not None)

        # First-run onboarding
        preferences_path = Path.home() / ".fleet" / "preferences.json"
        if not preferences_path.exists():
            render_onboarding_banner(self.console)

        if planner_lm is None:
            self.console.print(
                "[yellow]Planner LM not configured.[/] Use [bold]/settings[/] and "
                "[bold]/status[/] before sending chat prompts."
            )

        agent_context = build_chat_agent(
            docs_path=self.options.docs_path,
            react_max_iters=self.config.rlm_settings.max_iters,
            planner_lm=planner_lm,
            delegate_lm=delegate_lm,
        )

        lm_context = build_dspy_context(lm=planner_lm) if planner_lm else nullcontext()
        with lm_context, agent_context as agent:
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
                    self._print_error(str(exc), hint=_error_hint(exc))

    async def _run_chat_turn(self, agent: Any, message: str) -> None:
        """Run a single chat turn with Rich Live for flicker-free streaming."""
        from rich.live import Live

        from .ui import build_shell_layout

        trace_enabled = self.trace_mode != "off"
        assistant_chunks: list[str] = []
        tool_calls: list[str] = []
        final_text = ""
        final_payload: dict[str, Any] = {}
        self._append_transcript("you", message)
        self.is_processing = True
        self.last_status = "thinking..."
        token_since_render = 0

        def _layout(draft: str = "") -> Any:
            return build_shell_layout(
                session_id=self.session_id,
                model=self.config.agent.model,
                trace_mode=self.trace_mode,
                last_status=self.last_status,
                transcript=self.transcript,
                is_processing=self.is_processing,
                draft_assistant=draft,
            )

        try:
            with Live(
                _layout(),
                console=self.console,
                refresh_per_second=15,
                screen=True,
            ) as live:
                self._live = live

                async for event in agent.aiter_chat_turn_stream(
                    message=message,
                    trace=trace_enabled,
                ):
                    kind = event.kind
                    text = event.text or ""
                    stripped = text.strip()

                    if kind == "text":
                        assistant_chunks.append(text)
                        token_since_render += 1
                        if token_since_render >= 24:
                            live.update(_layout("".join(assistant_chunks)))
                            token_since_render = 0
                        continue

                    if kind == "status" and stripped:
                        self.last_status = stripped
                        if self.trace_mode == "verbose":
                            self._append_transcript("status", stripped)
                        live.update(_layout("".join(assistant_chunks)))
                        continue

                    if kind == "tool_call" and stripped:
                        tool_calls.append(stripped)
                        self.last_status = stripped
                        if self.trace_mode != "off":
                            self._append_transcript("tool", f"-> {stripped}")
                        live.update(_layout("".join(assistant_chunks)))
                        continue

                    if kind == "tool_result" and stripped and self.trace_mode == "verbose":
                        self._append_transcript("tool", f"* {stripped}")
                        live.update(_layout("".join(assistant_chunks)))
                        continue

                    if kind == "reasoning" and stripped and self.trace_mode == "verbose":
                        self._append_transcript("thinking", stripped)
                        live.update(_layout("".join(assistant_chunks)))
                        continue

                    if kind == "done":
                        final_text = text.strip()
                        payload = event.payload if isinstance(event.payload, dict) else {}
                        final_payload = dict(payload)
                        if payload.get("cancelled"):
                            self._print_warning("Turn cancelled.")
                        break

                    if kind == "error":
                        raise RuntimeError(stripped or "streaming error")
        finally:
            self._live = None
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
        return _bottom_toolbar_impl(
            self,
            model=self.config.agent.model,
            docs_count=0,
            trace_mode=self.trace_mode,
        )

    def _print_warning(self, message: str) -> None:
        """Print a warning message."""
        _print_warning_impl(self, message)

    def _print_error(self, message: str, *, hint: str | None = None) -> None:
        """Print an error message with an optional recovery hint."""
        _print_error_impl(self, message, hint=hint)

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
