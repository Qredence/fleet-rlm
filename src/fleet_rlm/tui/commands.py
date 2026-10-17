"""Slash commands: one table drives dispatch, `/help`, and tab completion."""

from __future__ import annotations

import base64
import json
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from rich.panel import Panel

from fleet_rlm.tui.render import CONSOLE, print_http_error

MODES = ("rlm", "flex", "hybrid")


@dataclass(frozen=True, slots=True)
class Context:
    client: httpx.Client
    session_id: str

    @property
    def session(self) -> str:
        return f"/sessions/{quote(self.session_id, safe='')}"

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return _ok(self.client.get(path, **kwargs))

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return _ok(self.client.put(path, **kwargs))

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return _ok(self.client.post(path, **kwargs))

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return _ok(self.client.delete(path, **kwargs))


def _ok(response: httpx.Response) -> httpx.Response:
    response.raise_for_status()
    return response


def _file(path: str) -> str:
    return quote(path, safe="/")


@dataclass(frozen=True, slots=True)
class Command:
    usage: str
    help: str
    run: Callable[[Context, str], None]
    completions: Any = None  # nested-completer options for the argument


def _config(ctx: Context, arg: str) -> None:
    if arg.startswith("mode "):
        _mode(ctx, arg.removeprefix("mode ").strip())
        return
    CONSOLE.print(Panel(json.dumps(ctx.get(f"{ctx.session}/config").json(), indent=2), title="Session configuration"))


def _models(ctx: Context, arg: str) -> None:
    scope = arg or "system.ai"
    models = ctx.get("/models", params={"scope": scope}).json()["models"]
    CONSOLE.print(Panel("\n".join(models) or "(no accessible chat services)", title=f"Models · {scope}"))


def _model(ctx: Context, arg: str) -> None:
    args = arg.split()
    role = args[0] if args else "root"
    if role not in {"root", "sub"} or len(args) > 2:
        CONSOLE.print("[red]Usage: /model [root|sub] [model-id|catalog.schema][/red]")
        return
    selected = args[1] if len(args) > 1 else ""
    if not selected.startswith("openai/"):
        models = ctx.get("/models", params={"scope": selected or "system.ai"}).json()["models"]
        if not models:
            CONSOLE.print("[yellow]No accessible chat services. Model unchanged.[/yellow]")
            return
        for index, name in enumerate(models, 1):
            CONSOLE.print(f"{index}. {name}")
        choice = CONSOLE.input(f"Select {role} model [1-{len(models)}; Enter cancels]: ").strip()
        if not choice:
            CONSOLE.print("[dim]Model selection cancelled; unchanged.[/dim]")
            return
        if not choice.isdigit() or not 1 <= int(choice) <= len(models):
            CONSOLE.print("[red]Invalid model selection; unchanged.[/red]")
            return
        selected = models[int(choice) - 1]
    model = ctx.put(f"{ctx.session}/model", json={"role": role, "model": selected}).json()["model"]
    CONSOLE.print(f"[green]{role.title()} model:[/green] {model}")


def _attach(ctx: Context, arg: str) -> None:
    path = Path(arg).expanduser()
    if not path.is_file():
        CONSOLE.print(f"[red]File not found:[/red] {path}")
        return
    with path.open("rb") as handle:
        mounted = ctx.post(f"{ctx.session}/attach", files={"file": (path.name, handle)}).json()["mounted_path"]
    CONSOLE.print(f"[green]Attached:[/green] {mounted}")


def _listing(key: str, title: str, path: str) -> Callable[[Context, str], None]:
    def run(ctx: Context, _arg: str) -> None:
        data = ctx.get(f"{ctx.session}{path}").json()
        CONSOLE.print(Panel("\n".join(data.get(key, [])) or "(empty)", title=title))

    return run


def _skills(ctx: Context, arg: str) -> None:
    params = {"session_id": ctx.session_id}
    if arg == "pull":
        CONSOLE.print(f"[green]Exported:[/green] {ctx.post('/skills/pull', params=params).json()['exported']}")
    elif arg == "push":
        CONSOLE.print(f"[green]Pushed:[/green] {ctx.post('/skills/push', params=params).json()['pushed']} Skills")
    else:
        CONSOLE.print(Panel(ctx.get("/skills", params=params).json()["catalog_xml"], title="Session Agent Skills"))


def _cat(ctx: Context, arg: str) -> None:
    response = ctx.get(f"{ctx.session}/workspace/files/{_file(arg)}")
    CONSOLE.print(Panel(response.content.decode("utf-8", errors="replace"), title=arg))


def _stat(ctx: Context, arg: str) -> None:
    item = ctx.get(f"{ctx.session}/workspace/stat/{_file(arg)}").json()
    CONSOLE.print(f"{item['path']}: {item['size_bytes']} bytes, sha256={item['sha256']}")


def _put(ctx: Context, arg: str) -> None:
    args = arg.split()
    source = Path(args[0]).expanduser()
    destination = args[1] if len(args) > 1 else f"files/{source.name}"
    content = source.read_bytes()
    ctx.put(
        f"{ctx.session}/workspace/files/{_file(destination)}",
        json={"content_base64": base64.b64encode(content).decode("ascii")},
    )
    CONSOLE.print(f"[green]Written:[/green] {destination}")


def _get(ctx: Context, arg: str) -> None:
    args = arg.split()
    target = Path(args[1]).expanduser() if len(args) > 1 else Path(Path(args[0]).name)
    target.write_bytes(ctx.get(f"{ctx.session}/workspace/files/{_file(args[0])}").content)
    CONSOLE.print(f"[green]Downloaded:[/green] {target}")


def _rm(ctx: Context, arg: str) -> None:
    ctx.delete(f"{ctx.session}/workspace/files/{_file(arg)}")
    CONSOLE.print(f"[green]Deleted:[/green] {arg}")


def _mode(ctx: Context, arg: str) -> None:
    response = ctx.put(f"{ctx.session}/mode", json={"mode": arg}) if arg else ctx.get(f"{ctx.session}/mode")
    CONSOLE.print(f"[cyan]Execution mode:[/cyan] {response.json()['mode']}")


def _history(ctx: Context, _arg: str) -> None:
    turns = ctx.get(f"{ctx.session}/history").json().get("turns", [])
    text = "\n\n".join(str(item.get("task", "")) + "\n" + str(item.get("answer", "")) for item in turns)
    CONSOLE.print(Panel(text or "(empty)", title="Session history"))


def _memory(ctx: Context, arg: str) -> None:
    memories = ctx.get(f"{ctx.session}/memory", params={"q": arg} if arg else None).json().get("memories", [])
    entries = []
    for item in memories:
        status = "reviewed" if item.get("reviewed") else "unreviewed"
        if item.get("stale"):
            status += ", stale sources"
        entries.append(
            f"[{item.get('kind', 'memory')}] {item.get('content', '')}\n"
            f"ID: {item.get('memory_id', 'unknown')} · {status}"
        )
    CONSOLE.print(Panel("\n\n".join(entries) or "(no saved memories)", title="Saved memories"))


def _trace(ctx: Context, _arg: str) -> None:
    turns = ctx.get(f"{ctx.session}/history").json().get("turns", [])
    traced = [item for item in turns if item.get("trace_id") or item.get("mlflow_trace_id")]
    if traced:
        trace_id = traced[-1].get("trace_id") or traced[-1].get("mlflow_trace_id")
        CONSOLE.print(f"[dim]Persisted MLflow trace ID:[/dim] {trace_id}")
    else:
        CONSOLE.print("[yellow]Trace unavailable:[/yellow] saved turn history contains no trace ID.")


def _context(ctx: Context, arg: str) -> None:
    if arg == "clear":
        ctx.delete(f"{ctx.session}/context")
        CONSOLE.print("[green]Context selection cleared.[/green]")
    else:
        ctx.put(f"{ctx.session}/context", json={"path": arg})
        CONSOLE.print(f"[green]Context selected:[/green] {arg}")


def _sessions(ctx: Context, _arg: str) -> None:
    for item in ctx.get("/sessions").json().get("sessions", []):
        CONSOLE.print(
            f"• [yellow]{item['session_id']}[/yellow] ({item['turn_count']} turns; updated {item['updated_at']})"
        )


def _optimize(ctx: Context, arg: str) -> None:
    """Run `fleet optimize` for this Session; extra arguments pass through (e.g. `--approve VERSION`)."""
    try:
        extra = shlex.split(arg)
    except ValueError as exc:
        CONSOLE.print(f"[red]Invalid /optimize arguments:[/red] {exc}")
        return
    command = [sys.executable, "-m", "fleet_rlm.cli", "optimize", "--session", ctx.session_id, *extra]
    subprocess.run(command, check=False)


_MODE_OPTIONS = dict.fromkeys(MODES)
COMMANDS: dict[str, Command] = {
    "/attach": Command("/attach <path>", "add a file to this Session", _attach),
    "/files": Command("/files", "list files in this Session's workspace", _listing("files", "Files", "/files")),
    "/artifacts": Command("/artifacts", "list generated artifacts", _listing("artifacts", "Artifacts", "/files")),
    "/skills": Command(
        "/skills [pull|push]",
        "show the Skills catalog; pull exports to `.fleet/skills/`, push syncs local Skills",
        _skills,
        {"push": None, "pull": None},
    ),
    "/tree": Command("/tree", "show workspace files", _listing("files", "Workspace tree", "/workspace/tree")),
    "/cat": Command("/cat <path>", "read a workspace file", _cat),
    "/stat": Command("/stat <path>", "show size and SHA-256 for a workspace file", _stat),
    "/put": Command("/put <local-path> [workspace-path]", "upload a workspace file", _put),
    "/get": Command("/get <workspace-path> [local-path]", "download a workspace file", _get),
    "/rm": Command("/rm <path>", "delete a workspace file", _rm),
    "/context": Command(
        "/context <workspace-path>|clear", "select an input file, or clear the selection", _context, {"clear": None}
    ),
    "/mode": Command("/mode [rlm|flex|hybrid]", "inspect or change execution mode", _mode, _MODE_OPTIONS),
    "/config": Command(
        "/config [mode <mode>]", "inspect non-secret Session settings and limits", _config, {"mode": _MODE_OPTIONS}
    ),
    "/settings": Command("/settings", "same as /config", _config, {"mode": _MODE_OPTIONS}),
    "/models": Command("/models [catalog.schema]", "discover accessible Gateway chat services", _models),
    "/model": Command(
        "/model [root|sub] [model-id|catalog.schema]",
        "choose interactively or set a model",
        _model,
        {"root": None, "sub": None},
    ),
    "/memory": Command("/memory [query]", "show saved memories, optionally filtered by query", _memory),
    "/history": Command("/history", "show saved turns", _history),
    "/trace": Command("/trace", "show the most recent persisted turn trace ID, if available", _trace),
    "/sessions": Command("/sessions", "list saved Sessions", _sessions),
    "/optimize": Command(
        "/optimize [--max-metric-calls N | --approve VERSION]",
        "score past Turns, compile a GEPA candidate, and gate it; --approve serves it from the next Turn",
        _optimize,
    ),
}
EXIT_COMMANDS = {"/exit", "/quit"}


def help_text() -> str:
    lines = [f"- `{command.usage}` — {command.help}" for command in COMMANDS.values()]
    return "\n".join([*lines, "- `/help` — show this list", "- `/exit` — quit the CLI"])


def completion_options() -> dict[str, Any]:
    return {
        **{name: command.completions for name, command in COMMANDS.items()},
        **dict.fromkeys(["/help", *EXIT_COMMANDS]),
    }


def handle_command(client: httpx.Client, command: str, session_id: str) -> bool:
    """Run one slash command. Return False when the CLI should exit."""
    name, _, arg = command.strip().partition(" ")
    if name in EXIT_COMMANDS:
        return False
    if name == "/help":
        CONSOLE.print(help_text(), markup=False, highlight=False)
        return True
    entry = COMMANDS.get(name)
    if entry is None:
        CONSOLE.print("[yellow]Unknown command. Use /help; submit a task without a leading slash.[/yellow]")
        return True
    try:
        entry.run(Context(client, session_id), arg.strip())
    except httpx.HTTPStatusError as exc:
        print_http_error(exc)
    except httpx.HTTPError as exc:
        CONSOLE.print(f"[red]Fleet API is unavailable:[/red] {type(exc).__name__}")
    except (OSError, ValueError, KeyError, IndexError) as exc:
        CONSOLE.print(f"[red]Command failed:[/red] {type(exc).__name__}")
    except (KeyboardInterrupt, EOFError):
        CONSOLE.print("[dim]Command cancelled; no further changes requested.[/dim]")
    return True
