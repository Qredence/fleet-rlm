"""Render Turn stream events in the terminal (Phase 4 replaces this with the compact live view)."""

from __future__ import annotations

from typing import Any

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

CONSOLE = Console()


def print_http_error(error: httpx.HTTPStatusError) -> None:
    CONSOLE.print(f"[bold red]HTTP {error.response.status_code}:[/bold red] Request was not completed.")


def render_event(event: dict[str, Any]) -> None:
    event = {**event.get("payload", {}), **event}
    event_type = event.get("event")
    identity = " · ".join(
        f"{label}={event[key]}"
        for key, label in (("agent_id", "agent"), ("call_id", "call"), ("execution_id", "exec"))
        if event.get(key)
    )
    if event_type == "turn_status":
        CONSOLE.print(f"[dim]Turn: {event.get('phase', 'running')}[/dim]")
    elif event_type == "module_start":
        CONSOLE.print(Text(f"  → Module: {event.get('module', 'unknown')} · {identity}", style="cyan"))
    elif event_type == "lm_start":
        CONSOLE.print(Text(f"  Model running: {event.get('model', 'unknown')} · {identity}", style="yellow"))
    elif event_type == "tool_start":
        CONSOLE.print(Text(f"  ⚙ Tool: {event.get('tool', 'unknown')} · {event.get('source', 'dspy')} · {identity}"))
        if event.get("inputs"):
            CONSOLE.print(
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
        CONSOLE.print(
            Text(
                f"  {name}: {status} · {event.get('duration_ms', '?')} ms{tokens} · {identity}",
                style="dim" if event.get("ok", True) else "red",
            )
        )
        if event_type == "lm_end" and event.get("reasoning"):
            CONSOLE.print(
                Panel(
                    Text(str(event["reasoning"]), style="dim"),
                    title="Thinking",
                    subtitle=Text(identity),
                    border_style="dim",
                )
            )
        if event.get("output") not in (None, "null"):
            CONSOLE.print(
                Panel(Text(str(event["output"])), title="Tool result", subtitle=Text(identity), border_style="blue")
            )
    elif event_type == "turn_complete":
        CONSOLE.print(
            Panel(
                Text(str(event.get("answer", ""))),
                title=f"Answer (route={event.get('route', 'unknown')})",
                border_style="green",
            )
        )
        if event.get("learned_skill_note"):
            CONSOLE.print(
                Panel(
                    Text(str(event["learned_skill_note"])),
                    title="Learned note · not part of answer",
                    border_style="magenta",
                )
            )
        if event.get("artifacts"):
            CONSOLE.print(f"[yellow]Artifacts:[/yellow] {event['artifacts']}")
        if event.get("trace_id"):
            CONSOLE.print(f"[dim]MLflow trace ID:[/dim] {event['trace_id']}")
        CONSOLE.print("[green]Turn persisted to Session history.[/green]")
    elif event_type == "turn_cancelled":
        CONSOLE.print(f"[yellow]Turn cancelled:[/yellow] {event.get('error', 'Cancellation confirmed by server.')}")
    elif event_type == "error":
        CONSOLE.print(f"[bold red]Turn failed:[/bold red] {event.get('error', 'Unknown error')}")
        if event.get("trace_id"):
            CONSOLE.print(f"MLflow trace ID: {event['trace_id']}")
    elif event_type == "repl_code":
        CONSOLE.print(
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
        CONSOLE.print(
            Panel(
                Text(text[-20000:] or "(no output)"),
                title=f"Execution {status} · {event.get('duration_ms', '?')} ms",
                subtitle=Text(identity),
                border_style="red" if event.get("error") else "blue",
            )
        )
    elif event_type == "triage_decision":
        CONSOLE.print(f"[cyan]Mode: {event.get('mode')} · Route: {event.get('route')}[/cyan]")
        if event.get("summary"):
            CONSOLE.print(
                Panel(
                    Text(str(event["summary"])), title="Decision summary · not private reasoning", border_style="cyan"
                )
            )
    elif event_type == "budget_update":
        calls = event.get("lm_calls")
        maximum = event.get("max_llm_calls")
        if isinstance(calls, int) and isinstance(maximum, int) and calls >= maximum:
            CONSOLE.print(f"[yellow]Model-call budget full; no calls remain:[/yellow] {calls}/{maximum}")
        else:
            CONSOLE.print(f"[dim]LM calls: {calls}/{maximum}[/dim]")
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
        CONSOLE.print(
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
