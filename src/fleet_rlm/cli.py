"""Pure-Python Rich CLI for the Fleet-RLM Lite API."""

from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import uvicorn
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from fleet_rlm.config import get_settings

_CONSOLE = Console()


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _print_http_error(error: httpx.HTTPStatusError) -> None:
    response = error.response
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    _CONSOLE.print(f"[bold red]HTTP {response.status_code}:[/bold red] {str(detail)[:1000]}")


def _handle_command(client: httpx.Client, command: str, session_id: str) -> bool:
    """Handle a slash command. Return False when the CLI should exit."""
    if command in {"/exit", "/quit"}:
        return False
    if command == "/help":
        _CONSOLE.print(
            "- `/attach <path>` — add a file to this Session\n"
            "- `/files` — list files in this Session's workspace\n"
            "- `/artifacts` — list generated artifacts\n"
            "- `/skills` — show this Session's Agent Skills catalog\n"
            "- `/skills pull` — export this Session's Skills into `.fleet/skills/`\n"
            "- `/sessions` — list saved Sessions\n"
            "- `/optimize` — collect traces, evaluate, and compile with GEPA\n"
            "- `/exit` — quit the CLI"
        )
        return True
    try:
        if command.startswith("/attach "):
            path = Path(command.split(" ", 1)[1].strip()).expanduser()
            if not path.is_file():
                _CONSOLE.print(f"[red]File not found:[/red] {path}")
                return True
            with path.open("rb") as handle:
                response = client.post(
                    f"/sessions/{quote(session_id, safe='')}/attach",
                    files={"file": (path.name, handle)},
                )
            response.raise_for_status()
            _CONSOLE.print(f"[green]Attached:[/green] {response.json()['mounted_path']}")
        elif command in {"/files", "/artifacts"}:
            response = client.get(f"/sessions/{quote(session_id, safe='')}/files")
            response.raise_for_status()
            data = response.json()
            key = "artifacts" if command == "/artifacts" else "files"
            _CONSOLE.print(Panel("\n".join(data.get(key, [])) or "(empty)", title=key.title()))
        elif command == "/skills":
            response = client.get("/skills", params={"session_id": session_id})
            response.raise_for_status()
            _CONSOLE.print(Panel(response.json()["catalog_xml"], title="Session Agent Skills"))
        elif command == "/skills pull":
            response = client.post("/skills/pull", params={"session_id": session_id})
            response.raise_for_status()
            _CONSOLE.print(f"[green]Exported:[/green] {response.json()['exported']}")
        elif command == "/sessions":
            response = client.get("/sessions")
            response.raise_for_status()
            for item in response.json().get("sessions", []):
                _CONSOLE.print(
                    f"• [yellow]{item['session_id']}[/yellow] "
                    f"({item['turn_count']} turns; updated {item['updated_at']})"
                )
        elif command == "/optimize":
            script = Path(__file__).resolve().parents[2] / "scripts" / "optimize_and_evaluate_fleet.py"
            if not script.is_file():
                _CONSOLE.print("[red]Optimization script is only available in a source checkout.[/red]")
            else:
                subprocess.run(
                    [sys.executable, str(script), "--session", session_id],
                    cwd=script.parents[1],
                    check=False,
                )
        else:
            return True
    except httpx.HTTPStatusError as exc:
        _print_http_error(exc)
    except httpx.HTTPError as exc:
        _CONSOLE.print(f"[red]Fleet API is unavailable:[/red] {type(exc).__name__}")
    except (OSError, ValueError, KeyError) as exc:
        _CONSOLE.print(f"[red]Command failed:[/red] {str(exc)[:500]}")
    return True


def run_interactive_cli(base_url: str, session_id: str) -> None:
    """Connect to the API and stream one Session's Turns until exit."""
    _CONSOLE.print(
        Panel.fit(
            f"[bold cyan]Fleet-RLM Lite[/bold cyan] | Session: [yellow]{session_id}[/yellow]\n"
            "Enter `/help` for commands; `/exit` to quit."
        )
    )
    with httpx.Client(base_url=base_url, timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        while True:
            try:
                user_input = _CONSOLE.input("\n[bold green]fleet>[/bold green] ").strip()
            except (EOFError, KeyboardInterrupt):
                _CONSOLE.print()
                break
            if not user_input:
                continue
            if user_input.startswith("/"):
                if not _handle_command(client, user_input, session_id):
                    break
                continue
            try:
                with client.stream(
                    "POST",
                    f"/sessions/{quote(session_id, safe='')}/turn",
                    json={"task": user_input},
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(line[6:])
                        except json.JSONDecodeError:
                            _CONSOLE.print("[yellow]Ignoring malformed SSE event.[/yellow]")
                            continue
                        _render_event(event)
            except httpx.HTTPStatusError as exc:
                _print_http_error(exc)
            except httpx.HTTPError as exc:
                _CONSOLE.print(f"[red]Turn stream failed:[/red] {type(exc).__name__}")


def _render_event(event: dict[str, Any]) -> None:
    event_type = event.get("event")
    if event_type == "module_start":
        _CONSOLE.print(f"  [dim]→ Module:[/dim] [cyan]{event.get('module', 'unknown')}[/cyan]")
    elif event_type == "tool_start":
        _CONSOLE.print(f"  [dim]⚙ Tool:[/dim] {str(event.get('tool', 'unknown'))[:100]}")
    elif event_type == "tool_end" and not event.get("ok", True):
        _CONSOLE.print(f"  [red]Tool error:[/red] {event.get('error_type', 'unknown')}")
    elif event_type == "turn_complete":
        _CONSOLE.print(
            Panel(
                Markdown(str(event.get("answer", ""))),
                title=f"Answer (route={event.get('route', 'unknown')})",
                border_style="green",
            )
        )
        if event.get("learned_skill_note"):
            _CONSOLE.print(f"[magenta]Learned note:[/magenta] {event['learned_skill_note']}")
        if event.get("artifacts"):
            _CONSOLE.print(f"[yellow]Artifacts:[/yellow] {event['artifacts']}")
        if event.get("trace_id"):
            _CONSOLE.print(f"[dim]MLflow trace ID:[/dim] {event['trace_id']}")
    elif event_type == "error":
        _CONSOLE.print(f"[bold red]Turn failed:[/bold red] {event.get('error', 'Unknown error')}")


def main(argv: list[str] | None = None) -> None:
    """Run the backend or the interactive Rich client."""
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="fleet", description="Fleet-RLM Lite server and terminal client")
    commands = parser.add_subparsers(dest="command")

    serve = commands.add_parser("serve", help="run the FastAPI SSE server")
    serve.add_argument("--host", default=settings.host)
    serve.add_argument("--port", type=int, default=settings.port)

    cli = commands.add_parser("cli", help="connect the interactive terminal client")
    cli.add_argument("--url", default=f"http://{settings.host}:{settings.port}")
    cli.add_argument("--session", default=f"sess_{uuid.uuid4().hex[:8]}")

    args = parser.parse_args(argv)
    if args.command == "serve":
        if not settings.allow_non_loopback_bind and not _is_loopback(args.host):
            parser.error("non-loopback binding requires FLEET_ALLOW_NON_LOOPBACK_BIND=true")
        uvicorn.run("fleet_rlm.server:app", host=args.host, port=args.port, reload=False)
        return

    session_id = getattr(args, "session", f"sess_{uuid.uuid4().hex[:8]}")
    base_url = getattr(args, "url", f"http://{settings.host}:{settings.port}")
    run_interactive_cli(base_url=base_url, session_id=session_id)


if __name__ == "__main__":
    main()
