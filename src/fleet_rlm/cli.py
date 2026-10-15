"""Pure-Python Rich CLI for the Fleet-RLM Lite API."""

from __future__ import annotations

import argparse
import base64
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
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import NestedCompleter, WordCompleter, merge_completers
from prompt_toolkit.history import FileHistory
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from fleet_rlm import launcher
from fleet_rlm.config import get_settings
from fleet_rlm.engine.routes import Route
from fleet_rlm.interpreter import validate_session_id

_CONSOLE = Console()
_COMMAND_ARGUMENTS = NestedCompleter.from_nested_dict(
    {
        **dict.fromkeys(
            [
                "/help",
                "/attach",
                "/files",
                "/artifacts",
                "/tree",
                "/cat",
                "/stat",
                "/put",
                "/get",
                "/rm",
                "/memory",
                "/history",
                "/trace",
                "/reload",
                "/sessions",
                "/optimize",
                "/exit",
                "/quit",
                "/models",
            ]
        ),
        "/skills": {"push": None, "pull": None},
        "/context": {"clear": None},
        "/mode": dict.fromkeys(["rlm", "flex", "hybrid"]),
        "/model": {"root": None, "sub": None},
        "/config": {"mode": dict.fromkeys(["rlm", "flex", "hybrid"])},
        "/settings": {"mode": dict.fromkeys(["rlm", "flex", "hybrid"])},
    }
)
_COMMAND_COMPLETER = merge_completers(
    [WordCompleter(list(_COMMAND_ARGUMENTS.options), sentence=True), _COMMAND_ARGUMENTS], deduplicate=True
)


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _print_http_error(error: httpx.HTTPStatusError) -> None:
    _CONSOLE.print(f"[bold red]HTTP {error.response.status_code}:[/bold red] Request was not completed.")


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
            "- `/skills push` — sync local Skills into this Session\n"
            "- `/tree` — show workspace files\n"
            "- `/cat <path>` — read a workspace file\n"
            "- `/stat <path>` — show size and SHA-256 for a workspace file\n"
            "- `/put <local-path> [workspace-path]` — upload a workspace file\n"
            "- `/get <workspace-path> [local-path]` — download a workspace file\n"
            "- `/rm <path>` — delete a workspace file\n"
            "- `/context <workspace-path>` — select an input file; `/context clear` clears it\n"
            "- `/mode [rlm|flex|hybrid]` — inspect or change execution mode\n"
            "- `/config` or `/settings` — inspect non-secret Session settings and limits\n"
            "- `/config mode <mode>` — change Session execution mode\n"
            "- `/models [catalog.schema]` — discover accessible Gateway chat services\n"
            "- `/model [root|sub] [model-id|catalog.schema]` — choose interactively or set a model\n"
            "- `/memory [query]` — show saved memories, optionally filtered by query\n"
            "- `/history` — show saved turns\n"
            "- `/trace` — show the most recent persisted turn trace ID, if available\n"
            "- `/reload` — approve reloading the saved Flex program at a turn boundary\n"
            "- `/sessions` — list saved Sessions\n"
            "- `/optimize` — evaluate reviewed, gold-labeled Session examples and compile with GEPA\n"
            "- `/exit` — quit the CLI"
        )
        return True
    try:
        if command in {"/config", "/settings"}:
            response = client.get(f"/sessions/{quote(session_id, safe='')}/config")
            response.raise_for_status()
            _CONSOLE.print(Panel(json.dumps(response.json(), indent=2), title="Session configuration"))
        elif command.startswith(("/config mode ", "/settings mode ")):
            return _handle_command(client, "/mode " + command.split(maxsplit=2)[2], session_id)
        elif command == "/models" or command.startswith("/models "):
            scope = command.partition(" ")[2].strip() or "system.ai"
            response = client.get("/models", params={"scope": scope})
            response.raise_for_status()
            models = response.json()["models"]
            _CONSOLE.print(Panel("\n".join(models) or "(no accessible chat services)", title=f"Models · {scope}"))
        elif command == "/model" or command.startswith("/model "):
            args = command.split()
            role = args[1] if len(args) > 1 else "root"
            if role not in {"root", "sub"} or len(args) > 3:
                _CONSOLE.print("[red]Usage: /model [root|sub] [model-id|catalog.schema][/red]")
                return True
            selected = args[2] if len(args) > 2 else ""
            if not selected.startswith("openai/"):
                scope = selected or "system.ai"
                response = client.get("/models", params={"scope": scope})
                response.raise_for_status()
                models = response.json()["models"]
                if not models:
                    _CONSOLE.print("[yellow]No accessible chat services. Model unchanged.[/yellow]")
                    return True
                for index, name in enumerate(models, 1):
                    _CONSOLE.print(f"{index}. {name}")
                choice = _CONSOLE.input(f"Select {role} model [1-{len(models)}; Enter cancels]: ").strip()
                if not choice:
                    _CONSOLE.print("[dim]Model selection cancelled; unchanged.[/dim]")
                    return True
                if not choice.isdigit() or not 1 <= int(choice) <= len(models):
                    _CONSOLE.print("[red]Invalid model selection; unchanged.[/red]")
                    return True
                selected = models[int(choice) - 1]
            response = client.put(
                f"/sessions/{quote(session_id, safe='')}/model", json={"role": role, "model": selected}
            )
            response.raise_for_status()
            _CONSOLE.print(f"[green]{role.title()} model:[/green] {response.json()['model']}")
        elif command.startswith("/attach "):
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
        elif command == "/skills push":
            response = client.post("/skills/push", params={"session_id": session_id})
            response.raise_for_status()
            _CONSOLE.print(f"[green]Pushed:[/green] {response.json()['pushed']} Skills")
        elif command == "/tree":
            response = client.get(f"/sessions/{quote(session_id, safe='')}/workspace/tree")
            response.raise_for_status()
            _CONSOLE.print(Panel("\n".join(response.json().get("files", [])) or "(empty)", title="Workspace tree"))
        elif command.startswith("/cat "):
            path = command.split(" ", 1)[1].strip()
            response = client.get(f"/sessions/{quote(session_id, safe='')}/workspace/files/{quote(path, safe='/')}")
            response.raise_for_status()
            _CONSOLE.print(Panel(response.content.decode("utf-8", errors="replace"), title=path))
        elif command.startswith("/stat "):
            path = command.split(" ", 1)[1].strip()
            response = client.get(f"/sessions/{quote(session_id, safe='')}/workspace/stat/{quote(path, safe='/')}")
            response.raise_for_status()
            item = response.json()
            _CONSOLE.print(f"{item['path']}: {item['size_bytes']} bytes, sha256={item['sha256']}")
        elif command.startswith("/put "):
            args = command.split()
            source = Path(args[1]).expanduser()
            destination = args[2] if len(args) > 2 else f"files/{source.name}"
            with source.open("rb") as handle:
                content = handle.read()
            response = client.put(
                f"/sessions/{quote(session_id, safe='')}/workspace/files/{quote(destination, safe='/')}",
                json={"content_base64": base64.b64encode(content).decode("ascii")},
            )
            response.raise_for_status()
            _CONSOLE.print(f"[green]Written:[/green] {destination}")
        elif command.startswith("/get "):
            args = command.split()
            source = args[1]
            download_path = Path(args[2]).expanduser() if len(args) > 2 else Path(Path(source).name)
            response = client.get(f"/sessions/{quote(session_id, safe='')}/workspace/files/{quote(source, safe='/')}")
            response.raise_for_status()
            download_path.write_bytes(response.content)
            _CONSOLE.print(f"[green]Downloaded:[/green] {download_path}")
        elif command.startswith("/rm "):
            path = command.split(" ", 1)[1].strip()
            response = client.delete(f"/sessions/{quote(session_id, safe='')}/workspace/files/{quote(path, safe='/')}")
            response.raise_for_status()
            _CONSOLE.print(f"[green]Deleted:[/green] {path}")
        elif command == "/mode" or command.startswith("/mode "):
            path = f"/sessions/{quote(session_id, safe='')}/mode"
            mode = command.partition(" ")[2].strip()
            response = client.put(path, json={"mode": mode}) if mode else client.get(path)
            response.raise_for_status()
            _CONSOLE.print(f"[cyan]Execution mode:[/cyan] {response.json()['mode']}")
        elif command == "/history":
            response = client.get(f"/sessions/{quote(session_id, safe='')}/history")
            response.raise_for_status()
            turns = response.json().get("turns", [])
            _CONSOLE.print(
                Panel(
                    "\n\n".join(str(item.get("task", "")) + "\n" + str(item.get("answer", "")) for item in turns)
                    or "(empty)",
                    title="Session history",
                )
            )
        elif command == "/memory" or command.startswith("/memory "):
            query = command.partition(" ")[2].strip()
            response = client.get(
                f"/sessions/{quote(session_id, safe='')}/memory",
                params={"q": query} if query else None,
            )
            response.raise_for_status()
            memories = response.json().get("memories", [])
            if memories:
                entries = []
                for item in memories:
                    status = "reviewed" if item.get("reviewed") else "unreviewed"
                    if item.get("stale"):
                        status += ", stale sources"
                    entries.append(
                        f"[{item.get('kind', 'memory')}] {item.get('content', '')}\n"
                        f"ID: {item.get('memory_id', 'unknown')} · {status}"
                    )
                _CONSOLE.print(Panel("\n\n".join(entries), title="Saved memories"))
            else:
                _CONSOLE.print(Panel("(no saved memories)", title="Saved memories"))
        elif command == "/trace":
            response = client.get(f"/sessions/{quote(session_id, safe='')}/history")
            response.raise_for_status()
            turns = response.json().get("turns", [])
            traced = [item for item in turns if item.get("trace_id") or item.get("mlflow_trace_id")]
            if traced:
                item = traced[-1]
                trace_id = item.get("trace_id") or item.get("mlflow_trace_id")
                _CONSOLE.print(f"[dim]Persisted MLflow trace ID:[/dim] {trace_id}")
            else:
                _CONSOLE.print("[yellow]Trace unavailable:[/yellow] saved turn history contains no trace ID.")
        elif command == "/context clear":
            response = client.delete(f"/sessions/{quote(session_id, safe='')}/context")
            response.raise_for_status()
            _CONSOLE.print("[green]Context selection cleared.[/green]")
        elif command.startswith("/context "):
            path = command.split(" ", 1)[1].strip()
            response = client.put(f"/sessions/{quote(session_id, safe='')}/context", json={"path": path})
            response.raise_for_status()
            _CONSOLE.print(f"[green]Context selected:[/green] {path}")
        elif command == "/reload":
            if _CONSOLE.input("Reload the saved Flex program? [y/N] ").strip().lower() == "y":
                response = client.post(f"/sessions/{quote(session_id, safe='')}/reload", params={"approved": "true"})
                response.raise_for_status()
                _CONSOLE.print("[green]Saved program reloaded.[/green]")
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
            _CONSOLE.print("[yellow]Unknown command. Use /help; submit a task without a leading slash.[/yellow]")
            return True
    except httpx.HTTPStatusError as exc:
        _print_http_error(exc)
    except httpx.HTTPError as exc:
        _CONSOLE.print(f"[red]Fleet API is unavailable:[/red] {type(exc).__name__}")
    except (OSError, ValueError, KeyError) as exc:
        _CONSOLE.print(f"[red]Command failed:[/red] {type(exc).__name__}")
    except (KeyboardInterrupt, EOFError):
        _CONSOLE.print("[dim]Command cancelled; no further changes requested.[/dim]")
    return True


def run_interactive_cli(base_url: str, session_id: str) -> None:
    """Connect to the API and stream one Session's Turns until exit."""
    validate_session_id(session_id)
    prompt = None
    if sys.stdin.isatty() and _CONSOLE.is_terminal:
        history_path = Path(".fleet/history") / f"{session_id}.txt"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        prompt = PromptSession(
            history=FileHistory(str(history_path)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=_COMMAND_COMPLETER,
        )
    _CONSOLE.print(
        Panel.fit(
            f"[bold cyan]Fleet-RLM Lite[/bold cyan] | Session: [yellow]{session_id}[/yellow]\n"
            "Enter `/help` for commands; `/exit` to quit."
        )
    )
    with httpx.Client(base_url=base_url, timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        while True:
            try:
                user_input = (
                    prompt.prompt("fleet> ") if prompt else _CONSOLE.input("\n[bold green]fleet>[/bold green] ")
                ).strip()
            except KeyboardInterrupt:
                _CONSOLE.print("[dim]Input cleared. Use /exit to quit.[/dim]")
                continue
            except EOFError:
                _CONSOLE.print()
                break
            if not user_input:
                continue
            if user_input.startswith("/"):
                if not _handle_command(client, user_input, session_id):
                    break
                continue
            try:
                terminal_event = False
                with client.stream(
                    "POST",
                    f"/sessions/{quote(session_id, safe='')}/turn",
                    json={"task": user_input, "idempotency_key": uuid.uuid4().hex},
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line[5:].lstrip())
                        except json.JSONDecodeError:
                            _CONSOLE.print("[yellow]Ignoring malformed SSE event.[/yellow]")
                            continue
                        if not isinstance(event, dict) or not isinstance(event.get("payload", {}), dict):
                            _CONSOLE.print("[yellow]Ignoring malformed SSE event.[/yellow]")
                            continue
                        terminal_event |= event.get("event") in {"turn_complete", "turn_cancelled", "error"}
                        _render_event(event)
                if not terminal_event:
                    _CONSOLE.print(
                        "[yellow]Stream ended without a final outcome. Check /history before retrying.[/yellow]"
                    )
            except httpx.HTTPStatusError as exc:
                _print_http_error(exc)
            except httpx.HTTPError as exc:
                _CONSOLE.print(f"[red]Turn stream failed:[/red] {type(exc).__name__}")
            except KeyboardInterrupt:
                _CONSOLE.print(
                    "[yellow]Turn stream closed; server cancels on disconnect. Check /history for outcome.[/yellow]"
                )


def _render_event(event: dict[str, Any]) -> None:
    event = {**event.get("payload", {}), **event}
    event_type = event.get("event")
    identity = " · ".join(
        f"{label}={event[key]}"
        for key, label in (("agent_id", "agent"), ("call_id", "call"), ("execution_id", "exec"))
        if event.get(key)
    )
    if event_type == "turn_status":
        _CONSOLE.print(f"[dim]Turn: {event.get('phase', 'running')}[/dim]")
    elif event_type == "module_start":
        _CONSOLE.print(Text(f"  → Module: {event.get('module', 'unknown')} · {identity}", style="cyan"))
    elif event_type == "lm_start":
        _CONSOLE.print(Text(f"  Model running: {event.get('model', 'unknown')} · {identity}", style="yellow"))
    elif event_type == "tool_start":
        _CONSOLE.print(Text(f"  ⚙ Tool: {event.get('tool', 'unknown')} · {event.get('source', 'dspy')} · {identity}"))
        if event.get("inputs"):
            _CONSOLE.print(
                Panel(Text(str(event["inputs"])), title="Tool arguments", subtitle=Text(identity), border_style="cyan")
            )
    elif event_type in {"module_end", "lm_end", "tool_end"}:
        status = "completed" if event.get("ok", True) else f"failed ({event.get('error_type', 'unknown')})"
        name = event.get("tool") or event.get("module") or event.get("model") or "unknown"
        usage = event.get("usage") or {}
        tokens = (
            f" · {usage.get('prompt_tokens', '?')}→{usage.get('completion_tokens', '?')} tok"
            + (f" ({usage['reasoning_tokens']} reasoning)" if usage.get("reasoning_tokens") is not None else "")
            if usage
            else ""
        )
        _CONSOLE.print(
            Text(
                f"  {name}: {status} · {event.get('duration_ms', '?')} ms{tokens} · {identity}",
                style="dim" if event.get("ok", True) else "red",
            )
        )
        if event_type == "lm_end" and event.get("reasoning"):
            _CONSOLE.print(
                Panel(
                    Text(str(event["reasoning"]), style="dim"),
                    title="Thinking",
                    subtitle=Text(identity),
                    border_style="dim",
                )
            )
        if event.get("output") not in (None, "null"):
            _CONSOLE.print(
                Panel(Text(str(event["output"])), title="Tool result", subtitle=Text(identity), border_style="blue")
            )
    elif event_type == "turn_complete":
        _CONSOLE.print(
            Panel(
                Text(str(event.get("answer", ""))),
                title=f"Answer (route={event.get('route', 'unknown')})",
                border_style="green",
            )
        )
        if event.get("learned_skill_note"):
            _CONSOLE.print(
                Panel(
                    Text(str(event["learned_skill_note"])),
                    title="Learned note · not part of answer",
                    border_style="magenta",
                )
            )
        if event.get("artifacts"):
            _CONSOLE.print(f"[yellow]Artifacts:[/yellow] {event['artifacts']}")
        if event.get("trace_id"):
            _CONSOLE.print(f"[dim]MLflow trace ID:[/dim] {event['trace_id']}")
        _CONSOLE.print("[green]Turn persisted to Session history.[/green]")
    elif event_type == "turn_cancelled":
        _CONSOLE.print(f"[yellow]Turn cancelled:[/yellow] {event.get('error', 'Cancellation confirmed by server.')}")
    elif event_type == "error":
        _CONSOLE.print(f"[bold red]Turn failed:[/bold red] {event.get('error', 'Unknown error')}")
        if event.get("trace_id"):
            _CONSOLE.print(f"MLflow trace ID: {event['trace_id']}")
    elif event_type == "repl_code":
        _CONSOLE.print(
            Panel(
                Syntax(str(event.get("code", ""))[:20000], "python", word_wrap=True),
                title=f"Python · Daytona · {event.get('phase', 'task')}",
                subtitle=Text(identity),
            )
        )
    elif event_type == "repl_output":
        text = str(event.get("stdout", "")) + str(event.get("stderr", ""))
        if event.get("value") not in (None, "null"):
            text += f"\nReturn value: {event['value']}"
        status = f"failed ({event.get('error_type') or 'Python error'})" if event.get("error") else "completed"
        _CONSOLE.print(
            Panel(
                Text(text[-20000:] or "(no output)"),
                title=f"Execution {status} · {event.get('duration_ms', '?')} ms",
                subtitle=Text(identity),
                border_style="red" if event.get("error") else "blue",
            )
        )
    elif event_type == "triage_decision":
        _CONSOLE.print(f"[cyan]Mode: {event.get('mode')} · Route: {event.get('route')}[/cyan]")
        if event.get("summary"):
            _CONSOLE.print(
                Panel(
                    Text(str(event["summary"])), title="Decision summary · not private reasoning", border_style="cyan"
                )
            )
    elif event_type == "budget_update":
        calls = event.get("lm_calls")
        maximum = event.get("max_llm_calls")
        if isinstance(calls, int) and isinstance(maximum, int) and calls >= maximum:
            _CONSOLE.print(f"[yellow]Model-call budget full; no calls remain:[/yellow] {calls}/{maximum}")
        else:
            _CONSOLE.print(f"[dim]LM calls: {calls}/{maximum}[/dim]")
    elif event_type in {
        "agent_spawn",
        "agent_result",
        "sandbox_provisioning",
        "sandbox_ready",
        "sandbox_deleting",
        "sandbox_deleted",
        "sandbox_cleanup_failed",
        "workspace_cleanup_failed",
        "cancellation_requested",
    }:
        _CONSOLE.print(
            Panel(
                Text(
                    f"Agent {event.get('agent_id')} ← {event.get('parent_agent_id') or 'root'}\n"
                    f"Sandbox: {event.get('sandbox_id') or 'pending'}"
                    + (f"\nArtifacts: {event['artifacts']}" if event.get("artifacts") else "")
                ),
                title=str(event_type),
                border_style="cyan",
            )
        )


def evaluation_statuses() -> tuple[str, ...]:
    from fleet_rlm.evaluation import REVIEW_STATUSES

    return REVIEW_STATUSES


def _eval_command(args: argparse.Namespace, settings: Any) -> None:
    """Build the eval dataset, label a trace, or run the synthetic cases against a live server."""
    import os

    import mlflow

    from fleet_rlm import evaluation

    target = settings.mlflow_target()
    mlflow.set_tracking_uri(target.tracking_uri)
    if target.mode == "databricks":
        os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = settings.mlflow_tracing_sql_warehouse_id
    if args.eval_command == "label":
        evaluation.label_trace(
            args.trace_id,
            review_status=args.status,
            source_group=args.group,
            expected_answer=args.answer,
            expected_values=args.value,
            route=args.route,
            reviewer=args.reviewer,
        )
        _CONSOLE.print(f"Labeled {args.trace_id} as {args.status}")
        return
    if not target.eval_experiment_name:
        raise SystemExit("set FLEET_MLFLOW_EVAL_EXPERIMENT_NAME to the evaluation experiment")
    experiment = mlflow.set_experiment(target.eval_experiment_name)
    cases = evaluation.synthetic_cases(seed=args.seed)
    if args.eval_command == "dataset":
        name = args.name or (
            f"{settings.mlflow_trace_catalog}.{settings.mlflow_trace_schema}.fleet_rlm_eval_cases"
            if target.mode == "databricks"
            else "fleet_rlm_eval_cases"
        )
        from fleet_rlm import benchmarks

        if args.long_context:
            cases += benchmarks.long_context_cases()
        if args.oolong or args.longcot:
            with httpx.Client(follow_redirects=True) as hf:
                if args.oolong:
                    cases += benchmarks.oolong_cases(hf, limit=args.oolong)
                if args.longcot:
                    cases += benchmarks.longcot_cases(hf, per_domain=args.longcot)
        dataset = evaluation.build_dataset(name, experiment.experiment_id, cases)
        _CONSOLE.print(f"Dataset {dataset.name}: {len(cases)} cases")
        return
    for name, value in settings.judge_environment().items():
        os.environ.setdefault(name, value)
    result = evaluation.evaluate_cases(
        cases[: args.limit] if args.limit else cases,
        base_url=args.url,
        judge_model=settings.fleet_judge_model,
    )
    _CONSOLE.print(result.metrics)


def main(argv: list[str] | None = None) -> None:
    """Run the backend or the interactive Rich client."""
    try:
        settings = get_settings()
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {str(error['msg']).removeprefix('Value error, ')}"
            for error in exc.errors(include_url=False, include_input=False)
        )
        sys.stderr.write(f"fleet: error: invalid configuration: {details}\n")
        raise SystemExit(2) from None
    parser = argparse.ArgumentParser(prog="fleet", description="Fleet-RLM Lite server and terminal client")
    commands = parser.add_subparsers(dest="command")

    serve = commands.add_parser("serve", help="run the FastAPI SSE server")
    serve.add_argument("--host", default=settings.host)
    serve.add_argument("--port", type=int, default=settings.port)

    cli = commands.add_parser(
        "cli",
        help="start the API server (and local MLflow) if needed, then open the interactive terminal client",
    )
    cli.add_argument("--url", default=None, help="connect to this server only; do not start anything")
    cli.add_argument("--session", default=f"sess_{uuid.uuid4().hex[:8]}")
    target = settings.mlflow_target()
    cli.add_argument(
        "--mlflow",
        choices=("local", "env"),
        default="local" if target.mode == "local" else "env",
        help="local: run a local MLflow server and trace to it; env: use the configured tracking URI "
        "(default follows FLEET_MLFLOW_MODE)",
    )
    cli.add_argument("--mlflow-port", type=int, default=5001)
    cli.add_argument("--mlflow-experiment", default=target.experiment_name)
    cli.add_argument("--startup-timeout", type=float, default=180.0, help="seconds to wait for each service")

    evaluate = commands.add_parser("eval", help="evaluation datasets, gold labels, and runs")
    eval_commands = evaluate.add_subparsers(dest="eval_command", required=True)
    dataset = eval_commands.add_parser("dataset", help="create the synthetic evaluation dataset")
    dataset.add_argument(
        "--name", default="", help="dataset name (databricks mode: catalog.schema.table from the trace settings)"
    )
    dataset.add_argument("--seed", type=int, default=7)
    dataset.add_argument("--long-context", action="store_true", help="add synthetic 20K-200K character log cases")
    dataset.add_argument("--oolong", type=int, default=0, metavar="N", help="add N Oolong-synth benchmark cases")
    dataset.add_argument("--longcot", type=int, default=0, metavar="N", help="add N LongCoT cases per domain")
    run = eval_commands.add_parser("run", help="run synthetic cases against a live server and score them")
    run.add_argument("--url", default=f"http://{settings.host}:{settings.port}")
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--limit", type=int, default=0, help="only run the first N cases")
    label = eval_commands.add_parser("label", help="write gold labels onto a trace")
    label.add_argument("trace_id")
    label.add_argument("--status", required=True, choices=evaluation_statuses())
    label.add_argument("--group", required=True, help="source_group used for grouped train/dev/holdout splits")
    label.add_argument("--answer")
    label.add_argument("--value", action="append", help="expected value that must appear in the answer (repeatable)")
    label.add_argument("--route", choices=[route.value for route in Route])
    label.add_argument("--reviewer", default="human")

    args = parser.parse_args(argv)
    if args.command == "eval":
        _eval_command(args, settings)
        return
    if args.command == "serve":
        if not settings.allow_non_loopback_bind and not _is_loopback(args.host):
            parser.error("non-loopback binding requires FLEET_ALLOW_NON_LOOPBACK_BIND=true")
        from fleet_rlm.server import create_app

        bound = settings.with_explicit_overrides(host=args.host, port=args.port)
        uvicorn.run(create_app(settings=bound), host=bound.host, port=bound.port, reload=False)
        return

    session_id = getattr(args, "session", f"sess_{uuid.uuid4().hex[:8]}")
    url = getattr(args, "url", None)
    if not url and not settings.allow_non_loopback_bind and not _is_loopback(settings.host):
        parser.error("non-loopback binding requires FLEET_ALLOW_NON_LOOPBACK_BIND=true")
    plan = launcher.plan_launch(
        url=url,
        host=settings.host,
        port=settings.port,
        mlflow_mode=getattr(args, "mlflow", "local"),
        mlflow_port=getattr(args, "mlflow_port", 5001),
        mlflow_experiment=getattr(args, "mlflow_experiment", "fleet-rlm-dev"),
    )
    try:
        with launcher.running_services(
            plan,
            host=settings.host,
            port=settings.port,
            mlflow_port=getattr(args, "mlflow_port", 5001),
            startup_timeout=getattr(args, "startup_timeout", 180.0),
            log=lambda message: _CONSOLE.print(f"[dim]{message}[/dim]"),
        ):
            run_interactive_cli(base_url=plan.api_url, session_id=session_id)
    except launcher.LaunchError as exc:
        _CONSOLE.print(f"[bold red]Could not start services:[/bold red] {exc}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
