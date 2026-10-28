"""Render Turn stream events and manage interactive collapsible REPL, tool, and thinking blocks."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from rich.console import Console, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

CONSOLE = Console()
_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_OMITTED_PAYLOADS = frozenset({None, "", "null", "{}", "[predictor payload omitted]", '"[predictor payload omitted]"'})
TURN_PERSISTED = "Turn persisted to Session history."


def _extract_http_detail(response: httpx.Response) -> str:
    """Return a bounded single-line detail message from an HTTP error response when available."""
    try:
        payload = response.json()
    except Exception:
        return "Request was not completed."
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return " ".join(detail.split())[:200]
        if isinstance(detail, list) and detail:
            first = detail[0]
            if isinstance(first, dict) and first.get("msg"):
                loc = ".".join(str(part) for part in first.get("loc", []) if part != "body")
                prefix = f"{loc}: " if loc else ""
                return " ".join(f"{prefix}{first['msg']}".split())[:200]
    return "Request was not completed."


def print_http_error(error: httpx.HTTPStatusError, *, console: Console | None = None) -> None:
    """Print a one-line summary of a failed HTTP request with its sanitized detail when present."""
    detail = _extract_http_detail(error.response)
    (console or CONSOLE).print(f"[bold red]HTTP {error.response.status_code}:[/bold red] {detail}")


def _is_omitted_payload(value: Any) -> bool:
    """Return True when a tool input or output preview carries no user-meaningful evidence."""
    if value in _OMITTED_PAYLOADS:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        return stripped in _OMITTED_PAYLOADS or stripped == '{"args": [], "kwargs": {}}'
    return False


def _format_tool_payload(value: Any) -> str:
    """Format JSON tool previews cleanly while preserving single-line strings and primitives."""
    text = str(value)
    try:
        parsed = json.loads(text)
    except Exception:
        return text
    if isinstance(parsed, str):
        return parsed
    if isinstance(parsed, (dict, list)):
        if isinstance(parsed, dict) and set(parsed.keys()) <= {"args", "kwargs"}:
            args = parsed.get("args") or []
            kwargs = parsed.get("kwargs") or {}
            if not kwargs and len(args) == 1:
                return json.dumps(args[0], indent=2, ensure_ascii=False)
            if not args and kwargs:
                return json.dumps(kwargs, indent=2, ensure_ascii=False)
        formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
        return formatted if len(formatted) <= 4000 else text
    return text


def _first_meaningful_line(text: str, limit: int = 64) -> str:
    """Return a short single-line preview of code, reasoning, or output."""
    for raw in text.splitlines():
        line = " ".join(raw.strip().split())
        if line and not line.startswith("#"):
            return line[:limit] + ("…" if len(line) > limit else "")
    fallback = " ".join(text.strip().split())
    return fallback[:limit] + ("…" if len(fallback) > limit else "")


def confidence_bar_text(confidence: float, width: int = 10) -> Text:
    """Build a compact block progress bar with percentage value for a `[0.0, 1.0]` confidence score."""
    clamped = max(0.0, min(1.0, float(confidence)))
    filled = round(clamped * width)
    empty = max(0, width - filled)
    fill_color = "#34d399" if clamped >= 0.8 else ("#fbbf24" if clamped >= 0.5 else "#f87171")
    bar = Text()
    bar.append("█" * filled, style=fill_color)
    bar.append("░" * empty, style="#3f3f46")
    bar.append(f" {clamped:.0%}", style="#a1a1aa")
    return bar


def _identity_text(event: dict[str, Any]) -> str:
    """Format correlation identifiers present on a raw stream event."""
    return " · ".join(
        f"{label}={event[key]}"
        for key, label in (("agent_id", "agent"), ("call_id", "call"), ("execution_id", "exec"))
        if event.get(key)
    )


@dataclass(slots=True)
class CollapsibleItem:
    """One interactive REPL cell, Thinking block, or Tool invocation that can expand and collapse."""

    item_id: str
    kind: str  # "repl" | "thinking" | "tool"
    index: int
    agent_label: str = "root"
    title: str = ""
    summary: str = ""
    status: str = "running"
    duration_ms: int | None = None
    expanded: bool = False
    user_toggled: bool = False
    phase: str = "task"
    namespace: str = ""
    code: str = ""
    stdout: str = ""
    stderr: str = ""
    value: str | None = None
    error: bool = False
    error_type: str | None = None
    reasoning: str = ""
    tool_inputs: str | None = None
    tool_output: str | None = None
    usage_text: str = ""

    def toggle(self) -> bool:
        """Flip the expanded state, mark the item as user-toggled, and return the new state."""
        self.expanded = not self.expanded
        self.user_toggled = True
        return self.expanded


@dataclass(slots=True)
class TurnTracker:
    """Stateful collector and interactive renderer for a single Turn's live SSE stream."""

    turn_id: str | None = None
    phase: str = "preparation"
    mode: str | None = None
    route: str | None = None
    route_summary: str | None = None
    route_override: str | None = None
    route_confidence: float | None = None
    preloaded_skills: list[str] | None = None
    sandbox_status: str | None = None
    active_model: str | None = None
    lm_calls: int = 0
    max_llm_calls: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    sandbox_errors: int = 0
    submit_rejections: int = 0
    cancelling: bool = False
    # Advisory label derived by the server from events; it never changes the Turn's behavior.
    status_label: str | None = None
    status_scope: str | None = None
    status_checkpoint: int | None = None
    items: list[CollapsibleItem] = field(default_factory=list)
    notices: list[RenderableType] = field(default_factory=list)
    terminal_event: dict[str, Any] | None = None
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    # The server's recorded wall duration, once the terminal event carries it. Displaying it makes
    # the client's elapsed time equal the stored Turn instead of a locally measured approximation.
    server_duration_seconds: float | None = None
    _agent_aliases: dict[str, str] = field(default_factory=dict)
    _agent_depths: dict[str, int] = field(default_factory=dict)
    _active_children: set[str] = field(default_factory=set)
    _child_counter: int = 0
    _repl_by_exec: dict[str, CollapsibleItem] = field(default_factory=dict)
    _tool_by_call: dict[str, CollapsibleItem] = field(default_factory=dict)
    _repl_counter: int = 0
    _think_counter: int = 0
    _tool_counter: int = 0
    _tick: int = 0

    def _agent_info(self, event: dict[str, Any]) -> tuple[str, int]:
        """Resolve a human-readable label ('root', 'child#1 [a1b2]') and indentation depth for an agent."""
        agent_id = event.get("agent_id")
        if not agent_id:
            return "root", 0
        if agent_id in self._agent_aliases:
            return self._agent_aliases[agent_id], self._agent_depths.get(agent_id, 0)
        parent_id = event.get("parent_agent_id")
        if not parent_id and not self._agent_aliases:
            self._agent_aliases[agent_id] = "root"
            self._agent_depths[agent_id] = 0
            return "root", 0
        self._child_counter += 1
        parent_depth = self._agent_depths.get(parent_id, 0) if parent_id else 0
        raw_depth = event.get("depth")
        depth = raw_depth if isinstance(raw_depth, int) and not isinstance(raw_depth, bool) else (parent_depth + 1)
        label = f"child#{self._child_counter} [{str(agent_id)[:6]}]"
        self._agent_aliases[agent_id] = label
        self._agent_depths[agent_id] = depth
        return label, depth

    def ingest(self, raw_event: dict[str, Any]) -> None:
        """Process one SSE event and update collapsible items, counters, and Turn state."""
        event = {**raw_event, **raw_event.get("payload", {})}
        event_type = event.get("event")
        if event.get("turn_id") and not self.turn_id:
            self.turn_id = str(event["turn_id"])
        # The server attaches the derived label to each event; the client renders it, never derives control.
        if "status" in event:
            raw_status = event.get("status")
            self.status_label = str(raw_status)[:64] if isinstance(raw_status, str) and raw_status else None
        if event_type in {"progress", "status"}:
            scope = event.get("scope")
            self.status_scope = scope[:64] if isinstance(scope, str) and scope else None
            checkpoint = event.get("checkpoint")
            self.status_checkpoint = checkpoint if type(checkpoint) is int and checkpoint >= 0 else None
        elif event_type == "fenced":
            self.notices.append(Text("Publication fenced; the late result was not delivered.", style="yellow"))
        elif event_type == "done":
            # The label is absent after `done`: the Turn has acknowledged its outcome.
            self.status_label = None
            self.status_scope = None
            self.status_checkpoint = None
            return
        if event_type == "orphan_deleted":
            self.notices.append(Text(f"✓ Cleaned up orphaned sandbox {event.get('sandbox_id') or ''}", style="dim"))
            return
        agent_label, depth = self._agent_info(event)

        if event_type == "turn_status":
            self.phase = str(event.get("phase") or "running")
        elif event_type == "triage_decision":
            self.mode = str(event.get("mode") or "")
            self.route = str(event.get("route") or "")
            self.route_summary = str(event["summary"]) if event.get("summary") else None
            raw_evidence = event.get("evidence")
            evidence = raw_evidence if isinstance(raw_evidence, dict) else {}
            self.route_override = evidence.get("override_reason")
            prob = evidence.get("probability")
            conf = prob if prob is not None else evidence.get("confidence")
            self.route_confidence = (
                float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None
            )
        elif event_type == "skill_selection":
            self.preloaded_skills = list(event.get("selected") or [])
        elif event_type == "lm_start":
            self.active_model = str(event.get("model") or "unknown")
        elif event_type == "lm_end":
            self.active_model = None
            raw_usage = event.get("usage")
            usage = raw_usage if isinstance(raw_usage, dict) else {}
            p_tok = usage.get("prompt_tokens")
            c_tok = usage.get("completion_tokens")
            r_tok = usage.get("reasoning_tokens")
            if isinstance(p_tok, (int, float)):
                self.prompt_tokens += int(p_tok)
            if isinstance(c_tok, (int, float)):
                self.completion_tokens += int(c_tok)
            if isinstance(r_tok, (int, float)):
                self.reasoning_tokens += int(r_tok)
            if event.get("reasoning"):
                self._think_counter += 1
                reasoning_text = str(event["reasoning"])
                usage_parts = []
                if isinstance(r_tok, (int, float)):
                    usage_parts.append(f"{int(r_tok)} reasoning tok")
                elif isinstance(c_tok, (int, float)):
                    usage_parts.append(f"{int(c_tok)} tok")
                item = CollapsibleItem(
                    item_id=f"think-{self._think_counter}",
                    kind="thinking",
                    index=self._think_counter,
                    agent_label=agent_label,
                    title="Thinking",
                    summary=_first_meaningful_line(reasoning_text),
                    status="completed" if event.get("ok", True) else "failed",
                    duration_ms=event.get("duration_ms") if isinstance(event.get("duration_ms"), int) else None,
                    expanded=False,
                    reasoning=reasoning_text,
                    usage_text=", ".join(usage_parts),
                )
                self.items.append(item)
        elif event_type == "tool_start":
            tool_name = str(event.get("tool") or "unknown")
            inputs = event.get("inputs")
            if _is_omitted_payload(inputs) and (
                tool_name.startswith("__dspy_") or tool_name in {"llm_query", "llm_query_batched"}
            ):
                return
            self._tool_counter += 1
            call_id = str(event.get("call_id") or f"tool-{self._tool_counter}")
            summary = _first_meaningful_line(_format_tool_payload(inputs)) if not _is_omitted_payload(inputs) else ""
            item = CollapsibleItem(
                item_id=f"tool-{self._tool_counter}",
                kind="tool",
                index=self._tool_counter,
                agent_label=agent_label,
                title=tool_name,
                summary=summary,
                status="running",
                expanded=False,
                tool_inputs=str(inputs) if inputs is not None else None,
            )
            self._tool_by_call[call_id] = item
            self.items.append(item)
        elif event_type == "tool_end":
            call_id = str(event.get("call_id") or "")
            item = self._tool_by_call.get(call_id)
            output = event.get("output")
            if item is None:
                tool_name = str(event.get("tool") or "unknown")
                if _is_omitted_payload(output) and (
                    tool_name.startswith("__dspy_") or tool_name in {"llm_query", "llm_query_batched"}
                ):
                    return
                self._tool_counter += 1
                item = CollapsibleItem(
                    item_id=f"tool-{self._tool_counter}",
                    kind="tool",
                    index=self._tool_counter,
                    agent_label=agent_label,
                    title=tool_name,
                    expanded=False,
                )
                self.items.append(item)
            ok = bool(event.get("ok", True))
            item.status = "completed" if ok else "failed"
            item.error = not ok
            item.error_type = str(event["error_type"]) if event.get("error_type") else None
            if isinstance(event.get("duration_ms"), int):
                item.duration_ms = event["duration_ms"]
            if output not in (None, "null"):
                item.tool_output = str(output)
                if not item.summary and not _is_omitted_payload(output):
                    item.summary = _first_meaningful_line(_format_tool_payload(output))
            if item.error and not item.user_toggled:
                item.expanded = True
        elif event_type == "repl_code":
            phase = str(event.get("phase") or "task")
            exec_id = str(event.get("execution_id") or f"exec-{len(self.items)}")
            code = str(event.get("code") or "")
            if phase == "task":
                self._repl_counter += 1
                idx = self._repl_counter
            else:
                idx = 0
            seq = len(self.items) + 1
            item = CollapsibleItem(
                item_id=f"repl-{seq}-{idx}-{exec_id[:6]}",
                kind="repl",
                index=idx,
                agent_label=agent_label,
                title="Python" if phase == "task" else "Bootstrap",
                summary=_first_meaningful_line(code) if phase == "task" else "DSPy namespace setup",
                status="running",
                # Task code expands while running so the user sees live execution; bootstrap stays collapsed.
                expanded=(phase == "task"),
                phase=phase,
                namespace=str(event.get("namespace") or ""),
                code=code,
            )
            self._repl_by_exec[exec_id] = item
            self.items.append(item)
        elif event_type == "repl_output":
            exec_id = str(event.get("execution_id") or "")
            phase = str(event.get("phase") or "task")
            item = self._repl_by_exec.get(exec_id)
            if item is None:
                if phase == "task":
                    self._repl_counter += 1
                    idx = self._repl_counter
                else:
                    idx = 0
                seq = len(self.items) + 1
                item = CollapsibleItem(
                    item_id=f"repl-{seq}-{idx}-{exec_id[:6] or seq}",
                    kind="repl",
                    index=idx,
                    agent_label=agent_label,
                    title="Python" if phase == "task" else "Bootstrap",
                    phase=phase,
                    namespace=str(event.get("namespace") or ""),
                )
                self.items.append(item)
            has_error = bool(event.get("error"))
            item.error = has_error
            item.error_type = str(event.get("error_type") or "Python error") if has_error else None
            item.status = "failed" if has_error else "completed"
            if isinstance(event.get("duration_ms"), int):
                item.duration_ms = event["duration_ms"]
            item.stdout = str(event.get("stdout") or "")
            item.stderr = str(event.get("stderr") or "")
            val = event.get("value")
            item.value = str(val) if val not in (None, "null") else None
            if has_error:
                if event.get("outcome") in (None, "remote_code_error"):
                    self.sandbox_errors += 1
                if not item.user_toggled:
                    item.expanded = True
            elif not item.user_toggled:
                # Auto-collapse finished clean cells to keep the live view compact; click or Ctrl+O re-expands.
                item.expanded = False
        elif event_type == "budget_update":
            if isinstance(event.get("lm_calls"), int):
                self.lm_calls = event["lm_calls"]
            if isinstance(event.get("max_llm_calls"), int):
                self.max_llm_calls = event["max_llm_calls"]
            if self.max_llm_calls and self.lm_calls >= self.max_llm_calls:
                self.notices.append(
                    Text(
                        f"Shared budget full; no calls remain: {self.lm_calls}/{self.max_llm_calls} units",
                        style="yellow",
                    )
                )
        elif event_type == "ungrounded_submit":
            if event.get("rejected"):
                self.submit_rejections += 1
            verdict = "rejected; the agent must compute them" if event.get("rejected") else "accepted after retries"
            self.notices.append(
                Text(
                    f"SUBMIT with figures not seen in sandbox output ({verdict}): {event.get('figures')}",
                    style="yellow",
                )
            )
        elif event_type == "learned_note_warning":
            self.notices.append(
                Text(f"Unreviewed learned note: figures need verification: {event.get('figures')}", style="yellow")
            )
        elif event_type == "agent_spawn":
            agent_id = str(event.get("agent_id") or "")
            if agent_id:
                self._active_children.add(agent_id)
            inputs = event.get("input_paths") or []
            inputs_note = f" · inputs: {', '.join(str(p) for p in inputs[:4])}" if inputs else ""
            self.notices.append(
                Padding(
                    Text(f"↳ Spawned {agent_label} (depth={depth}{inputs_note})", style="cyan"),
                    (0, 0, 0, max(0, depth - 1) * 2),
                )
            )
        elif event_type == "agent_result":
            agent_id = str(event.get("agent_id") or "")
            self._active_children.discard(agent_id)
            artifacts = event.get("artifacts") or []
            paths = [row.get("path", str(row)) if isinstance(row, dict) else str(row) for row in artifacts]
            art_note = f" · promoted: {', '.join(paths)}" if paths else " · no new artifacts"
            self.notices.append(
                Padding(
                    Text(f"✓ Completed {agent_label}{art_note}", style="green"),
                    (0, 0, 0, max(0, depth - 1) * 2),
                )
            )
        elif event_type == "sandbox_provisioning":
            self.sandbox_status = f"Provisioning sandbox ({agent_label})…"
        elif event_type == "sandbox_resuming":
            state = event.get("state") or "stopped"
            self.sandbox_status = f"Resuming {state} sandbox ({agent_label})…"
            self.notices.append(Text(f"↻ Resuming {state} Daytona sandbox ({agent_label})…", style="yellow"))
        elif event_type == "sandbox_ready":
            self.sandbox_status = None
        elif event_type == "sandbox_deleting":
            self.sandbox_status = f"Cleaning up sandbox ({agent_label})…"
        elif event_type == "sandbox_deleted":
            self.sandbox_status = None
        elif event_type in {"sandbox_cleanup_failed", "workspace_cleanup_failed"}:
            self.notices.append(
                Text(f"{event_type}: sandbox={event.get('sandbox_id') or 'unknown'} ({agent_label})", style="bold red")
            )
        elif event_type == "cancellation_requested":
            self.cancelling = True
            self.notices.append(Text("Cancellation requested; stopping remote execution…", style="yellow"))
        elif event_type in {"turn_complete", "turn_cancelled", "error"}:
            for name in ("lm_calls", "sandbox_errors", "submit_rejections"):
                value = event.get(name)
                if type(value) is int and value >= 0:
                    setattr(self, name, value)
            self.terminal_event = event
            self.finished_at = time.monotonic()
            # The terminal event carries the Turn's recorded route and wall duration; prefer them
            # over what the client observed so the displayed values match the stored Turn.
            if event_type == "turn_complete" and event.get("route"):
                self.route = str(event["route"])
            duration = event.get("duration_seconds")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
                self.server_duration_seconds = float(duration)
            self.sandbox_status = None
            self.active_model = None
            end_status = (
                "cancelled"
                if event_type == "turn_cancelled"
                else ("interrupted" if event_type == "error" else "completed")
            )
            self.finalize_running_items(end_status)

    def finalize_running_items(self, status: str = "interrupted") -> None:
        """Transition any still-running collapsible items to `status` when the Turn ends."""
        for item in self.items:
            if item.status == "running":
                item.status = status
                if status != "completed" and not item.user_toggled:
                    item.expanded = False

    def toggle_latest_repl(self) -> bool:
        """Toggle the most recent task REPL cell (or fallback to any collapsible item)."""
        for item in reversed(self.items):
            if item.kind == "repl" and item.phase == "task":
                item.toggle()
                return True
        for item in reversed(self.items):
            if item.kind == "repl":
                item.toggle()
                return True
        if self.items:
            self.items[-1].toggle()
            return True
        return False

    def toggle_repl_index(self, index: int) -> bool:
        """Toggle the REPL cell matching the 1-based index (or 0 for bootstrap)."""
        for item in self.items:
            if item.kind == "repl" and item.index == index:
                item.toggle()
                return True
        return False

    def toggle_thinking(self) -> bool:
        """Toggle all Thinking blocks open or closed."""
        thinking = [item for item in self.items if item.kind == "thinking"]
        if not thinking:
            return False
        target = any(not item.expanded for item in thinking)
        for item in thinking:
            item.expanded = target
            item.user_toggled = True
        return True

    def toggle_all(self) -> bool:
        """Expand all collapsible items if any are collapsed; otherwise collapse all."""
        if not self.items:
            return False
        target = any(not item.expanded for item in self.items)
        for item in self.items:
            item.expanded = target
            item.user_toggled = True
        return True

    def advance_spinner(self) -> bool:
        """Advance the live spinner frame while the Turn is running; return True when active."""
        if self.terminal_event is not None:
            return False
        self._tick += 1
        return True

    def elapsed_seconds(self) -> float:
        """Return the Turn's elapsed wall time, preferring the duration the server recorded."""
        if self.server_duration_seconds is not None:
            return self.server_duration_seconds
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(0.0, end - self.started_at)

    def _status_text(self) -> str:
        """Render the advisory label with neutral evidence: no percentage and no completion claim."""
        parts = [self.status_label or ""]
        if self.status_scope:
            parts.append(self.status_scope)
        if self.status_checkpoint is not None:
            parts.append(f"checkpoint {self.status_checkpoint}")
        return " · ".join(part for part in parts if part)

    def status_line(self) -> Text:
        """Build the live status footer (or final Turn receipt summary when finished)."""
        elapsed = f"{self.elapsed_seconds():.1f}s"
        budget = (
            f"{self.lm_calls}/{self.max_llm_calls} budget units"
            if self.max_llm_calls
            else (f"{self.lm_calls} budget units" if self.lm_calls else "")
        )
        total_tok = self.prompt_tokens + self.completion_tokens
        tokens = f"{total_tok:,} tok" if total_tok else ""
        repl_count = sum(1 for item in self.items if item.kind == "repl" and item.phase == "task")
        repl_info = f"{repl_count} REPL" if repl_count else ""
        metrics = " · ".join(part for part in (budget, tokens, repl_info, elapsed) if part)

        if self.terminal_event is not None:
            # The recorded route joins the answer and elapsed time the terminal event reported.
            route = self.route or self.terminal_event.get("route")
            route_info = f"route={route}" if route else ""
            summary = " · ".join(part for part in (route_info, metrics or elapsed) if part)
            line = Text("── Turn summary: ", style="dim")
            line.append(summary, style="dim")
            if self.items:
                line.append(" · ctrl+o inspect cells", style="dim italic")
            return line

        spinner = _SPINNER_FRAMES[self._tick % len(_SPINNER_FRAMES)]
        running_repl = next(
            (item for item in reversed(self.items) if item.kind == "repl" and item.status == "running"), None
        )

        if self.cancelling:
            activity = "Cancelling turn and stopping sandboxes…"
            style = "bold yellow"
        elif self.sandbox_status:
            activity = f"{spinner} {self.sandbox_status}"
            style = "yellow"
        elif self.status_label:
            # Advisory status derived from the server's event stream; it never controls the Turn.
            activity = f"{spinner} {self._status_text()}"
            style = "yellow" if self.status_label in {"stalled", "fenced", "interrupted"} else "cyan"
        elif running_repl is not None:
            activity = f"{spinner} Running Python #{running_repl.index} in Daytona ({running_repl.agent_label})…"
            style = "cyan"
        elif self._active_children:
            count = len(self._active_children)
            activity = f"{spinner} Running {count} child agent{'s' if count != 1 else ''}…"
            style = "cyan"
        elif self.active_model:
            short_model = self.active_model.removeprefix("openai/")
            activity = f"{spinner} Thinking ({short_model})…"
            style = "yellow"
        elif self.route is None:
            activity = f"{spinner} Preparing & routing turn ({self.phase})…"
            style = "cyan"
        else:
            activity = f"{spinner} Running {self.route} pipeline…"
            style = "cyan"

        line = Text(activity, style=style)
        if metrics:
            line.append(f"  │  {metrics}", style="dim")
        if self.items:
            line.append("  │  click ▸/▾ or ctrl+o toggle REPL", style="dim italic")
        return line


def _terminal_renderables(event: dict[str, Any]) -> list[RenderableType]:
    """Build the final Answer, Learned note, error, or cancellation renderables for a finished Turn."""
    event_type = event.get("event")
    out: list[RenderableType] = []
    if event_type == "turn_complete":
        out.append(
            Panel(
                Text(str(event.get("answer", ""))),
                title=f"Answer (route={event.get('route', 'unknown')})",
                border_style="green",
            )
        )
        if event.get("learned_skill_note"):
            out.append(
                Panel(
                    Text(str(event["learned_skill_note"])),
                    title="Learned note · not part of answer",
                    border_style="magenta",
                )
            )
        if event.get("artifacts"):
            out.append(Text.from_markup(f"[yellow]Artifacts:[/yellow] {event['artifacts']}"))
        if event.get("trace_id"):
            out.append(Text.from_markup(f"[dim]MLflow trace ID:[/dim] {event['trace_id']}"))
        out.append(Text.from_markup(f"[green]{TURN_PERSISTED}[/green]"))
    elif event_type == "turn_cancelled":
        out.append(
            Text.from_markup(
                f"[yellow]Turn cancelled:[/yellow] {event.get('error', 'Cancellation confirmed by server.')}"
            )
        )
    elif event_type == "error":
        out.append(Text.from_markup(f"[bold red]Turn failed:[/bold red] {event.get('error', 'Unknown error')}"))
        if event.get("trace_id"):
            out.append(Text(f"MLflow trace ID: {event['trace_id']}"))
    return out


def render_event(event: dict[str, Any]) -> None:
    """Print one Turn stream event to the console, merging its payload into the event."""
    event = {**event, **event.get("payload", {})}
    event_type = event.get("event")
    identity = _identity_text(event)
    if event_type == "turn_status":
        CONSOLE.print(f"[dim]Turn: {event.get('phase', 'running')}[/dim]")
    elif event_type == "status":
        label = str(event.get("status") or "")
        scope = str(event.get("scope") or "")
        detail = f" · {scope}" if scope else ""
        CONSOLE.print(f"[dim]Status: {label[:64]}{detail}[/dim]")
    elif event_type == "progress":
        scope = str(event.get("scope") or "")[:64]
        checkpoint = event.get("checkpoint")
        count = f" · checkpoint {checkpoint}" if type(checkpoint) is int and checkpoint >= 0 else ""
        CONSOLE.print(f"[dim]Progress · {scope}{count}[/dim]")
    elif event_type == "fenced":
        CONSOLE.print("[yellow]  Publication fenced; the late result was not delivered.[/yellow]")
    elif event_type == "module_start":
        CONSOLE.print(Text(f"  → Module: {event.get('module', 'unknown')} · {identity}", style="cyan"))
    elif event_type == "lm_start":
        CONSOLE.print(Text(f"  Model running: {event.get('model', 'unknown')} · {identity}", style="yellow"))
    elif event_type == "tool_start":
        CONSOLE.print(Text(f"  ⚙ Tool: {event.get('tool', 'unknown')} · {event.get('source', 'dspy')} · {identity}"))
        inputs = event.get("inputs")
        if inputs and not _is_omitted_payload(inputs):
            CONSOLE.print(
                Panel(
                    Text(_format_tool_payload(inputs)),
                    title="Tool arguments",
                    subtitle=Text(identity),
                    border_style="cyan",
                )
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
        output = event.get("output")
        if output not in (None, "null") and not _is_omitted_payload(output):
            CONSOLE.print(
                Panel(
                    Text(_format_tool_payload(output)),
                    title="Tool result",
                    subtitle=Text(identity),
                    border_style="blue",
                )
            )
    elif event_type in {"turn_complete", "turn_cancelled", "error"}:
        for renderable in _terminal_renderables(event):
            CONSOLE.print(renderable)
    elif event_type == "repl_code":
        phase = event.get("phase", "task")
        if phase == "bootstrap":
            CONSOLE.print(Text(f"  ⚙ Python bootstrap · Daytona · {identity}", style="dim"))
        else:
            CONSOLE.print(
                Panel(
                    Syntax(str(event.get("code", ""))[:20000], "python", line_numbers=True, word_wrap=True),
                    title=f"Python · Daytona · {phase}",
                    subtitle=Text(identity),
                )
            )
    elif event_type == "repl_output":
        phase = event.get("phase", "task")
        if phase == "bootstrap" and not event.get("error"):
            CONSOLE.print(
                Text(f"  ✓ Bootstrap completed · {event.get('duration_ms', '?')} ms · {identity}", style="dim")
            )
        else:
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
        raw_evidence = event.get("evidence")
        evidence = raw_evidence if isinstance(raw_evidence, dict) else {}
        override_reason = evidence.get("override_reason")
        override = f" (override={override_reason})" if override_reason else ""
        prob = evidence.get("probability")
        conf = prob if prob is not None else evidence.get("confidence")
        conf_str = (
            f" · confidence={float(conf):.0%}" if isinstance(conf, (int, float)) and not isinstance(conf, bool) else ""
        )
        CONSOLE.print(f"[cyan]Mode: {event.get('mode')} · Route: {event.get('route')}{override}{conf_str}[/cyan]")
        if event.get("summary"):
            CONSOLE.print(
                Panel(
                    Text(str(event["summary"])), title="Decision summary · not private reasoning", border_style="cyan"
                )
            )
    elif event_type == "skill_selection":
        selected = event.get("selected") or []
        decisions = event.get("decisions")
        count_note = f" ({len(selected)}/{len(decisions)} catalog skills)" if isinstance(decisions, list) else ""
        CONSOLE.print(f"[cyan]Skills preloaded:[/cyan] {', '.join(selected) or 'none relevant'}{count_note}")
    elif event_type == "ungrounded_submit":
        verdict = "rejected; the agent must compute them" if event.get("rejected") else "accepted after retries"
        CONSOLE.print(
            f"[yellow]SUBMIT with figures not seen in sandbox output ({verdict}):[/yellow] {event.get('figures')}"
        )
    elif event_type == "learned_note_warning":
        CONSOLE.print(
            Text(f"Unreviewed learned note: figures need verification: {event.get('figures')}", style="yellow")
        )
    elif event_type == "budget_update":
        calls = event.get("lm_calls")
        maximum = event.get("max_llm_calls")
        if isinstance(calls, int) and isinstance(maximum, int) and calls >= maximum:
            CONSOLE.print(f"[yellow]Shared budget full; no calls remain:[/yellow] {calls}/{maximum} units")
        else:
            CONSOLE.print(f"[dim]Budget units: {calls}/{maximum}[/dim]")
    elif event_type == "sandbox_resuming":
        state = event.get("state") or "stopped"
        CONSOLE.print(f"[yellow]  ↻ Resuming {state} Daytona sandbox…[/yellow] {identity}")
    elif event_type == "orphan_deleted":
        CONSOLE.print(f"[dim]  ✓ Deleted orphaned sandbox {event.get('sandbox_id') or 'unknown'} ({identity})[/dim]")
    elif event_type == "cancellation_requested":
        CONSOLE.print("[yellow]  Cancellation requested; stopping remote execution…[/yellow]")
    elif event_type in {"sandbox_cleanup_failed", "workspace_cleanup_failed"}:
        CONSOLE.print(
            Panel(
                Text(
                    f"Agent {event.get('agent_id')} ← {event.get('parent_agent_id') or 'root'}\n"
                    f"Sandbox: {event.get('sandbox_id') or 'pending'}"
                ),
                title=str(event_type),
                border_style="red",
            )
        )
    elif event_type in {
        "agent_spawn",
        "agent_result",
        "sandbox_provisioning",
        "sandbox_ready",
        "sandbox_deleting",
        "sandbox_deleted",
    }:
        details = [
            f"Agent {event.get('agent_id')} ← {event.get('parent_agent_id') or 'root'}",
            f"Sandbox: {event.get('sandbox_id') or 'pending'}",
        ]
        if event.get("depth") is not None:
            details.append(f"Depth: {event['depth']}")
        if event.get("input_paths"):
            details.append(f"Inputs: {event['input_paths']}")
        if event.get("artifacts"):
            details.append(f"Artifacts: {event['artifacts']}")
        CONSOLE.print(
            Panel(
                Text("\n".join(details)),
                title=str(event_type),
                border_style="cyan",
            )
        )
    # Status is advisory metadata attached by the server; render it without changing any behavior.
    advisory = event.get("status")
    if isinstance(advisory, str) and advisory and event_type not in {"status", "progress"}:
        CONSOLE.print(f"[dim]Status: {advisory[:64]}[/dim]")
