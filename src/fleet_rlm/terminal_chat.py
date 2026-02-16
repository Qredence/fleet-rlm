"""Standalone terminal chat runtime for the `fleet` entrypoint."""

from __future__ import annotations

import asyncio
import getpass
import json
import os
import re
import shlex
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dspy
from dotenv import set_key
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import input_dialog, radiolist_dialog, yes_no_dialog
from prompt_toolkit.shortcuts.prompt import CompleteStyle
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import runners
from .config import AppConfig
from .core.config import get_planner_lm_from_env
from .models import TraceMode
from .react.commands import COMMAND_DISPATCH
from .utils.modal import get_default_volume_name, load_modal_config

_TRACE_MODES: set[str] = {"compact", "verbose", "off"}
_SETTINGS_KEYS = (
    "DSPY_LM_MODEL",
    "DSPY_LLM_API_KEY",
    "DSPY_LM_API_BASE",
    "DSPY_LM_MAX_TOKENS",
)


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


class _TerminalChatSession:
    def __init__(self, *, config: AppConfig, options: TerminalChatOptions) -> None:
        self.config = config
        self.options = options
        self.trace_mode: TraceMode = _normalize_trace_mode(options.trace_mode)
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
            completer=_FleetCompleter(),
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
                        self._prompt_label(),
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
                    should_exit = self._handle_slash(agent, line)
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
                        self._append_transcript("tool", f"↳ {stripped}")
                        self._render_shell(draft_assistant="".join(assistant_chunks))
                    continue

                if kind == "tool_result" and stripped and self.trace_mode == "verbose":
                    self._append_transcript("tool", f"✓ {stripped}")
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
                f"{len(tool_calls)} tool actions · use /trace verbose for details",
            )
        self._append_transcript("assistant", assistant_response)
        self.last_status = "ready"
        self._render_shell()

        warnings = list(final_payload.get("guardrail_warnings", []) or [])
        for warning in warnings:
            self._print_warning(str(warning))

    def _handle_slash(self, agent: Any, line: str) -> bool:
        command, arg_text = _split_slash_command(line)

        if command in {"/", "/help", "/commands"}:
            return self._print_command_palette(agent)
        if command == "/?":
            self._show_shortcuts()
            return False

        if command in {"/exit", "/quit"}:
            self.console.print("[dim]bye[/dim]")
            return True

        if command == "/clear":
            self.console.clear()
            self._print_banner(planner_ready=True)
            return False

        if command == "/reset":
            if not _confirm("Reset agent history and clear sandbox buffers?"):
                self._print_warning("Reset cancelled.")
                return False
            result = agent.reset(clear_sandbox_buffers=True)
            self._print_result(result, title="reset")
            return False

        if command == "/trace":
            mode = arg_text.strip().lower()
            if mode not in _TRACE_MODES:
                self._print_error("usage: /trace <compact|verbose|off>")
                return False
            self.trace_mode = _normalize_trace_mode(mode)
            self.console.print(f"[green]Trace mode set to {self.trace_mode}[/]")
            return False

        if command == "/status":
            self._print_status(agent)
            return False

        if command == "/settings":
            self._run_settings(arg_text.strip().lower())
            return False
        if command == "/model":
            self._run_settings("model")
            return False
        if command == "/permissions":
            self._print_permissions()
            return False
        if command == "/permissions-reset":
            self.command_permissions.clear()
            self._print_warning("Permission policy reset.")
            return False

        if command == "/run-long-context":
            self._run_long_context(arg_text)
            return False

        if command == "/check-secret":
            self._check_secret()
            return False

        if command == "/check-secret-key":
            key = arg_text.strip() or "DSPY_LLM_API_KEY"
            self._check_secret_key(key=key)
            return False

        alias_result = self._handle_alias_command(agent, command, arg_text)
        if alias_result is not None:
            return alias_result

        canonical = command.lstrip("/")
        if canonical in COMMAND_DISPATCH:
            try:
                payload = _parse_command_payload(arg_text)
            except Exception as exc:
                self._print_error(str(exc))
                return False
            self._execute_agent_command(agent, canonical, payload)
            return False

        self._print_unknown_command(command)
        return False

    def _handle_alias_command(
        self, agent: Any, command: str, arg_text: str
    ) -> bool | None:
        args = _safe_split(arg_text)

        if command in {"/docs", "/load"}:
            if not args:
                self._print_error("usage: /docs <path> [alias]")
                return False
            payload: dict[str, Any] = {"path": args[0]}
            if len(args) > 1:
                payload["alias"] = args[1]
            self._execute_agent_command(agent, "load_document", payload)
            return False

        if command == "/active":
            if not args:
                self._print_error("usage: /active <alias>")
                return False
            self._execute_agent_command(
                agent, "set_active_document", {"alias": args[0]}
            )
            return False

        if command == "/list":
            self._execute_agent_command(agent, "list_documents", {})
            return False

        if command == "/chunk":
            if not args:
                self._print_error(
                    "usage: /chunk <size|headers|timestamps|json> [chunk_size]"
                )
                return False
            payload: dict[str, Any] = {"strategy": args[0]}
            if len(args) > 1 and args[1].isdigit():
                payload["size"] = int(args[1])
            self._execute_agent_command(agent, "chunk_host", payload)
            return False

        if command == "/analyze":
            query = arg_text.strip()
            if not query:
                self._print_error("usage: /analyze <query>")
                return False
            self._execute_agent_command(agent, "analyze_document", {"query": query})
            return False

        if command == "/summarize":
            focus = arg_text.strip()
            if not focus:
                self._print_error("usage: /summarize <focus>")
                return False
            self._execute_agent_command(agent, "summarize_document", {"focus": focus})
            return False

        if command == "/extract":
            query = arg_text.strip()
            if not query:
                self._print_error("usage: /extract <query>")
                return False
            self._execute_agent_command(agent, "extract_logs", {"query": query})
            return False

        if command == "/semantic":
            query = arg_text.strip()
            if not query:
                self._print_error("usage: /semantic <query>")
                return False
            self._execute_agent_command(
                agent, "parallel_semantic_map", {"query": query}
            )
            return False

        if command == "/buffer":
            if not args:
                self._print_error("usage: /buffer <name>")
                return False
            self._execute_agent_command(agent, "read_buffer", {"name": args[0]})
            return False

        if command == "/clear-buffer":
            payload: dict[str, Any] = {"name": args[0]} if args else {}
            if not args and not _confirm("Clear all buffers?"):
                self._print_warning("clear-buffer cancelled.")
                return False
            self._execute_agent_command(agent, "clear_buffer", payload)
            return False

        if command == "/save-buffer":
            if len(args) < 2:
                self._print_error("usage: /save-buffer <buffer_name> <volume_path>")
                return False
            self._execute_agent_command(
                agent,
                "save_buffer",
                {"name": args[0], "path": args[1]},
            )
            return False

        if command == "/load-volume":
            if not args:
                self._print_error("usage: /load-volume <path> [alias]")
                return False
            payload: dict[str, Any] = {"path": args[0]}
            if len(args) > 1:
                payload["alias"] = args[1]
            self._execute_agent_command(agent, "load_volume", payload)
            return False

        return None

    def _execute_agent_command(
        self, agent: Any, command: str, args: dict[str, Any]
    ) -> None:
        if not self._authorize_command(command=command):
            return
        confirm_message = _confirmation_message(command=command, args=args)
        if confirm_message and not _confirm(confirm_message):
            self._print_warning("Command cancelled.")
            return

        try:
            result = asyncio.run(agent.execute_command(command, args))
            self._print_result(result, title=command)
        except Exception as exc:  # pragma: no cover - runtime path
            self._print_error(str(exc))

    def _print_status(self, agent: Any) -> None:
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

    def _run_settings(self, section: str) -> None:
        section_norm = section.strip().lower()
        if not section_norm:
            section_norm = (
                _prompt_choice(
                    "Settings section:",
                    ["llm", "model", "modal"],
                    allow_freeform=True,
                )
                .strip()
                .lower()
            )

        if section_norm in {"llm", "model"}:
            self._settings_llm(model_only=section_norm == "model")
            return

        if section_norm == "modal":
            self._settings_modal()
            return

        self._print_error("unknown settings section. try: /settings llm|model|modal")

    def _settings_llm(self, *, model_only: bool) -> None:
        env_path = _resolve_env_path()
        updates: dict[str, str] = {}

        self.console.print(
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
            self._print_warning("No changes made.")
            return

        if not _confirm(f"Write {len(updates)} update(s) to {env_path}?"):
            self._print_warning("Settings update cancelled.")
            return

        _write_env_updates(env_path=env_path, updates=updates)
        self.console.print(
            f"[green]Updated[/] {', '.join(sorted(updates))} in [bold]{env_path}[/]"
        )

    def _settings_modal(self) -> None:
        env_path = _resolve_env_path()
        updates: dict[str, str] = {}

        self.console.print(
            Panel(
                "Store Modal credentials in local .env (optional for this CLI).",
                title="settings modal",
            )
        )

        token_id = _prompt_value(
            key="MODAL_TOKEN_ID",
            default="",
            secret=False,
        )
        if token_id:
            updates["MODAL_TOKEN_ID"] = token_id

        token_secret = _prompt_value(
            key="MODAL_TOKEN_SECRET",
            default="",
            secret=True,
        )
        if token_secret:
            updates["MODAL_TOKEN_SECRET"] = token_secret

        if updates:
            if _confirm(f"Write {len(updates)} Modal value(s) to {env_path}?"):
                _write_env_updates(env_path=env_path, updates=updates)
                self.console.print(
                    f"[green]Updated[/] {', '.join(sorted(updates))} in [bold]{env_path}[/]"
                )
            else:
                self._print_warning("Settings update cancelled.")
        else:
            self._print_warning("No changes made.")

        self.console.print("\n[bold]next steps:[/]")
        self.console.print("  uv run modal setup")
        self.console.print("  uv run modal volume create rlm-volume-dspy")
        self.console.print("  uv run modal secret create LITELLM ...")

    def _run_long_context(self, arg_text: str) -> None:
        if not self._authorize_command(command="run-long-context"):
            return
        args = _safe_split(arg_text)
        if not args:
            docs_path = _prompt_value(key="docs_path", default="", secret=False)
            query = _prompt_value(key="query", default="", secret=False)
            mode = _prompt_choice(
                "Mode:",
                ["analyze", "summarize"],
                allow_freeform=False,
            )
        else:
            docs_path = args[0]
            mode = "analyze"
            query_parts = args[1:]
            if query_parts and query_parts[-1] in {"analyze", "summarize"}:
                mode = query_parts[-1]
                query_parts = query_parts[:-1]
            query = " ".join(query_parts)

        if not docs_path or not query:
            self._print_error(
                "usage: /run-long-context <docs_path> <query> [analyze|summarize]"
            )
            return

        with self.console.status(
            "[cyan]Running long-context task...[/]", spinner="line"
        ):
            try:
                result = runners.run_long_context(
                    docs_path=docs_path,
                    query=query,
                    mode=mode,
                    max_iterations=self.config.rlm_settings.max_iterations,
                    max_llm_calls=self.config.rlm_settings.max_llm_calls,
                    verbose=self.config.rlm_settings.verbose,
                    timeout=self.config.interpreter.timeout,
                    secret_name=self.secret_name,
                    volume_name=self.volume_name,
                )
            except Exception as exc:  # pragma: no cover - runtime path
                self._print_error(str(exc))
                return

        self._print_result(result, title="run-long-context")

    def _check_secret(self) -> None:
        if not self._authorize_command(command="check-secret"):
            return
        with self.console.status("[cyan]Checking secret...[/]", spinner="line"):
            try:
                result = runners.check_secret_presence(secret_name=self.secret_name)
            except Exception as exc:  # pragma: no cover - runtime path
                self._print_error(str(exc))
                return
        self._print_result(result, title="check-secret")

    def _check_secret_key(self, *, key: str) -> None:
        if not self._authorize_command(command="check-secret-key"):
            return
        with self.console.status("[cyan]Checking secret key...[/]", spinner="line"):
            try:
                result = runners.check_secret_key(secret_name=self.secret_name, key=key)
            except Exception as exc:  # pragma: no cover - runtime path
                self._print_error(str(exc))
                return
        self._print_result(result, title=f"check-secret-key ({key})")

    def _print_command_palette(self, agent: Any) -> bool:
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
            for spec in sorted(
                _COMMAND_SPECS, key=lambda item: (item.category, item.name)
            )
            if (
                not query_norm
                or query_norm in spec.name.lower()
                or query_norm in spec.summary.lower()
                or query_norm in spec.category.lower()
            )
        ]
        if not specs:
            self._print_warning("No commands match that filter.")
            return False

        values = [
            (spec.name, f"{spec.name:<20} {spec.summary}  [{spec.category}]")
            for spec in specs
        ]
        selected = radiolist_dialog(
            title="Slash command palette",
            text="Select a command (↑/↓, Enter):",
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
        return self._handle_slash(agent, quick_line)

    def _print_unknown_command(self, command: str) -> None:
        options = sorted({spec.name for spec in _COMMAND_SPECS})
        options.extend(f"/{name}" for name in sorted(COMMAND_DISPATCH))
        suggestions = [opt for opt in options if opt.startswith(command)][:6]
        if suggestions:
            self._print_error(
                f"Unknown command: {command}. Did you mean: {', '.join(suggestions)}"
            )
            return
        self._print_error(f"Unknown command: {command}. Type /help for commands.")

    def _print_result(self, result: dict[str, Any], *, title: str) -> None:
        rendered = json.dumps(result, indent=2, sort_keys=True, default=str)
        self._append_transcript("result", f"{title}\n{rendered}")
        self.last_status = f"{title} complete"
        self._render_shell()

    def _print_banner(self, *, planner_ready: bool) -> None:
        planner_text = (
            "[green]ready[/]" if planner_ready else "[yellow]not configured[/]"
        )
        content = (
            "[bold cyan]fleet[/]  [dim]Copilot-style mode[/]\n"
            "Describe a task to get started.\n\n"
            "Use [bold]/model[/], [bold]/settings[/], [bold]/status[/], and "
            "[bold]/[/] for the command palette.\n"
            f"[dim]session={self.session_id}  planner={planner_text}[/]"
        )
        self.console.print(
            Panel(content, title="fleet chat", border_style="cyan", expand=False)
        )
        self.console.print(
            f"[dim]• workspace:[/] {Path.cwd()}    "
            f"[dim]model:[/] {self.config.agent.model}"
        )

    def _prompt_label(self) -> HTML:
        return HTML("<prompt>❯ </prompt>")

    def _bottom_toolbar(self) -> HTML:
        mode = "thinking" if self.is_processing else "idle"
        return HTML(
            "<trace>"
            " Type @ to mention files, / for commands, or ? for shortcuts "
            f" · mode={mode}"
            "</trace>"
        )

    def _print_warning(self, message: str) -> None:
        self._append_transcript("warning", message)
        self.last_status = "warning"
        self._render_shell()

    def _print_error(self, message: str) -> None:
        self._append_transcript("error", message)
        self.last_status = "error"
        self._render_shell()

    def _print_permissions(self) -> None:
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
        self._append_transcript(
            "status",
            (
                "Shortcuts: / opens command palette · @ mentions files · "
                "Ctrl+C interrupts · /trace compact|verbose|off"
            ),
        )
        self._render_shell()

    def _append_transcript(self, role: str, content: str) -> None:
        text = content.strip()
        if not text:
            return
        self.transcript.append((role, text))
        if len(self.transcript) > 200:
            self.transcript = self.transcript[-200:]

    def _render_shell(self, *, draft_assistant: str = "") -> None:
        header = Panel(
            Text.from_markup(
                f"[bold]fleet[/]  "
                f"[dim]session[/]={self.session_id}  "
                f"[dim]model[/]={self.config.agent.model}  "
                f"[dim]trace[/]={self.trace_mode}  "
                f"[dim]status[/]={self.last_status}"
            ),
            border_style="cyan",
            padding=(0, 1),
        )

        body_text = Text()
        for role, content in self.transcript[-30:]:
            role_style = {
                "you": "bold white",
                "assistant": "bold cyan",
                "status": "dim",
                "tool": "magenta",
                "thinking": "dim",
                "warning": "yellow",
                "error": "red",
                "result": "blue",
            }.get(role, "white")
            body_text.append(f"{role}> ", style=role_style)
            body_text.append(content + "\n\n")

        if self.is_processing and draft_assistant:
            body_text.append("assistant> ", style="bold cyan")
            body_text.append(draft_assistant + "\n")

        transcript_panel = Panel(
            body_text
            if body_text.plain.strip()
            else Text("No messages yet.", style="dim"),
            border_style="bright_black",
            title="chat",
        )

        hint = (
            "Enter=send • Shift+Enter=newline • /=command palette • "
            "Ctrl+C=interrupt • /help=commands"
        )
        footer = Panel(Text(hint, style="dim"), border_style="bright_black")

        layout = Layout()
        layout.split_column(
            Layout(header, size=3),
            Layout(transcript_panel, ratio=1),
            Layout(footer, size=3),
        )

        self.console.clear()
        self.console.print(layout)


class _FleetCompleter(Completer):
    """Slash + file mention completer used by the chat composer."""

    def __init__(self) -> None:
        self._slash_entries: list[tuple[str, str]] = sorted(
            [(spec.name, spec.summary) for spec in _COMMAND_SPECS]
            + [(f"/{name}", "tool command") for name in sorted(COMMAND_DISPATCH)],
            key=lambda item: item[0],
        )

    def get_completions(self, document: Any, complete_event: Any):
        text = document.text_before_cursor

        if text.startswith("/"):
            token = text.split(maxsplit=1)[0]
            for command, summary in self._slash_entries:
                if command.startswith(token):
                    yield Completion(
                        command,
                        start_position=-len(token),
                        display=command,
                        display_meta=summary,
                    )
            if text.startswith("/settings "):
                sub = (
                    text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
                )
                for choice in ("llm", "model", "modal"):
                    if choice.startswith(sub):
                        yield Completion(
                            choice,
                            start_position=-len(sub),
                            display=choice,
                            display_meta="settings scope",
                        )
            return

        mention = re.search(r"@(\S*)$", text)
        if not mention:
            return
        prefix = mention.group(1)
        for candidate in _iter_mention_paths(prefix):
            yield Completion(
                candidate,
                start_position=-len(prefix),
                display=f"@{candidate}",
                display_meta="file path",
            )


def _history_path() -> Path:
    return Path.home() / ".fleet" / "history.txt"


def _badge(ok: bool) -> str:
    return "[green]OK[/]" if ok else "[yellow]WARN[/]"


def _confirmation_message(*, command: str, args: dict[str, Any]) -> str | None:
    if command == "write_to_file" and not bool(args.get("append")):
        path = str(args.get("path", "<unknown>"))
        return f"Overwrite file at {path}?"
    if command == "clear_buffer" and not args.get("name"):
        return "Clear all sandbox buffers?"
    return None


def _confirm(question: str) -> bool:
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


def _normalize_trace_mode(value: str) -> TraceMode:
    return value if value in _TRACE_MODES else "compact"  # type: ignore[return-value]


def _split_slash_command(line: str) -> tuple[str, str]:
    stripped = line.strip()
    if not stripped:
        return "/", ""
    parts = stripped.split(maxsplit=1)
    command = parts[0].lower()
    arg_text = parts[1] if len(parts) > 1 else ""
    return command, arg_text


def _safe_split(arg_text: str) -> list[str]:
    try:
        return shlex.split(arg_text)
    except ValueError:
        return arg_text.split()


def _iter_mention_paths(prefix: str, *, limit: int = 40) -> list[str]:
    base = Path.cwd()
    query = prefix.strip()
    prefix_dir = ""
    name_prefix = query
    root = base

    if query:
        as_path = Path(query)
        if as_path.is_absolute():
            parent = as_path.parent if as_path.parent.as_posix() else Path("/")
            root = parent if parent.exists() else Path("/")
            prefix_dir = (
                f"{as_path.parent.as_posix().rstrip('/')}/"
                if as_path.parent.as_posix()
                else ""
            )
            name_prefix = as_path.name
        elif "/" in query:
            maybe_dir, name_prefix = query.rsplit("/", 1)
            resolved = (base / maybe_dir).resolve()
            if resolved.exists() and resolved.is_dir():
                root = resolved
                prefix_dir = maybe_dir.rstrip("/") + "/"

    suggestions: list[str] = []
    try:
        entries = sorted(
            root.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower())
        )
    except Exception:
        return suggestions

    if not query:
        suggestions.append(str(base))

    lowered = name_prefix.lower()
    for entry in entries:
        if lowered and not entry.name.lower().startswith(lowered):
            continue
        suffix = "/" if entry.is_dir() else ""
        suggestion = f"{prefix_dir}{entry.name}{suffix}"
        suggestions.append(suggestion)
        if len(suggestions) >= limit:
            break
    return suggestions


def _parse_command_payload(arg_text: str) -> dict[str, Any]:
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


def _resolve_env_path() -> Path:
    for parent in [Path.cwd(), *Path.cwd().parents]:
        if (parent / "pyproject.toml").exists():
            return parent / ".env"
    return Path.cwd() / ".env"


def _write_env_updates(*, env_path: Path, updates: dict[str, str]) -> None:
    env_path.touch(exist_ok=True)
    for key, value in updates.items():
        if key in _SETTINGS_KEYS and not value:
            continue
        set_key(str(env_path), key, value)
        os.environ[key] = value


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}...{value[-2:]}"


def _prompt_value(*, key: str, default: str, secret: bool) -> str:
    shown_default = _mask_secret(default) if secret else default
    suffix = f" [{shown_default}]" if shown_default else ""
    try:
        if secret:
            raw = getpass.getpass(f"{key}{suffix}: ").strip()
        else:
            raw = input(f"{key}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):  # pragma: no cover - interactive path
        return ""
    return raw


def _prompt_choice(
    prompt: str,
    choices: list[str],
    *,
    allow_freeform: bool,
) -> str:
    try:
        values = [(str(index), choice) for index, choice in enumerate(choices, start=1)]
        if allow_freeform:
            values.append(("0", "Custom input"))
        picked = radiolist_dialog(
            title="Select option",
            text=prompt,
            values=values,
            style=_dialog_style(),
        ).run()
        if picked is None:
            return ""
        if picked == "0":
            custom = input_dialog(
                title="Custom input",
                text=prompt,
                style=_dialog_style(),
            ).run()
            return (custom or "").strip()
        if picked.isdigit():
            number = int(picked)
            if 1 <= number <= len(choices):
                return choices[number - 1]
    except Exception:
        pass

    print(prompt)
    for index, choice in enumerate(choices, start=1):
        print(f"  {index}) {choice}")
    if allow_freeform:
        print("  0) custom input")
    while True:
        selection = input("Select option: ").strip()
        if selection.isdigit():
            number = int(selection)
            if 1 <= number <= len(choices):
                return choices[number - 1]
            if allow_freeform and number == 0:
                return input("Custom value: ").strip()
        if allow_freeform and selection:
            return selection
        print("Invalid selection.")


def _dialog_style() -> Style:
    return Style.from_dict(
        {
            "dialog": "bg:#101114",
            "dialog frame.label": "fg:#77d6ff bold",
            "dialog.body": "fg:#f0f3f7",
            "dialog shadow": "bg:#050607",
            "button.focused": "bg:#2d7ff9 #ffffff",
        }
    )
