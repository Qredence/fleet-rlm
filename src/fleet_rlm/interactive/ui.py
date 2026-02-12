"""Rich-based rendering helpers for interactive code-chat."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax


class ChatUI:
    """Small presentation layer for the interactive TUI."""

    def __init__(self, *, console: Console | None = None) -> None:
        self.console = console or Console()

    def banner(self, *, profile_name: str, trace: bool, stream: bool) -> None:
        self.console.print(
            Panel.fit(
                f"[bold cyan]fleet-rlm code-chat[/bold cyan]\n"
                f"profile=[bold]{profile_name}[/bold]  trace={trace}  stream={stream}\n"
                "Type /help for commands.",
                title="ReAct + RLM",
            )
        )

    def info(self, message: str) -> None:
        self.console.print(f"[cyan]{message}[/cyan]")

    def error(self, message: str) -> None:
        self.console.print(f"[bold red]{message}[/bold red]")

    def assistant(self, message: str) -> None:
        self.console.print(
            Panel(Markdown(message), title="assistant", border_style="green")
        )

    def trace(self, payload: Any) -> None:
        body = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        self.console.print(
            Panel(Syntax(body, "json", theme="monokai", word_wrap=True), title="trace")
        )

    def data(self, title: str, payload: Any) -> None:
        body = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        self.console.print(
            Panel(Syntax(body, "json", theme="monokai", word_wrap=True), title=title)
        )

    def show_help(self) -> None:
        commands = [
            "/exit - exit session",
            "/help - show this help",
            "/history - show chat history",
            "/reset - clear history + sandbox buffers",
            "/tools - list ReAct tools",
            "/load <path> - load document into active context",
            "/docs - show loaded document aliases",
            "/trace on|off - toggle trajectory display",
            "/profile show|set <name> - show current or switch active profile",
            "/py - execute multiline python in Modal interpreter (end with :end)",
            "/rg <pattern> [path] - ripgrep code/doc search",
            "/save-buffer <name> <path> - persist buffer to volume",
            "/load-volume <path> [alias] - load text file from volume",
        ]
        self.console.print(Panel("\n".join(commands), title="Commands"))
