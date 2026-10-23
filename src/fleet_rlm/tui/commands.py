"""Slash commands: one table drives dispatch, `/help`, session state updates, and tab completion."""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from rich.panel import Panel
from rich.text import Text

from fleet_rlm.config import FleetSettings, validate_turn_timeout_seconds
from fleet_rlm.tui.render import CONSOLE, TurnTracker, print_http_error

MODES = ("rlm", "flex", "hybrid")


@dataclass(slots=True)
class SessionState:
    """Client-side view of the active Session's mode, models, context, replay flag, and latest Turn."""

    mlflow_settings: FleetSettings | None = field(default=None, repr=False)
    mode: str = "hybrid"
    root_model: str = ""
    sub_model: str = ""
    context_path: str | None = None
    record_replay: bool = False
    # Client-local `/timeout` override: None inherits, 0 disables the Fleet deadline.
    # It is sent as `TurnRequest.timeout_seconds` and is never stored on the Session.
    timeout_seconds: int | None = None
    last_turn_id: str | None = None
    last_trace_id: str | None = None
    workspace_files: list[str] = field(default_factory=list)
    last_tracker: TurnTracker | None = None
    fullscreen: bool = False
    pending_model_choice: tuple[str, list[str]] | None = None


@dataclass(frozen=True, slots=True)
class Context:
    """HTTP client, Session ID, and mutable client SessionState that slash command handlers use."""

    client: httpx.Client
    session_id: str
    state: SessionState = field(default_factory=SessionState)

    @property
    def session(self) -> str:
        """Return the URL path prefix for this Session, with its ID percent-encoded."""
        return f"/sessions/{quote(self.session_id, safe='')}"

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a GET request, raising on an HTTP error status."""
        return _ok(self.client.get(path, **kwargs))

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a PUT request, raising on an HTTP error status."""
        return _ok(self.client.put(path, **kwargs))

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a POST request, raising on an HTTP error status."""
        return _ok(self.client.post(path, **kwargs))

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a DELETE request, raising on an HTTP error status."""
        return _ok(self.client.delete(path, **kwargs))


def _ok(response: httpx.Response) -> httpx.Response:
    response.raise_for_status()
    return response


def _file(path: str) -> str:
    return quote(path, safe="/")


@dataclass(frozen=True, slots=True)
class Command:
    """One slash command: its usage line, help text, handler, and completion options."""

    usage: str
    help: str
    run: Callable[[Context, str], None]
    completions: Any = None  # nested-completer options for the argument
    category: str = "Session & Models"


def _format_config_panel(data: dict[str, Any]) -> Text:
    """Format the `/config` response into a structured, readable overview while preserving raw values."""
    text = Text()
    mode = data.get("mode", "hybrid")
    raw_models = data.get("models")
    models = raw_models if isinstance(raw_models, dict) else {}
    raw_context = data.get("context")
    context = raw_context if isinstance(raw_context, dict) else None
    raw_limits = data.get("limits")
    limits = raw_limits if isinstance(raw_limits, dict) else {}
    raw_config = data.get("configuration")
    config_meta = raw_config if isinstance(raw_config, dict) else {}

    text.append("Execution mode: ", style="bold cyan")
    text.append(f"{mode}\n")

    text.append("Models:\n", style="bold cyan")
    text.append(f"  root : {models.get('root', '(default)')}\n")
    text.append(f"  sub  : {models.get('sub', '(default)')}\n")

    text.append("Dataset context: ", style="bold cyan")
    if context and context.get("workspace_path"):
        size = context.get("size_bytes", "?")
        sha = str(context.get("sha256", ""))[:12]
        sha_part = f", sha256={sha}…" if sha else ""
        text.append(f"{context['workspace_path']} ({size} bytes{sha_part})\n")
    else:
        text.append("(none selected)\n", style="dim")

    if limits:
        text.append("Limits: ", style="bold cyan")
        text.append(
            f"lm_calls={limits.get('lm_calls', '?')} · "
            f"depth={limits.get('depth', '?')} · "
            f"children={limits.get('children', '?')} (active={limits.get('active_children', '?')}) · "
            f"timeout={limits.get('timeout_seconds', '?')}s\n"
        )

    if config_meta:
        digest = str(config_meta.get("digest", ""))[:16]
        if digest:
            text.append("Config digest: ", style="bold cyan")
            text.append(f"{digest}\n", style="dim")
        raw_sources = config_meta.get("sources")
        sources = raw_sources if isinstance(raw_sources, dict) else {}
        raw_values = config_meta.get("values")
        values = raw_values if isinstance(raw_values, dict) else {}
        overrides = [f"{k}={values.get(k)} ({src})" for k, src in sources.items() if src not in {"default", ""}]
        if overrides:
            text.append("Active overrides: ", style="bold cyan")
            text.append(", ".join(overrides[:8]) + "\n", style="yellow")
    return text


def _config(ctx: Context, arg: str) -> None:
    if arg.startswith("mode "):
        _mode(ctx, arg.removeprefix("mode ").strip())
        return
    data = ctx.get(f"{ctx.session}/config").json()
    if isinstance(data, dict):
        if isinstance(data.get("mode"), str):
            ctx.state.mode = data["mode"]
        models = data.get("models")
        if isinstance(models, dict):
            ctx.state.root_model = str(models.get("root") or "")
            ctx.state.sub_model = str(models.get("sub") or "")
        context = data.get("context")
        ctx.state.context_path = (
            str(context["workspace_path"]) if isinstance(context, dict) and context.get("workspace_path") else None
        )
    if arg == "json" or not isinstance(data, dict):
        CONSOLE.print(Panel(json.dumps(data, indent=2), title="Session configuration"))
        return
    CONSOLE.print(Panel(_format_config_panel(data), title="Session configuration", border_style="cyan"))


def _models(ctx: Context, arg: str) -> None:
    scope = arg or "system.ai"
    models = ctx.get("/models", params={"scope": scope}).json()["models"]
    CONSOLE.print(Panel("\n".join(models) or "(no accessible chat services)", title=f"Models · {scope}"))


def apply_model_choice(ctx: Context, role: str, models: list[str], choice: str) -> None:
    """Validate a numbered model selection and bind it to `role`."""
    choice = choice.strip()
    if not choice:
        CONSOLE.print("[dim]Model selection cancelled; unchanged.[/dim]")
        return
    if not choice.isdigit() or not 1 <= int(choice) <= len(models):
        CONSOLE.print("[red]Invalid model selection; unchanged.[/red]")
        return
    selected = models[int(choice) - 1]
    model = ctx.put(f"{ctx.session}/model", json={"role": role, "model": selected}).json()["model"]
    if role == "root":
        ctx.state.root_model = str(model)
    else:
        ctx.state.sub_model = str(model)
    CONSOLE.print(f"[green]{role.title()} model:[/green] {model}")


def _model(ctx: Context, arg: str) -> None:
    args = arg.split()
    role = args[0] if args else "root"
    if role not in {"root", "sub"} or len(args) > 2:
        CONSOLE.print("[red]Usage: /model [root|sub] [model-id|catalog.schema][/red]")
        return
    selected = args[1] if len(args) > 1 else ""
    if not selected.startswith("openai/"):
        models = list(ctx.get("/models", params={"scope": selected or "system.ai"}).json()["models"])
        if not models:
            CONSOLE.print("[yellow]No accessible chat services. Model unchanged.[/yellow]")
            return
        for index, name in enumerate(models, 1):
            CONSOLE.print(f"{index}. {name}")
        if ctx.state.fullscreen:
            ctx.state.pending_model_choice = (role, models)
            CONSOLE.print(f"[cyan]Select {role} model [1-{len(models)}; Enter cancels] in the input box below.[/cyan]")
            return
        choice = CONSOLE.input(f"Select {role} model [1-{len(models)}; Enter cancels]: ").strip()
        apply_model_choice(ctx, role, models, choice)
        return
    model = ctx.put(f"{ctx.session}/model", json={"role": role, "model": selected}).json()["model"]
    if role == "root":
        ctx.state.root_model = str(model)
    else:
        ctx.state.sub_model = str(model)
    CONSOLE.print(f"[green]{role.title()} model:[/green] {model}")


def _attach(ctx: Context, arg: str) -> None:
    if not arg:
        CONSOLE.print("[yellow]Usage: /attach <path>[/yellow]")
        return
    path = Path(arg).expanduser()
    if not path.is_file():
        CONSOLE.print(f"[red]File not found:[/red] {path}")
        return
    with path.open("rb") as handle:
        payload = ctx.post(f"{ctx.session}/attach", files={"file": (path.name, handle)}).json()
    mounted = payload["mounted_path"]
    rel_path = payload.get("rel_path") or f"files/{path.name}"
    if rel_path not in ctx.state.workspace_files:
        ctx.state.workspace_files.append(rel_path)
    CONSOLE.print(f"[green]Attached:[/green] {mounted}")


def _listing(key: str, title: str, path: str) -> Callable[[Context, str], None]:
    def run(ctx: Context, _arg: str) -> None:
        data = ctx.get(f"{ctx.session}{path}").json()
        items = list(data.get(key, []))
        if key == "files":
            ctx.state.workspace_files = list(items)
        lines = [
            f"{item}  [active context]" if ctx.state.context_path and item == ctx.state.context_path else str(item)
            for item in items
        ]
        CONSOLE.print(Panel("\n".join(lines) or "(empty)", title=title))

    return run


def _format_skills_catalog(catalog_xml: str) -> str:
    """Format XML skill catalog entries into readable lines when valid XML is returned."""
    try:
        root = ElementTree.fromstring(catalog_xml)
    except ElementTree.ParseError:
        return catalog_xml or "(empty)"
    skills = [
        f"• {item.get('name')}: {' '.join((item.text or '').split())}"
        for item in root.iter("skill")
        if item.get("name")
    ]
    return "\n".join(skills) if skills else (catalog_xml or "(no skills in catalog)")


def _skills(ctx: Context, arg: str) -> None:
    params = {"session_id": ctx.session_id}
    if arg == "pull":
        CONSOLE.print(f"[green]Exported:[/green] {ctx.post('/skills/pull', params=params).json()['exported']}")
    elif arg == "push":
        CONSOLE.print(f"[green]Pushed:[/green] {ctx.post('/skills/push', params=params).json()['pushed']} Skills")
    else:
        catalog_xml = ctx.get("/skills", params=params).json()["catalog_xml"]
        CONSOLE.print(Panel(_format_skills_catalog(catalog_xml), title="Session Agent Skills"))


def _cat(ctx: Context, arg: str) -> None:
    if not arg:
        CONSOLE.print("[yellow]Usage: /cat <path>[/yellow]")
        return
    response = ctx.get(f"{ctx.session}/workspace/files/{_file(arg)}")
    CONSOLE.print(Panel(response.content.decode("utf-8", errors="replace"), title=arg))


def _stat(ctx: Context, arg: str) -> None:
    if not arg:
        CONSOLE.print("[yellow]Usage: /stat <path>[/yellow]")
        return
    item = ctx.get(f"{ctx.session}/workspace/stat/{_file(arg)}").json()
    CONSOLE.print(f"{item['path']}: {item['size_bytes']} bytes, sha256={item['sha256']}")


def _put(ctx: Context, arg: str) -> None:
    """Upload a local file and cache its workspace path, defaulting to `files/<basename>`.

    `arg` contains shell-quoted source and optional destination paths. Missing arguments
    or a non-file source print a message and return; parsing, file-read, and HTTP errors propagate.
    """
    args = shlex.split(arg)
    if not args:
        CONSOLE.print("[yellow]Usage: /put <local-path> [workspace-path][/yellow]")
        return
    source = Path(args[0]).expanduser()
    if not source.is_file():
        CONSOLE.print(f"[red]File not found:[/red] {source}")
        return
    destination = args[1] if len(args) > 1 else f"files/{source.name}"
    content = source.read_bytes()
    ctx.put(
        f"{ctx.session}/workspace/files/{_file(destination)}",
        json={"content_base64": base64.b64encode(content).decode("ascii")},
    )
    if destination not in ctx.state.workspace_files:
        ctx.state.workspace_files.append(destination)
    CONSOLE.print(f"[green]Written:[/green] {destination}")


def _get(ctx: Context, arg: str) -> None:
    """Download a workspace file, overwriting the chosen local path or its basename by default.

    `arg` contains shell-quoted workspace and optional local paths. Missing arguments
    print usage and return; parsing, HTTP, and file-write errors propagate.
    """
    args = shlex.split(arg)
    if not args:
        CONSOLE.print("[yellow]Usage: /get <workspace-path> [local-path][/yellow]")
        return
    target = Path(args[1]).expanduser() if len(args) > 1 else Path(Path(args[0]).name)
    target.write_bytes(ctx.get(f"{ctx.session}/workspace/files/{_file(args[0])}").content)
    CONSOLE.print(f"[green]Downloaded:[/green] {target}")


def _rm(ctx: Context, arg: str) -> None:
    if not arg:
        CONSOLE.print("[yellow]Usage: /rm <path>[/yellow]")
        return
    ctx.delete(f"{ctx.session}/workspace/files/{_file(arg)}")
    if arg in ctx.state.workspace_files:
        ctx.state.workspace_files.remove(arg)
    if ctx.state.context_path == arg:
        ctx.state.context_path = None
    CONSOLE.print(f"[green]Deleted:[/green] {arg}")


def _mode(ctx: Context, arg: str) -> None:
    response = ctx.put(f"{ctx.session}/mode", json={"mode": arg}) if arg else ctx.get(f"{ctx.session}/mode")
    mode = str(response.json()["mode"])
    ctx.state.mode = mode
    CONSOLE.print(f"[cyan]Execution mode:[/cyan] {mode}")


def _history(ctx: Context, _arg: str) -> None:
    turns = ctx.get(f"{ctx.session}/history").json().get("turns", [])
    entries: list[str] = []
    for idx, item in enumerate(turns, 1):
        meta_parts = [f"#{idx}"]
        if item.get("route"):
            meta_parts.append(f"route={item['route']}")
        trace_id = item.get("trace_id") or item.get("mlflow_trace_id")
        if trace_id:
            meta_parts.append(f"trace={trace_id}")
            ctx.state.last_trace_id = str(trace_id)
        if item.get("turn_id"):
            ctx.state.last_turn_id = str(item["turn_id"])
        header = f"[{' · '.join(meta_parts)}] {item.get('task', '')}"
        entries.append(f"{header}\n{item.get('answer', '')}")
    CONSOLE.print(Panel("\n\n".join(entries) or "(empty)", title="Session history"))


def _memory(ctx: Context, arg: str) -> None:
    memories = ctx.get(f"{ctx.session}/memory", params={"q": arg} if arg else None).json().get("memories", [])
    entries = []
    for item in memories:
        status = "reviewed" if item.get("reviewed") else "unreviewed"
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
        ctx.state.last_trace_id = str(trace_id)
        CONSOLE.print(f"[dim]Persisted MLflow trace ID:[/dim] {trace_id}")
    else:
        CONSOLE.print("[yellow]Trace unavailable:[/yellow] saved turn history contains no trace ID.")


def _turn(ctx: Context, arg: str) -> None:
    turn_id = arg or ctx.state.last_turn_id
    if not turn_id:
        CONSOLE.print("[yellow]Usage: /turn <turn-id> (no Turn ID recorded in this client session yet)[/yellow]")
        return
    data = ctx.get(f"{ctx.session}/turns/{quote(turn_id, safe='')}").json()
    status = str(data.get("status", "unknown"))
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    lines = [
        f"Turn ID : {data.get('turn_id', turn_id)}",
        f"Status  : {status}",
        f"Updated : {data.get('updated_at', 'unknown')}",
    ]
    if result.get("route"):
        lines.append(f"Route   : {result['route']}")
    if result.get("trace_id"):
        lines.append(f"Trace   : {result['trace_id']}")
    if result.get("answer"):
        lines.append(f"\nAnswer:\n{result['answer']}")
    elif result.get("error"):
        lines.append(f"\nError:\n{result['error']}")
    style = "green" if status == "completed" else ("yellow" if status in {"running", "cancelled"} else "red")
    CONSOLE.print(Panel(Text("\n".join(lines)), title=f"Turn status · {status}", border_style=style))


def _replay(ctx: Context, arg: str) -> None:
    if not arg:
        state_label = "enabled" if ctx.state.record_replay else "disabled"
        CONSOLE.print(f"[cyan]Replay capture:[/cyan] {state_label}")
        return
    if arg not in {"on", "off"}:
        CONSOLE.print("[yellow]Usage: /replay on|off[/yellow]")
        return
    ctx.state.record_replay = arg == "on"
    state = "enabled for subsequent Turns" if ctx.state.record_replay else "disabled"
    CONSOLE.print(f"[dim]Replay capture {state}.[/dim]")


def _timeout(ctx: Context, arg: str) -> None:
    """Hold a client-local deadline override for subsequent Turns in this client only."""
    if not arg:
        current = ctx.state.timeout_seconds
        if current is None:
            label = "inheriting the server configuration"
        elif current == 0:
            label = "off (no Fleet deadline)"
        else:
            label = f"{current}s"
        CONSOLE.print(f"[cyan]Client timeout:[/cyan] {label}  [dim](use /timeout <seconds|off|default>)[/dim]")
        return
    if arg == "default":
        ctx.state.timeout_seconds = None
        CONSOLE.print("[green]Timeout override cleared:[/green] subsequent Turns inherit the server configuration.")
        return
    if arg == "off":
        ctx.state.timeout_seconds = 0
        CONSOLE.print(
            "[green]Timeout off:[/green] subsequent Turns have no Fleet deadline; "
            "provider TTL and idle bounds still apply."
        )
        return
    if not re.fullmatch(r"[0-9]+", arg):
        CONSOLE.print("[red]Invalid /timeout value:[/red] use an integer number of seconds, `off`, or `default`.")
        return
    try:
        seconds = validate_turn_timeout_seconds(int(arg), field="/timeout")
    except ValueError:
        CONSOLE.print("[red]Invalid /timeout value:[/red] use 0, or a value between 10 and 3600 seconds.")
        return
    ctx.state.timeout_seconds = seconds
    if seconds == 0:
        CONSOLE.print(
            "[green]Timeout set to 0:[/green] subsequent Turns have no Fleet deadline; "
            "provider TTL and idle bounds still apply."
        )
    else:
        CONSOLE.print(f"[green]Timeout set:[/green] subsequent Turns will request {seconds}s.")


def _context(ctx: Context, arg: str) -> None:
    if not arg:
        current = ctx.state.context_path or "(none selected)"
        CONSOLE.print(f"[cyan]Current context:[/cyan] {current}  [dim](use /context <workspace-path>|clear)[/dim]")
        return
    if arg == "clear":
        ctx.delete(f"{ctx.session}/context")
        ctx.state.context_path = None
        CONSOLE.print("[green]Context selection cleared.[/green]")
    else:
        ctx.put(f"{ctx.session}/context", json={"path": arg})
        ctx.state.context_path = arg
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
    settings = ctx.state.mlflow_settings
    backend = settings.mlflow_mode if settings is not None else "local"
    for index, value in enumerate(extra):
        selected = (
            (extra[index + 1] if index + 1 < len(extra) else "")
            if value == "--mlflow"
            else (value.split("=", 1)[1] if value.startswith("--mlflow=") else None)
        )
        if selected is not None and selected != backend:
            CONSOLE.print("[red]/optimize must use the session's MLflow backend.[/red]")
            return
    command = [
        sys.executable,
        "-m",
        "fleet_rlm.cli",
        "optimize",
        "--session",
        ctx.session_id,
        *extra,
        "--mlflow",
        backend,
    ]
    environment = dict(os.environ)
    for key in ("MLFLOW_TRACKING_URI", "MLFLOW_EXPERIMENT_NAME", "MLFLOW_EXPERIMENT_ID"):
        environment.pop(key, None)
    if settings is not None:
        environment.update(settings.mlflow_environment())
    if getattr(getattr(ctx, "state", None), "fullscreen", False):
        completed = subprocess.run(command, check=False, capture_output=True, text=True, env=environment)
        output = (completed.stdout or "") + (completed.stderr or "")
        CONSOLE.print(Panel(output.strip() or "(no output)", title="Optimization run"))
        return
    subprocess.run(command, check=False, env=environment)


_MODE_OPTIONS = dict.fromkeys(MODES)
COMMANDS: dict[str, Command] = {
    "/attach": Command("/attach <path>", "add a file to this Session", _attach, category="Workspace & Context"),
    "/files": Command(
        "/files",
        "list files in this Session's workspace",
        _listing("files", "Files", "/files"),
        category="Workspace & Context",
    ),
    "/artifacts": Command(
        "/artifacts",
        "list generated artifacts",
        _listing("artifacts", "Artifacts", "/files"),
        category="Workspace & Context",
    ),
    "/skills": Command(
        "/skills [pull|push]",
        "show the Skills catalog; pull exports to `.fleet/skills/`, push syncs local Skills",
        _skills,
        {"push": None, "pull": None},
        category="Skills & Memory",
    ),
    "/tree": Command(
        "/tree",
        "show workspace files",
        _listing("files", "Workspace tree", "/workspace/tree"),
        category="Workspace & Context",
    ),
    "/cat": Command("/cat <path>", "read a workspace file", _cat, category="Workspace & Context"),
    "/stat": Command(
        "/stat <path>", "show size and SHA-256 for a workspace file", _stat, category="Workspace & Context"
    ),
    "/put": Command(
        "/put <local-path> [workspace-path]", "upload a workspace file", _put, category="Workspace & Context"
    ),
    "/get": Command(
        "/get <workspace-path> [local-path]", "download a workspace file", _get, category="Workspace & Context"
    ),
    "/rm": Command("/rm <path>", "delete a workspace file", _rm, category="Workspace & Context"),
    "/context": Command(
        "/context <workspace-path>|clear",
        "select an input file, or clear the selection",
        _context,
        {"clear": None},
        category="Workspace & Context",
    ),
    "/mode": Command(
        "/mode [rlm|flex|hybrid]", "inspect or change execution mode", _mode, _MODE_OPTIONS, category="Session & Models"
    ),
    "/timeout": Command(
        "/timeout <seconds|off|default>",
        "set this client's Fleet deadline override for subsequent Turns (client-local)",
        _timeout,
        {"off": None, "default": None},
        category="Session & Models",
    ),
    "/config": Command(
        "/config [mode <mode>|json]",
        "inspect non-secret Session settings and limits",
        _config,
        {"mode": _MODE_OPTIONS, "json": None},
        category="Session & Models",
    ),
    "/settings": Command(
        "/settings [mode <mode>|json]",
        "same as /config",
        _config,
        {"mode": _MODE_OPTIONS, "json": None},
        category="Session & Models",
    ),
    "/models": Command(
        "/models [catalog.schema]", "discover accessible Gateway chat services", _models, category="Session & Models"
    ),
    "/model": Command(
        "/model [root|sub] [model-id|catalog.schema]",
        "choose interactively or set a model",
        _model,
        {"root": None, "sub": None},
        category="Session & Models",
    ),
    "/memory": Command(
        "/memory [query]", "show saved memories, optionally filtered by query", _memory, category="Skills & Memory"
    ),
    "/history": Command("/history", "show saved turns", _history, category="Evaluation & Traces"),
    "/trace": Command(
        "/trace", "show the most recent persisted turn trace ID, if available", _trace, category="Evaluation & Traces"
    ),
    "/turn": Command(
        "/turn [turn-id]",
        "inspect the durable status and outcome of the latest or given Turn",
        _turn,
        category="Evaluation & Traces",
    ),
    "/replay": Command(
        "/replay [on|off]",
        "enable or disable bounded replay snapshot capture for subsequent Turns",
        _replay,
        {"on": None, "off": None},
        category="Evaluation & Traces",
    ),
    "/sessions": Command("/sessions", "list saved Sessions", _sessions, category="Session & Models"),
    "/optimize": Command(
        "/optimize [--max-metric-calls N | --approve VERSION]",
        "score past Turns, compile a GEPA candidate, and gate it; --approve serves it from the next Turn",
        _optimize,
        {"--max-metric-calls": None, "--approve": None},
        category="Evaluation & Traces",
    ),
}
EXIT_COMMANDS = {"/exit", "/quit"}


def help_text() -> str:
    """Return the `/help` text grouped by category, plus live REPL expand/collapse shortcuts."""
    categories = ("Session & Models", "Workspace & Context", "Skills & Memory", "Evaluation & Traces")
    sections: list[str] = []
    for category in categories:
        items = [f"- `{cmd.usage}` — {cmd.help}" for cmd in COMMANDS.values() if cmd.category == category]
        if items:
            sections.append(f"## {category}\n" + "\n".join(items))
    sections.append(
        "## Interactive Shortcuts & General\n"
        "- Click `▸`/`▾` or press `Ctrl+O` (`e` while streaming) — expand/collapse REPL code & output\n"
        "- Press `Alt+A` (`a` while streaming) or `Alt+1`..`Alt+9` — toggle all cells or a numbered REPL cell\n"
        "- Press `Ctrl+T` (`t` while streaming) — expand/collapse model Thinking blocks\n"
        "- `/help` — show this list\n"
        "- `/exit` — quit the CLI"
    )
    return "\n\n".join(sections)


def completion_options() -> dict[str, Any]:
    """Return the nested completer options for every slash command, including help and exit."""
    return {
        **{name: command.completions for name, command in COMMANDS.items()},
        **dict.fromkeys(["/help", *EXIT_COMMANDS]),
    }


def handle_command(
    client: httpx.Client,
    command: str,
    session_id: str,
    state: SessionState | None = None,
) -> bool:
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
        entry.run(Context(client, session_id, state if state is not None else SessionState()), arg.strip())
    except httpx.HTTPStatusError as exc:
        print_http_error(exc)
    except httpx.HTTPError as exc:
        CONSOLE.print(f"[red]Fleet API is unavailable:[/red] {type(exc).__name__}")
    except (OSError, ValueError, KeyError, IndexError) as exc:
        CONSOLE.print(f"[red]Command failed:[/red] {type(exc).__name__}")
    except (KeyboardInterrupt, EOFError):
        CONSOLE.print("[dim]Command cancelled; no further changes requested.[/dim]")
    return True
