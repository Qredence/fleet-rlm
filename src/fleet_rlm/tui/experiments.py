"""Session-scoped GEPA experiment history and review screen."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Mapping
from typing import Any, ClassVar
from urllib.parse import quote

import httpx
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ExperimentsScreen(ModalScreen[None]):
    """Browse a Session's GEPA history and review a saved candidate."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "close", "Close", show=False),
        Binding("ctrl+c", "cancel_optimizer", "Cancel optimizer", show=False),
    ]
    CSS = """
    ExperimentsScreen { align: center middle; background: $background 85%; }
    #experiments-panel { width: 94%; height: 92%; border: round $primary; background: $surface; padding: 1 2; }
    #experiments-title { height: 2; text-style: bold; color: $text; }
    #experiments-progress { height: 2; color: $text-muted; }
    #experiments-content { height: 1fr; min-height: 0; }
    #experiment-history-scroll { width: 34%; min-width: 24; height: 1fr; border: round $panel; padding: 1; }
    #experiment-detail-scroll { width: 1fr; height: 1fr; min-height: 0; border: round $panel; padding: 1; }
    #experiment-history, #experiment-detail { height: auto; }
    #experiment-actions { height: 6; }
    #experiment-actions Horizontal { height: 3; }
    #experiment-actions Button { width: 1fr; min-width: 9; margin: 0 1; padding: 0 1; }
    ExperimentsScreen.compact #experiments-content { layout: vertical; }
    ExperimentsScreen.compact #experiments-panel { padding: 0 1; }
    ExperimentsScreen.compact #experiments-title, ExperimentsScreen.compact #experiments-progress { height: 1; }
    ExperimentsScreen.compact #experiment-history-scroll {
        width: 1fr; min-width: 0; min-height: 0; height: 1fr; padding: 0;
    }
    ExperimentsScreen.compact #experiment-detail-scroll { width: 1fr; height: 2fr; padding: 0; }
    """

    def __init__(
        self,
        client: httpx.Client,
        session_id: str,
        *,
        on_run: Callable[[dict[str, Any] | str], None],
        on_cancel: Callable[[str], None],
        on_review: Callable[[str, str, str], None],
        on_snapshot: Callable[[dict[str, Any] | None], None] | None = None,
    ) -> None:
        super().__init__()
        self.client = client
        self.session_id = session_id
        self.on_run = on_run
        self.on_cancel = on_cancel
        self.on_review = on_review
        self.on_snapshot = on_snapshot
        self.records: list[dict[str, Any]] = []
        self.progress: dict[str, Any] = {}
        self.comparison: dict[str, Any] | None = None
        self.comparison_run_id: str | None = None
        self.active_run_id: str | None = None
        self.accepting = True
        self._polling = False
        self._refresh_pending = False
        self._review_request_ids: dict[tuple[str, str], str] = {}
        self.selected_run_id: str | None = None

    def compose(self) -> ComposeResult:
        """Compose the history, review detail, and guarded actions."""
        with Vertical(id="experiments-panel"):
            yield Static(f"Experiments · Session {self.session_id}", id="experiments-title")
            yield Static("", id="experiments-progress")
            with Horizontal(id="experiments-content"):
                with VerticalScroll(id="experiment-history-scroll"):
                    yield Static("Loading session history…", id="experiment-history")
                with VerticalScroll(id="experiment-detail-scroll"):
                    yield Static("Select an experiment to inspect its evidence.", id="experiment-detail")
            with Vertical(id="experiment-actions"):
                with Horizontal():
                    yield Button("Previous", id="experiment-previous")
                    yield Button("Next", id="experiment-next")
                    yield Button("Run GEPA", variant="primary", id="experiment-run")
                with Horizontal():
                    yield Button("Approve", variant="success", id="experiment-approve", disabled=True)
                    yield Button("Reject", variant="error", id="experiment-reject", disabled=True)
                    yield Button("Cancel", variant="warning", id="experiment-cancel", disabled=True)
                    yield Button("Close", id="experiment-close")

    def on_resize(self, event: Resize) -> None:
        """Keep evidence and every action reachable in compact terminals."""
        self.set_class(event.size.width < 80, "compact")

    def on_mount(self) -> None:
        """Load the current Session history and keep it refreshed while open."""
        self.refresh_history()
        self.set_interval(1.0, self.refresh_history)

    def action_close(self) -> None:
        """Return to the main chat screen."""
        self.dismiss()

    def action_cancel_optimizer(self) -> None:
        """Request the same cooperative stop used by the main chat controls."""
        if self.active_run_id:
            self.on_cancel(self.active_run_id)

    def set_progress(self, event: Mapping[str, Any]) -> None:
        """Show only stage names or metric-call counts reported by GEPA."""
        self.progress = dict(event)
        self._render_progress()
        self._update_actions(self._selected())

    def focus_run(self, run_id: str) -> None:
        """Select the active run even if its record has not appeared yet."""
        self.selected_run_id = run_id
        self.comparison = None
        self.comparison_run_id = None
        if self.is_mounted and self.is_attached:
            self._render_detail()
        self.refresh_history()

    def refresh_history(self) -> None:
        """Fetch the current API snapshot without blocking Textual's event loop."""
        if self._polling:
            self._refresh_pending = True
            return
        self._polling = True
        self.run_worker(self._refresh_history(), name="experiments-snapshot", group="experiments", exit_on_error=False)

    async def _refresh_history(self) -> None:
        """Refresh history and selected evidence, discarding stale or detached detail responses.

        Handled history fetch errors show an unavailable message; handled detail fetch
        errors clear the selected comparison. Coalesce refreshes requested while busy.
        """
        try:
            payload = await asyncio.to_thread(self._fetch_snapshot)
            if not self.is_mounted or not self.is_attached:
                self._polling = False
                return
            self.records = [item for item in payload.get("experiments", []) if isinstance(item, dict)]
            active = payload.get("active_run_id")
            self.active_run_id = active if isinstance(active, str) else None
            self.accepting = payload.get("accepting") is True
            available = {record.get("run_id") for record in self.records}
            if self.selected_run_id not in available:
                self.selected_run_id = self.records[0]["run_id"] if self.records else None
                self.comparison = None
                self.comparison_run_id = None
            error = None
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            if not self.is_attached:
                self._polling = False
                return
            self.records = []
            self.active_run_id = None
            error = "Fleet API history is unavailable; retrying."
        selected = self._selected()
        if selected is not None:
            requested_run_id = str(selected.get("run_id", ""))
            try:
                detail = await asyncio.to_thread(self._fetch_detail, requested_run_id)
                if not self.is_mounted or not self.is_attached:
                    self._polling = False
                    return
                experiment = detail.get("experiment")
                if not isinstance(experiment, dict) or experiment.get("run_id") != requested_run_id:
                    raise ValueError("experiment detail identity mismatch")
                if self.selected_run_id == requested_run_id:
                    self.comparison = detail.get("comparison") if isinstance(detail.get("comparison"), dict) else None
                    self.comparison_run_id = requested_run_id if self.comparison is not None else None
                    selected.update(experiment)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                if not self.is_attached:
                    self._polling = False
                    return
                if self.selected_run_id == requested_run_id:
                    self.comparison = None
                    self.comparison_run_id = None
        else:
            self.comparison = None
            self.comparison_run_id = None
        self._sync_progress()
        lines = ["SESSION RUNS", ""]
        lines.extend(
            f"{'>' if record['run_id'] == self.selected_run_id else ' '} {record.get('status', 'unknown'):10} "
            f"{record['run_id']}  ·  {record.get('stage', '—')}"
            for record in self.records
        )
        if not self.records:
            lines.append("No optimization runs for this Session.")
        if error:
            lines = [error]
        self.query_one("#experiment-history", Static).update(Text("\n".join(lines), overflow="fold"))
        self._render_progress()
        self._render_detail()
        self._polling = False
        if self._refresh_pending and self.is_mounted and self.is_attached:
            self._refresh_pending = False
            self.refresh_history()

    def _fetch_snapshot(self) -> dict[str, Any]:
        """Fetch this Session's experiment snapshot.

        HTTP and JSON decoding errors propagate; a response that is not an object raises ValueError.
        """
        response = self.client.get(f"/sessions/{quote(self.session_id, safe='')}/experiments")
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("invalid experiment snapshot")
        return value

    def _fetch_detail(self, run_id: str) -> dict[str, Any]:
        """Fetch a run's saved evidence within this Session.

        HTTP and JSON decoding errors propagate; a response that is not an object raises ValueError.
        """
        response = self.client.get(f"/sessions/{quote(self.session_id, safe='')}/experiments/{quote(run_id, safe='')}")
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("invalid experiment detail")
        return value

    def _active_record(self) -> dict[str, Any] | None:
        """Return the history row matching active_run_id, or None when it is absent."""
        return next((record for record in self.records if record.get("run_id") == self.active_run_id), None)

    def _sync_progress(self) -> None:
        """Display active or selected progress and notify the observer of the active run."""
        active = self._active_record()
        visible = active or self._selected()
        progress = visible.get("progress") if visible else None
        self.progress = dict(progress) if isinstance(progress, Mapping) else {}
        if visible and not self.progress:
            self.progress = {"stage": visible.get("stage"), "status": visible.get("status")}
        if self.on_snapshot is not None:
            self.on_snapshot(active)

    def _render_progress(self) -> None:
        """Display observed metric-call counts, stage, or terminal status without estimating progress."""
        kind = self.progress.get("type")
        stage = self.progress.get("stage")
        active = self._active_record()
        if kind == "progress" and type(self.progress.get("metric_calls_used")) is int:
            used = self.progress["metric_calls_used"]
            remaining = self.progress.get("metric_calls_remaining")
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
        self.query_one("#experiments-progress", Static).update(message)

    def _selected(self) -> dict[str, Any] | None:
        """Return the history row matching selected_run_id, or None when it is absent."""
        return next((record for record in self.records if record.get("run_id") == self.selected_run_id), None)

    def _render_detail(self) -> None:
        """Show evidence or a message when no run is selected, and update the available actions."""
        record = self._selected()
        detail = "No experiment selected." if record is None else self._format_record(record)
        self.query_one("#experiment-detail", Static).update(Text(detail, overflow="fold"))
        self._update_actions(record)

    def _format_record(self, record: dict[str, Any]) -> str:
        """Format saved outcomes and diagnostics, using comparison evidence only for the matching run."""
        run_id = str(record.get("run_id", "unknown"))
        lines = [
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
            critical_passes = sum(score == 1.0 for score in critical_scores)
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

    def _update_actions(self, record: dict[str, Any] | None) -> None:
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
        self.query_one("#experiment-approve", Button).disabled = not (
            (reviewable or recoverable)
            and self.comparison is not None
            and self.comparison_run_id == record.get("run_id")
            and isinstance(gate, Mapping)
            and gate.get("passed") is True
        )
        self.query_one("#experiment-reject", Button).disabled = not reviewable
        self.query_one("#experiment-cancel", Button).disabled = not is_selected_active
        self.query_one("#experiment-run", Button).disabled = not self.accepting or self.active_run_id is not None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dispatch navigation, run, cancellation, and review actions."""
        button_id = event.button.id
        if button_id == "experiment-close":
            self.dismiss()
        elif button_id == "experiment-run":
            self.on_run("")
        elif button_id == "experiment-cancel":
            if self.selected_run_id:
                self.on_cancel(self.selected_run_id)
        elif button_id in {"experiment-approve", "experiment-reject"}:
            record = self._selected()
            if record is None:
                return
            if button_id == "experiment-approve":
                self._update_actions(record)
                if self.query_one("#experiment-approve", Button).disabled:
                    return
                decision = "approved"
                self.on_review(record["run_id"], decision, self._review_request_id(record["run_id"], decision))
            else:
                decision = "rejected"
                self.on_review(record["run_id"], decision, self._review_request_id(record["run_id"], decision))
        elif button_id in {"experiment-previous", "experiment-next"} and self.records:
            index = next((i for i, record in enumerate(self.records) if record["run_id"] == self.selected_run_id), 0)
            offset = -1 if button_id == "experiment-previous" else 1
            self.selected_run_id = self.records[(index + offset) % len(self.records)]["run_id"]
            self.comparison = None
            self.comparison_run_id = None
            self._render_history()
            self._render_detail()
            self.refresh_history()

    def _review_request_id(self, run_id: str, decision: str) -> str:
        """Reuse an id for retries of one unresolved review action."""
        key = (run_id, decision)
        return self._review_request_ids.setdefault(key, str(uuid.uuid4()))

    def _finish_review_request(self, run_id: str, decision: str) -> None:
        """Allow a new user action after the API confirms the old request's outcome."""
        self._review_request_ids.pop((run_id, decision), None)

    def _render_history(self) -> None:
        """Render saved Session runs with the selected row marked, or show an empty history."""
        lines = ["SESSION RUNS", ""]
        lines.extend(
            f"{'>' if record['run_id'] == self.selected_run_id else ' '} {record.get('status', 'unknown'):10} "
            f"{record['run_id']}  ·  {record.get('stage', '—')}"
            for record in self.records
        )
        if not self.records:
            lines.append("No optimization runs for this Session.")
        self.query_one("#experiment-history", Static).update(Text("\n".join(lines), overflow="fold"))
