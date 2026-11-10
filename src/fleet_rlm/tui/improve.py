"""Improve screen: label Turns, check readiness, run and review global GEPA jobs.

A pure HTTP client of the `/improve/*` API; the API owns labels, jobs, and the installed program.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import quote

import httpx
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Input, Sparkline, Static, TabbedContent, TabPane

from fleet_rlm.contracts import figures

STAGES = ("preparing", "search", "validation", "finalization")
# The queue API truncates task and answer previews at this length.
PREVIEW_CHARS = 500
MAX_FIGURES = 20
QUEUE_LIMIT = 50
_SLOW_EVERY = 5


def error_text(error: Exception) -> str:
    """Name the API's sanitized detail when it sent one, otherwise the exception type."""
    if isinstance(error, httpx.HTTPStatusError):
        try:
            detail = error.response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        if isinstance(detail, str) and detail:
            return detail
        return f"HTTP {error.response.status_code}"
    return type(error).__name__


def save_label(client: httpx.Client, body: dict[str, Any]) -> dict[str, Any]:
    """Post one Turn label; HTTP errors propagate."""
    response = client.post("/improve/labels", json=body)
    response.raise_for_status()
    value = response.json()
    return value if isinstance(value, dict) else {}


def label_figures(answer: str) -> list[str]:
    """The answer's number literals, in the same bounded form the queue API serves."""
    return list(figures(answer))[:MAX_FIGURES]


def confirm_text(options: Mapping[str, Any]) -> str:
    """Describe the billed work a job request starts, including its budget and token cap."""
    calls = options.get("max_metric_calls")
    budget = f"{calls} metric calls" if calls else f"{options.get('gepa_budget') or 'light'} GEPA budget"
    cap = options.get("max_total_tokens")
    tokens = "server default (FLEET_JOB_MAX_TOKENS)" if cap is None else ("unlimited" if cap == 0 else f"{cap:,}")
    return (
        "Run optimize over the labelled Turns (up to the newest 500 approved labels)?\n\n"
        f"Search budget: {budget} (reflection and validation cost extra)\n"
        f"Token cap: {tokens}\n\n"
        "This calls the model provider and Daytona."
    )


def _bar(used: int | float, total: int | float | None, width: int = 24) -> str:
    if not total:
        return f"{used:,.0f} used"
    filled = max(0, min(width, round(width * used / total)))
    return f"[{'█' * filled}{'░' * (width - filled)}] {used:,.0f}/{total:,.0f}"


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _elapsed(record: Mapping[str, Any]) -> str | None:
    try:
        start = datetime.fromisoformat(str(record["started_at"]))
        end = datetime.fromisoformat(str(record["finished_at"])) if record.get("finished_at") else datetime.now(UTC)
    except (KeyError, ValueError):
        return None
    seconds = max(0, int((end - start).total_seconds()))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def stepper(record: Mapping[str, Any]) -> Text:
    """Show the job's stages with completed ones ticked and the current one highlighted."""
    stage, status = record.get("stage"), record.get("status")
    current = STAGES.index(stage) if stage in STAGES else (len(STAGES) if status in {"completed", "approved"} else -1)
    text = Text()
    for index, name in enumerate(STAGES):
        if index:
            text.append(" → ")
        if index < current:
            text.append(f"✓ {name}", style="green")
        elif index == current:
            text.append(f"▶ {name}", style="bold reverse")
        else:
            text.append(f"· {name}", style="dim")
    return text


def example_rows(comparison: Mapping[str, Any] | None) -> list[tuple[str, str, str, str]]:
    """Per-example baseline and candidate scores, largest gain first."""
    manifest = comparison.get("manifest") if comparison else None
    evaluation = manifest.get("evaluation") if isinstance(manifest, Mapping) else None
    if not isinstance(evaluation, Mapping):
        return []
    baseline, candidate = evaluation.get("baseline") or {}, evaluation.get("candidate") or {}
    rows: list[tuple[float, str, float, float]] = []
    for split in ("dev", "holdout"):
        before, after = baseline.get(f"{split}_scores"), candidate.get(f"{split}_scores")
        if not (isinstance(before, list) and isinstance(after, list) and len(before) == len(after)):
            continue
        rows.extend(
            (y - x, f"{split} #{i}", x, y)
            for i, (x, y) in enumerate(zip(before, after, strict=True), 1)
            if _number(x) is not None and _number(y) is not None
        )
    rows.sort(key=lambda row: -row[0])
    return [(name, f"{x:.3f}", f"{y:.3f}", f"{delta:+.3f}") for delta, name, x, y in rows]


class ImproveScreen(ModalScreen[None]):
    """Review labels, readiness, runs, and results of the global improvement loop."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "close", "Close", show=False),
        Binding("ctrl+c", "cancel_optimizer", "Cancel optimizer", show=False),
    ]
    CSS = """
    ImproveScreen { align: center middle; background: $background 85%; }
    #improve-panel { width: 94%; height: 92%; border: round $primary; background: $surface; padding: 0 2; }
    #improve-title { height: 1; text-style: bold; color: $text; }
    #improve-progress { height: 1; color: $text-muted; }
    #improve-tabs { height: 1fr; min-height: 0; }
    #improve-tabs TabPane { padding: 0; }
    .pane-body { height: 1fr; min-height: 0; }
    .pane-actions { height: 3; }
    .pane-actions Button { width: 1fr; min-width: 9; margin: 0 1; padding: 0 1; }
    #improve-footer { height: 1; align-horizontal: right; }
    #review-queue, #runs-table { width: 45%; height: 1fr; }
    #review-detail-scroll, #runs-scroll, #results-scroll, #readiness-scroll {
        width: 1fr; height: 1fr; min-height: 0; border: round $panel; padding: 0 1;
    }
    #review-text, #runs-detail, #results-text, #readiness-text, #review-figures { height: auto; }
    #review-figures Checkbox { height: auto; padding: 0; border: none; }
    #review-detail-scroll Input { margin: 0; }
    #runs-spark { height: 2; margin: 1 0; }
    #results-examples { height: auto; max-height: 14; margin-top: 1; }
    ImproveScreen.compact #improve-panel { padding: 0 1; }
    ImproveScreen.compact #review-body, ImproveScreen.compact #runs-body { layout: vertical; }
    ImproveScreen.compact #review-queue, ImproveScreen.compact #runs-table { width: 1fr; height: 5; }
    """

    def __init__(
        self,
        client: httpx.Client,
        *,
        on_run: Callable[[], None],
        on_cancel: Callable[[str], None],
        on_review: Callable[[str, str, str], None],
        on_snapshot: Callable[[dict[str, Any] | None], None] | None = None,
        tab: str = "review",
        turn_id: str | None = None,
    ) -> None:
        super().__init__()
        self.client = client
        self.on_run = on_run
        self.on_cancel = on_cancel
        self.on_review = on_review
        self.on_snapshot = on_snapshot
        self.initial_tab = tab
        self.queue: list[dict[str, Any]] = []
        self.readiness: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []
        self.progress: dict[str, Any] = {}
        self.comparison: dict[str, Any] | None = None
        self.comparison_run_id: str | None = None
        self.active_run_id: str | None = None
        self.accepting = True
        self.selected_run_id: str | None = None
        self.selected_turn_id: str | None = turn_id
        self._figure_boxes: list[tuple[str, Checkbox]] = []
        self._loaded_turn: str | None = None
        self._prefilled_answer = ""
        self.queue_error = False
        self.queue_offset = 0
        self.next_queue_offset = None
        self._polling = False
        self._refresh_pending = False
        self._ticks = 0
        self._slow_now = True
        self._review_request_ids: dict[tuple[str, str], str] = {}

    # Layout

    def compose(self) -> ComposeResult:
        """Compose the four tabs and their guarded actions."""
        with Vertical(id="improve-panel"):
            yield Static("Improve · all Sessions · esc closes", id="improve-title")
            yield Static("", id="improve-progress")
            with TabbedContent(initial=self.initial_tab, id="improve-tabs"):
                with TabPane("Review", id="review"):
                    with Horizontal(id="review-body", classes="pane-body"):
                        yield DataTable(id="review-queue", cursor_type="row", zebra_stripes=True)
                        with VerticalScroll(id="review-detail-scroll"):
                            yield Static("Loading replay-captured Turns…", id="review-text")
                            yield Vertical(id="review-figures")
                            yield Input(placeholder="expected answer", id="review-answer")
                            yield Input(placeholder="extra values, comma-separated", id="review-extra")
                            yield Checkbox("Critical (pinned to holdout)", id="review-critical")
                            yield Input(placeholder="group (default: the Session)", id="review-group")
                    with Horizontal(classes="pane-actions"):
                        yield Button("Newer", id="queue-newer")
                        yield Button("Older", id="queue-older")
                        yield Button("✓ Approve", variant="success", id="label-approve", disabled=True)
                        yield Button("✗ Reject", variant="error", id="label-reject", disabled=True)
                with TabPane("Readiness", id="readiness"), VerticalScroll(id="readiness-scroll"):
                    yield Static("Loading readiness…", id="readiness-text")
                with TabPane("Runs", id="runs"):
                    with Horizontal(id="runs-body", classes="pane-body"):
                        yield DataTable(id="runs-table", cursor_type="row", zebra_stripes=True)
                        with VerticalScroll(id="runs-scroll"):
                            yield Static("Loading job history…", id="runs-detail")
                            yield Sparkline([], id="runs-spark")
                    with Horizontal(classes="pane-actions"):
                        yield Button("Run optimize", variant="primary", id="job-run")
                        yield Button("Cancel", variant="warning", id="job-cancel", disabled=True)
                with TabPane("Results", id="results"):
                    with VerticalScroll(id="results-scroll", classes="pane-body"):
                        yield Static("Select a run in Runs to inspect its evidence.", id="results-text")
                        yield DataTable(id="results-examples", cursor_type="none", zebra_stripes=True)
                    with Horizontal(classes="pane-actions"):
                        yield Button("Approve", variant="success", id="job-approve", disabled=True)
                        yield Button("Reject", variant="error", id="job-reject", disabled=True)
            with Horizontal(id="improve-footer"):
                yield Button("Close", id="improve-close", compact=True)

    def on_resize(self, event: Resize) -> None:
        """Keep every action reachable in compact terminals."""
        self.set_class(event.size.width < 80, "compact")

    def on_mount(self) -> None:
        """Build the tables, load the API state, and keep it refreshed while open."""
        self.query_one("#review-queue", DataTable).add_columns("Label", "Session", "Task", "Answer")
        self.query_one("#runs-table", DataTable).add_columns("Run", "Kind", "Status", "Stage")
        self.query_one("#results-examples", DataTable).add_columns("Example", "Baseline", "Candidate", "Delta")
        self.refresh_state()
        self.set_interval(1.0, self.refresh_state)

    def action_close(self) -> None:
        """Return to the main chat screen."""
        self.dismiss()

    def action_cancel_optimizer(self) -> None:
        """Request the same cooperative stop used by the main chat controls."""
        if self.active_run_id:
            self.on_cancel(self.active_run_id)

    # Polling

    def refresh_state(self) -> None:
        """Fetch the API snapshot without blocking Textual's event loop; coalesce requests made while busy."""
        if self._polling:
            self._refresh_pending = True
            return
        self._polling = True
        self.run_worker(self._refresh(), name="improve-snapshot", group="improve", exit_on_error=False)

    def _get(self, path: str) -> dict[str, Any]:
        response = self.client.get(path)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("invalid improve response")
        return value

    def _fetch_slow(self) -> dict[str, Any]:
        """Fetch the slower-moving views, omitting any that fail so the others still update."""
        fetched: dict[str, Any] = {}
        for name, path in (
            ("queue", f"/improve/queue?limit={QUEUE_LIMIT}&offset={self.queue_offset}"),
            ("readiness", "/improve/readiness"),
            ("history", "/improve/history"),
        ):
            try:
                fetched[name] = self._get(path)
            except (httpx.HTTPError, ValueError):
                continue
        return fetched

    async def _refresh(self) -> None:
        """Run one refresh, then rerun it if another was requested meanwhile."""
        try:
            await self._refresh_snapshot()
        finally:
            self._polling = False
        if self._refresh_pending and self.is_attached:
            self._refresh_pending = False
            self.refresh_state()

    async def _refresh_snapshot(self) -> None:
        """Refresh jobs, selected evidence, and (every few ticks) labels, readiness, and history.

        Handled fetch errors show an unavailable message or clear the selected comparison;
        stale or detached detail responses are discarded.
        """
        slow = self._slow_now or self._ticks % _SLOW_EVERY == 0
        self._slow_now = False
        self._ticks += 1
        try:
            payload = await asyncio.to_thread(self._get, "/improve/jobs")
            if not self.is_attached:
                return
            self.records = [item for item in payload.get("jobs", []) if isinstance(item, dict)]
            active = payload.get("active_run_id")
            self.active_run_id = active if isinstance(active, str) else None
            self.accepting = payload.get("accepting") is True
            if self.selected_run_id not in {record.get("run_id") for record in self.records}:
                self.selected_run_id = self.records[0]["run_id"] if self.records else None
                self.comparison = None
                self.comparison_run_id = None
            error = None
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            if not self.is_attached:
                return
            self.records = []
            self.active_run_id = None
            error = "Fleet API history is unavailable; retrying."
        # Show the job list now; the selected job's evidence may be slow to arrive.
        self._render_runs(error)
        selected = self._selected()
        if selected is not None:
            requested = str(selected.get("run_id", ""))
            try:
                detail = await asyncio.to_thread(self._get, f"/improve/jobs/{quote(requested, safe='')}")
                if not self.is_attached:
                    return
                job = detail.get("job")
                if not isinstance(job, dict) or job.get("run_id") != requested:
                    raise ValueError("job detail identity mismatch")
                if self.selected_run_id == requested:
                    self.comparison = detail.get("comparison") if isinstance(detail.get("comparison"), dict) else None
                    self.comparison_run_id = requested if self.comparison is not None else None
                    selected.update(job)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                if not self.is_attached:
                    return
                if self.selected_run_id == requested:
                    self.comparison = None
                    self.comparison_run_id = None
        else:
            self.comparison = None
            self.comparison_run_id = None
        if slow:
            fetched = await asyncio.to_thread(self._fetch_slow)
            if not self.is_attached:
                return
            self.queue_error = not isinstance(fetched.get("queue", {}).get("items"), list)
            if self.queue_error:
                self.queue = []
            if not self.queue_error:
                self.next_queue_offset = fetched["queue"].get("next_offset")
                self.queue = [item for item in fetched["queue"]["items"] if isinstance(item, dict)]
            if "readiness" in fetched:
                self.readiness = fetched["readiness"]
            if isinstance(fetched.get("history", {}).get("versions"), list):
                self.history = [item for item in fetched["history"]["versions"] if isinstance(item, dict)]
        self._sync_progress(publish=error is None)
        self._render_all(error)

    def _selected(self) -> dict[str, Any] | None:
        return next((record for record in self.records if record.get("run_id") == self.selected_run_id), None)

    def _active_record(self) -> dict[str, Any] | None:
        return next((record for record in self.records if record.get("run_id") == self.active_run_id), None)

    def _selected_item(self) -> dict[str, Any] | None:
        return next((item for item in self.queue if item.get("turn_id") == self.selected_turn_id), None)

    def _sync_progress(self, *, publish: bool = True) -> None:
        """Track the active (or selected) job's progress and tell the chat screen about the active one."""
        active = self._active_record()
        visible = active or self._selected()
        progress = visible.get("progress") if visible else None
        self.progress = dict(progress) if isinstance(progress, Mapping) else {}
        if visible and not self.progress:
            self.progress = {"stage": visible.get("stage"), "status": visible.get("status")}
        if publish and self.on_snapshot is not None:
            self.on_snapshot(active)

    # Rendering

    def _render_all(self, error: str | None = None) -> None:
        self._render_progress()
        self._render_review()
        self._render_readiness()
        self._render_runs(error)
        self._render_results()

    def _render_progress(self) -> None:
        """Display observed metric-call counts, stage, or terminal status without estimating progress."""
        kind, stage = self.progress.get("type"), self.progress.get("stage")
        active = self._active_record()
        if type(self.progress.get("metric_calls_used")) is int:
            used, remaining = self.progress["metric_calls_used"], self.progress.get("metric_calls_remaining")
            message = f"Search · {used} metric calls used"
            if type(remaining) is int and remaining >= 0:
                message = f"Search · {used}/{used + remaining} metric calls"
        elif active and active.get("status") not in {"running", "approving"}:
            message = f"Optimization · {str(active.get('status'))[:60]}"
        elif kind == "terminal":
            message = f"Optimization · {str(self.progress.get('status', 'finished'))[:60]}"
        elif stage:
            message = f"Optimization · {str(stage)[:60]}"
        else:
            message = "No active optimization progress."
        self.query_one("#improve-progress", Static).update(message)

    @staticmethod
    def _sync_table(table: DataTable, rows: Sequence[tuple[str, tuple[str, ...]]], selected: str | None) -> None:
        """Update cells in place while the keys are unchanged, so polling never moves the user's cursor."""
        keys = [key for key, _ in rows]
        if keys == [row.value for row in table.rows]:
            for key, cells in rows:
                for column, value in zip(table.columns, cells, strict=True):
                    table.update_cell(key, column, value, update_width=True)
            return
        table.clear()
        for key, cells in rows:
            table.add_row(*cells, key=key)
        if selected in keys:
            table.move_cursor(row=keys.index(selected), animate=False)

    def _render_review(self) -> None:
        """Show the queue; the form follows the selected Turn and is rebuilt only when that changes."""
        if self.selected_turn_id is None and self.queue:
            self.selected_turn_id = self.queue[0]["turn_id"]
        rows = [
            (
                item["turn_id"],
                (
                    str(item.get("label", "")),
                    str(item.get("session_id", ""))[-10:],
                    item["task"][:40],
                    item["answer"][:40],
                ),
            )
            for item in self.queue
        ]
        self._sync_table(self.query_one("#review-queue", DataTable), rows, self.selected_turn_id)
        self.query_one("#queue-newer", Button).disabled = self.queue_offset == 0
        self.query_one("#queue-older", Button).disabled = self.queue_error or self.next_queue_offset is None
        item = self._selected_item()
        if item is None:
            if not self.queue:
                self.query_one("#review-text", Static).update(
                    "The review queue is unavailable; retrying."
                    if self.queue_error
                    else "No replay-captured Turns yet. Run one with /replay on."
                )
        elif self._loaded_turn != item["turn_id"]:
            self._load_form(item)
        for button in ("#label-approve", "#label-reject"):
            self.query_one(button, Button).disabled = item is None

    def _load_form(self, item: dict[str, Any]) -> None:
        """Prefill the form: every pre-extracted figure checked, the answer as the expected answer."""
        self._loaded_turn = item["turn_id"]
        labelled = item.get("label") != "unlabelled"
        saved = list(item.get("expected_values") or [])
        listed = list(item.get("figures") or [])
        self.query_one("#review-text", Static).update(
            Text(f"Label  {item.get('label')}\n\nTASK\n{item['task']}\n\nANSWER\n{item['answer']}", overflow="fold")
        )
        container = self.query_one("#review-figures", Vertical)
        container.remove_children()
        self._figure_boxes = [(value, Checkbox(value, value=not labelled or value in saved)) for value in listed]
        if self._figure_boxes:
            container.mount(Static("Figures that must appear in the answer:"), *(box for _, box in self._figure_boxes))
        answer = self.query_one("#review-answer", Input)
        truncated = len(item["answer"]) >= PREVIEW_CHARS
        answer.value = item.get("expected_answer") or ("" if truncated else item["answer"])
        self._prefilled_answer = answer.value
        answer.placeholder = "answer was truncated; type the expected answer" if truncated else "expected answer"
        self.query_one("#review-extra", Input).value = ", ".join(v for v in saved if v not in listed)
        self.query_one("#review-critical", Checkbox).value = bool(item.get("critical"))
        self.query_one("#review-group", Input).value = item.get("group") or ""

    def _render_readiness(self) -> None:
        report = self.readiness
        if report is None:
            text = "Readiness is unavailable; retrying."
        else:
            sizes = report.get("split_sizes") or {}
            lines = [
                "READY · a job can start" if report.get("ready") else "NOT READY · a job would refuse to start",
                "",
                f"Labelled examples  {report.get('examples', 0)}",
                f"Source groups  {report.get('groups', 0)}",
                f"Critical cases  {report.get('critical', 0)}",
                "Split sizes  " + (" · ".join(f"{name} {count}" for name, count in sizes.items()) or "none"),
                f"New labels since the last run  {report.get('new_labels_since_last_run', 0)}",
            ]
            missing = report.get("missing") or []
            if missing:
                lines += ["", "STILL NEEDED", *(f"  • {item}" for item in missing)]
            text = "\n".join(lines)
        self.query_one("#readiness-text", Static).update(Text(text, overflow="fold"))

    def _render_runs(self, error: str | None) -> None:
        rows = [
            (
                str(record["run_id"]),
                (
                    str(record["run_id"])[-14:],
                    str(record.get("kind", "optimize")),
                    str(record.get("status", "unknown")),
                    str(record.get("stage") or "—"),
                ),
            )
            for record in self.records
        ]
        self._sync_table(self.query_one("#runs-table", DataTable), rows, self.selected_run_id)
        record = self._selected()
        detail = self.query_one("#runs-detail", Static)
        spark = self.query_one("#runs-spark", Sparkline)
        if error:
            detail.update(Text(error, overflow="fold"))
            spark.data = []
        elif record is None:
            detail.update("No optimization runs yet.")
            spark.data = []
        else:
            raw = record.get("progress")
            progress: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
            used = _number(progress.get("metric_calls_used"))
            remaining = _number(progress.get("metric_calls_remaining"))
            tokens, cap = _number(progress.get("tokens_used")), _number(progress.get("token_cap"))
            out = Text()
            out.append(f"RUN {record['run_id']} · {record.get('kind', 'optimize')} · {record.get('status')}\n\n")
            out.append("Stage        ")
            out.append_text(stepper(record))
            out.append("\n")
            if used is not None:
                out.append(f"Metric calls {_bar(used, used + remaining if remaining is not None else None)}\n")
            if tokens is not None:
                out.append(f"Tokens       {_bar(tokens, cap)}{'' if cap else ' (no cap)'}\n")
            best = _number(progress.get("best_score"))
            if best is not None:
                out.append(f"Best score   {best:.3f}\n")
            elapsed = _elapsed(record)
            if elapsed:
                out.append(f"Elapsed      {elapsed}\n")
            failure = record.get("failure")
            if isinstance(failure, Mapping):
                out.append(f"\nFAILURE {failure.get('type', 'Error')}: {failure.get('detail', '')}\n")
            detail.update(out)
            trajectory = progress.get("score_trajectory")
            spark.data = (
                [float(point[1]) for point in trajectory if isinstance(point, list) and len(point) == 2]
                if isinstance(trajectory, list)
                else []
            )
        self._update_job_actions(record)

    def _render_results(self) -> None:
        record = self._selected()
        text = "No run selected." if record is None else self._format_record(record)
        self.query_one("#results-text", Static).update(Text(text, overflow="fold"))
        rows = example_rows(self.comparison if record and self.comparison_run_id == record.get("run_id") else None)
        self._sync_table(
            self.query_one("#results-examples", DataTable),
            [(f"{index}", cells) for index, cells in enumerate(rows)],
            None,
        )
        self.query_one("#results-examples", DataTable).display = bool(rows)

    def _trend(self) -> str | None:
        points = []
        for job in reversed(self.history):
            scores = job.get("scores")
            value = (
                _number(((scores or {}).get("candidate") or {}).get("holdout")) if isinstance(scores, Mapping) else None
            )
            if value is not None:
                points.append(f"{value:.3f}")
        return f"INSTALLED TREND · HOLDOUT ({len(points)} versions)  " + " → ".join(points) if points else None

    def _format_record(self, record: dict[str, Any]) -> str:
        """Format saved outcomes and diagnostics, using comparison evidence only for the matching run."""
        run_id = str(record.get("run_id", "unknown"))
        lines = []
        if trend := self._trend():
            lines += [trend, ""]
        lines += [
            f"RUN  {run_id}",
            f"Status  {record.get('status', 'unknown')} · stage {record.get('stage', '—')}",
            f"Started  {record.get('started_at', 'unavailable')}",
            f"Finished  {record.get('finished_at', 'in progress')}",
            f"Candidate  {record.get('candidate_version') or 'none'}",
        ]
        gate = record.get("gate")
        if isinstance(gate, Mapping):
            lines.append(f"Promotion gate  {'PASS' if gate.get('passed') else 'FAIL'}")
            for reason in gate.get("reasons", []):
                lines.append(f"  Gate reason: {reason}")
        if record.get("mlflow_run_id"):
            lines.append(f"MLflow run  {record['mlflow_run_id']}")
        failure = record.get("failure")
        if isinstance(failure, Mapping):
            lines.extend(["", "FAILURE", f"{failure.get('type', 'Error')}: {failure.get('detail', '')}"])
        report = self.comparison if self.comparison_run_id == run_id else None
        if report is None:
            lines.extend(["", "Comparison report unavailable."])
            return "\n".join(lines)
        manifest = report.get("manifest")
        evaluation = manifest.get("evaluation") if isinstance(manifest, Mapping) else None
        if not isinstance(evaluation, Mapping):
            lines.extend(["", "Saved comparison has no evaluation details."])
            return "\n".join(lines)
        baseline = evaluation.get("baseline", {})
        candidate = evaluation.get("candidate", {})
        lines.extend(["", "QUALITY · MEAN SCORE"])
        for split in ("dev", "holdout"):
            before, after = baseline.get(split), candidate.get(split)
            if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                lines.append(f"{split.title()}  {before:.3f} → {after:.3f} ({after - before:+.3f})")
        lines.extend(["", "OBJECTIVES · HOLDOUT"])
        before_objectives = baseline.get("holdout_objectives", {})
        after_objectives = candidate.get("holdout_objectives", {})
        for name in sorted(set(before_objectives) | set(after_objectives)):
            before, after = before_objectives.get(name), after_objectives.get(name)
            if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                lines.append(f"{name}  {before:.3f} → {after:.3f} ({after - before:+.3f})")
        critical_scores = candidate.get("holdout_critical_scores", [])
        if critical_scores:
            critical_passes = sum(score >= 1.0 for score in critical_scores)
            rollout_errors = candidate.get("holdout_errors", "unavailable")
            error_label = "error" if rollout_errors == 1 else "errors"
            lines.append(
                f"Critical holdout cases {critical_passes}/{len(critical_scores)} passed; "
                f"{rollout_errors} rollout {error_label}"
            )
        lines.extend(["", "MEASURED DIAGNOSTICS"])
        for split in ("dev", "holdout"):
            for side, values in (("baseline", baseline), ("candidate", candidate)):
                lm = values.get(f"{split}_lm_calls_median")
                calls = values.get(f"{split}_lm_calls", [])
                measured_calls = [call for call in calls if type(call) is int and call >= 0]
                lm_n = values.get(f"{split}_lm_calls_measured", len(measured_calls))
                runtime = values.get(f"{split}_seconds_per_example_median")
                runtime_n = len(values.get(f"{split}_seconds_per_example", []))
                passes = values.get(f"{split}_artifact_passes", 0)
                applicable = values.get(f"{split}_artifact_cases", 0)
                if isinstance(lm, (int, float)):
                    lm_text = f"total {sum(measured_calls)}, median {lm:g} ({lm_n}/{len(calls)} measured)"
                elif measured_calls:
                    lm_text = f"total {sum(measured_calls)} ({len(measured_calls)}/{len(calls)} measured)"
                else:
                    lm_text = "unavailable"
                runtime_text = (
                    f"{runtime:g}s ({runtime_n} examples)" if isinstance(runtime, (int, float)) else "unavailable"
                )
                lines.append(
                    f"{split.title()} {side}: LM calls {lm_text}; runtime median {runtime_text}; "
                    f"artifact passes {passes}/{applicable} applicable"
                )
        rows = example_rows(report)
        if rows:
            wins = sum(row[3].startswith("+") and row[3] != "+0.000" for row in rows)
            losses = sum(row[3].startswith("-") and row[3] != "-0.000" for row in rows)
            lines.extend(["", f"PER EXAMPLE · {wins} better, {losses} worse, {len(rows) - wins - losses} unchanged"])
        diffs = candidate.get("component_diffs", [])
        lines.extend(["", "COMPONENT CHANGES"])
        if not diffs:
            lines.append("No predictor instructions or Flex source changed.")
        for item in diffs:
            if not isinstance(item, Mapping):
                continue
            lines.extend(["", f"[{item.get('component', 'component')}]", str(item.get("diff", "(diff unavailable)"))])
            lines.extend(["BEFORE", str(item.get("before", "")), "AFTER", str(item.get("after", ""))])
        failures = [
            (split, failure)
            for split in ("dev", "holdout")
            for failure in candidate.get(f"{split}_failures", [])
            if isinstance(failure, Mapping)
        ]
        if failures:
            lines.extend(["", "SANITIZED VALIDATION FAILURES"])
            for split, failure in failures:
                lines.append(
                    f"{split} · {failure.get('case_id', 'unknown')} · {failure.get('type', 'Error')}: "
                    f"{failure.get('detail', '')}"
                )
        return "\n".join(lines)

    def _update_job_actions(self, record: dict[str, Any] | None) -> None:
        """Enable approval only with matching saved evidence and a passing gate.

        Limit rejection to unreviewed completed runs, cancellation to the selected running
        active run, and new searches to open admission with no active run.
        """
        is_selected_active = bool(
            record and record.get("run_id") == self.active_run_id and record.get("status") == "running"
        )
        reviewable = bool(record and record.get("status") == "completed" and not record.get("decision"))
        recoverable = bool(record and record.get("status") == "approving")
        gate = record.get("gate", {}) if record else {}
        self.query_one("#job-approve", Button).disabled = not (
            (reviewable or recoverable)
            # Per-Session jobs from before jobs became global stay readable but the API never approves them.
            and not record.get("legacy_session_id")
            and self.comparison is not None
            and self.comparison_run_id == record.get("run_id")
            and isinstance(gate, Mapping)
            and gate.get("passed") is True
        )
        self.query_one("#job-reject", Button).disabled = not reviewable
        self.query_one("#job-cancel", Button).disabled = not is_selected_active
        self.query_one("#job-run", Button).disabled = not self.accepting or self.active_run_id is not None

    # Events

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Selecting a queue row loads its form; selecting a run switches the evidence shown."""
        key = event.row_key.value
        if key is None:
            return
        if event.data_table.id == "review-queue" and key != self.selected_turn_id:
            self.selected_turn_id = key
            self._render_review()
        elif event.data_table.id == "runs-table" and key != self.selected_run_id:
            self.selected_run_id = key
            self.comparison = None
            self.comparison_run_id = None
            self._render_runs(None)
            self._render_results()
            self.refresh_state()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dispatch labelling, run, cancellation, and review actions."""
        button_id = event.button.id
        if button_id == "improve-close":
            self.dismiss()
        elif button_id in {"queue-newer", "queue-older"}:
            self.queue_offset = (
                max(0, self.queue_offset - QUEUE_LIMIT) if button_id == "queue-newer" else (self.next_queue_offset or 0)
            )
            self.selected_turn_id = None
            self._loaded_turn = None
            self._slow_now = True
            self.refresh_state()
        elif button_id == "job-run":
            self.on_run()
        elif button_id == "job-cancel":
            if self.selected_run_id:
                self.on_cancel(self.selected_run_id)
        elif button_id in {"label-approve", "label-reject"}:
            self._label(approve=button_id == "label-approve")
        elif button_id in {"job-approve", "job-reject"}:
            record = self._selected()
            if record is None:
                return
            self._update_job_actions(record)
            if self.query_one(f"#{button_id}", Button).disabled:
                return
            decision = "approved" if button_id == "job-approve" else "rejected"
            self.on_review(record["run_id"], decision, self._review_request_id(record["run_id"], decision))

    def _label_body(self, item: dict[str, Any], *, approve: bool) -> dict[str, Any]:
        """Build the label request: checked figures plus extras, or a rejection (or correction) of the answer."""
        answer = self.query_one("#review-answer", Input).value.strip()
        extra = [value.strip() for value in self.query_one("#review-extra", Input).value.split(",") if value.strip()]
        body: dict[str, Any] = {"turn_id": item["turn_id"], "status": "approved"}
        if approve:
            body["expected_values"] = [value for value, box in self._figure_boxes if box.value] + extra
            body["critical"] = self.query_one("#review-critical", Checkbox).value
        elif answer and answer not in {item["answer"], self._prefilled_answer}:
            # Only an answer the user typed replaces the Turn's answer as the gold; its own figures are the
            # expected values. A prefilled saved answer left untouched is still a plain rejection.
            body["expected_values"] = label_figures(answer) + extra
        else:
            return {"turn_id": item["turn_id"], "status": "rejected"}
        if answer:
            body["expected_answer"] = answer
        if group := self.query_one("#review-group", Input).value.strip():
            body["group"] = group
        return body

    def _label(self, *, approve: bool) -> None:
        item = self._selected_item()
        if item is not None:
            self.run_worker(
                self._post_label(self._label_body(item, approve=approve)),
                name="improve-label",
                group="improve-label",
                exit_on_error=False,
            )

    async def _post_label(self, body: dict[str, Any]) -> None:
        """Save one label, report the outcome, and move to the next Turn."""
        try:
            await asyncio.to_thread(save_label, self.client, body)
        except httpx.HTTPError as error:
            self.app.notify(f"Label not saved: {error_text(error)}", severity="error")
            return
        self.app.notify(f"Turn {body['status']}")
        keys = [item["turn_id"] for item in self.queue]
        index = keys.index(body["turn_id"]) if body["turn_id"] in keys else -1
        if 0 <= index < len(keys) - 1:
            self.query_one("#review-queue", DataTable).move_cursor(row=index + 1)
        self._slow_now = True
        self.refresh_state()

    def _review_request_id(self, run_id: str, decision: str) -> str:
        """Reuse an id for retries of one unresolved review action."""
        return self._review_request_ids.setdefault((run_id, decision), str(uuid.uuid4()))

    def _finish_review_request(self, run_id: str, decision: str) -> None:
        """Allow a new user action after the API confirms the old request's outcome."""
        self._review_request_ids.pop((run_id, decision), None)
