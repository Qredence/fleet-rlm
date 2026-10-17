"""The interactive prompt loop: read input, run slash commands, and stream Turns."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, NestedCompleter, WordCompleter, merge_completers
from prompt_toolkit.history import FileHistory
from rich.panel import Panel

from fleet_rlm.tui.commands import completion_options, handle_command
from fleet_rlm.tui.render import CONSOLE, print_http_error, render_event
from fleet_rlm.workspace import validate_session_id


def _completer() -> Completer:
    arguments = NestedCompleter.from_nested_dict(completion_options())
    return merge_completers([WordCompleter(list(arguments.options), sentence=True), arguments], deduplicate=True)


def run_interactive_cli(base_url: str, session_id: str) -> None:
    """Connect to the API and stream one Session's Turns until exit."""
    validate_session_id(session_id)
    prompt = None
    if sys.stdin.isatty() and CONSOLE.is_terminal:
        history_path = Path(".fleet/history") / f"{session_id}.txt"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        prompt = PromptSession(
            history=FileHistory(str(history_path)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=_completer(),
        )
    CONSOLE.print(
        Panel.fit(
            f"[bold cyan]Fleet-RLM Lite[/bold cyan] | Session: [yellow]{session_id}[/yellow]\n"
            "Enter `/help` for commands; `/replay on|off` selects bounded replay capture; `/exit` to quit."
        )
    )
    record_replay = False
    with httpx.Client(base_url=base_url, timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        while True:
            try:
                user_input = (
                    prompt.prompt("fleet> ") if prompt else CONSOLE.input("\n[bold green]fleet>[/bold green] ")
                ).strip()
            except KeyboardInterrupt:
                CONSOLE.print("[dim]Input cleared. Use /exit to quit.[/dim]")
                continue
            except EOFError:
                CONSOLE.print()
                break
            if not user_input:
                continue
            if user_input.startswith("/"):
                if user_input.startswith("/replay"):
                    parts = user_input.split()
                    if len(parts) != 2 or parts[1] not in {"on", "off"}:
                        CONSOLE.print("[yellow]Usage: /replay on|off[/yellow]")
                    else:
                        record_replay = parts[1] == "on"
                        state = "enabled for subsequent Turns" if record_replay else "disabled"
                        CONSOLE.print(f"[dim]Replay capture {state}.[/dim]")
                    continue
                if not handle_command(client, user_input, session_id):
                    break
                continue
            try:
                terminal_event = False
                with client.stream(
                    "POST",
                    f"/sessions/{quote(session_id, safe='')}/turn",
                    json={"task": user_input, "idempotency_key": uuid.uuid4().hex, "record_replay": record_replay},
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line[5:].lstrip())
                        except json.JSONDecodeError:
                            CONSOLE.print("[yellow]Ignoring malformed SSE event.[/yellow]")
                            continue
                        if not isinstance(event, dict) or not isinstance(event.get("payload", {}), dict):
                            CONSOLE.print("[yellow]Ignoring malformed SSE event.[/yellow]")
                            continue
                        terminal_event |= event.get("event") in {"turn_complete", "turn_cancelled", "error"}
                        render_event(event)
                if not terminal_event:
                    CONSOLE.print(
                        "[yellow]Stream ended without a final outcome. Check /history before retrying.[/yellow]"
                    )
            except httpx.HTTPStatusError as exc:
                print_http_error(exc)
            except httpx.HTTPError as exc:
                CONSOLE.print(f"[red]Turn stream failed:[/red] {type(exc).__name__}")
            except KeyboardInterrupt:
                CONSOLE.print(
                    "[yellow]Turn stream closed; server cancels on disconnect. Check /history for outcome.[/yellow]"
                )
