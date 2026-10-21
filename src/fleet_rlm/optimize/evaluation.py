"""Evaluation datasets, gold labels, and MLflow evaluation runs for Fleet Turns.

Cases are generated from the current Turn contract (attach files, send a task, receive an answer),
never from historical traces or stored Turns.
"""

from __future__ import annotations

import csv
import io
import json
import random
import threading
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any

import httpx
import mlflow
from mlflow.entities import AssessmentSource, AssessmentSourceType, Feedback
from mlflow.genai.scorers import scorer

from fleet_rlm.engine.signatures import Route
from fleet_rlm.observability import trace_object
from fleet_rlm.optimize.dataset import labels_of
from fleet_rlm.optimize.scorers import (
    build_mlflow_scorer_suite,
    missing_values,
)
from fleet_rlm.replay import validate_review_labels

_REGIONS = ("north", "south", "east", "west", "central")
_STATUSES = ("on_time", "late", "cancelled")
_SELF_CONTAINED = (
    ("Convert 72 degrees Fahrenheit to Celsius, rounded to one decimal place.", "22.2", Route.FLEX),
    ("What is 17 multiplied by 23?", "391", Route.FLEX),
    ("How many days are there in a leap year?", "366", Route.DIRECT),
    ("What is the sum of the integers from 1 to 100?", "5050", Route.FLEX),
    ("What is 15 percent of 240?", "36", Route.FLEX),
    ("How many minutes are in 3.5 hours?", "210", Route.FLEX),
)


@dataclass(frozen=True)
class EvalCase:
    """One labeled Turn: input files plus a task, with a checkable expected answer."""

    case_id: str
    task: str
    expected_values: tuple[str, ...]
    category: str
    source_group: str
    files: dict[str, str] = field(default_factory=dict)
    route: str | None = None
    review_status: str = "synthetic"
    answer_type: str = "scalar"
    context_chars: int = 0
    benchmark: str = "synthetic"
    critical: bool = False

    def record(self) -> dict[str, Any]:
        """Return this case as the inputs and expectations record used for evaluation."""
        expectations: dict[str, Any] = {
            "expected_values": list(self.expected_values),
            "expected_facts": [f"The answer states {value}." for value in self.expected_values],
            "review_status": self.review_status,
            "source_group": self.source_group,
            "category": self.category,
            "answer_type": self.answer_type,
            "context_chars": self.context_chars or sum(len(text) for text in self.files.values()) or len(self.task),
            "benchmark": self.benchmark,
            "critical": self.critical,
        }
        if self.route:
            expectations["route"] = self.route
        return {
            "inputs": {"case_id": self.case_id, "task": self.task, "files": dict(self.files)},
            "expectations": expectations,
        }


def _csv(header: list[str], rows: Sequence[Sequence[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")


def synthetic_cases(seed: int = 7, per_category: int = 8) -> list[EvalCase]:
    """Deterministic cases whose gold answers are computed from the generated files."""
    rng = random.Random(seed)
    cases = [
        EvalCase(
            case_id=f"direct-{index:02d}",
            task=task,
            expected_values=(answer,),
            category="direct" if route is Route.DIRECT else "compute",
            source_group=f"direct-{index:02d}",
            route=route.value,
            critical=True,
        )
        for index, (task, answer, route) in enumerate(_SELF_CONTAINED)
    ]
    for index in range(per_category):
        sales = [(rng.choice(_REGIONS), rng.randrange(20, 500)) for _ in range(rng.randrange(8, 40))]
        totals: Counter[str] = Counter()
        for region, revenue in sales:
            totals[region] += revenue
        cases.append(
            EvalCase(
                case_id=f"aggregate-{index:02d}",
                task=(
                    "Calculate total revenue by region from files/sales.csv and save the summary "
                    "as artifacts/revenue_by_region.csv."
                ),
                expected_values=tuple(f"{region}" for region in sorted(totals))
                + tuple(_fmt(totals[region]) for region in sorted(totals)),
                category="aggregate",
                source_group=f"aggregate-{index:02d}",
                files={"files/sales.csv": _csv(["region", "revenue"], sales)},
                route=Route.FLEX.value,
                critical=True,
            )
        )
    for index in range(per_category):
        rows = [[f"S{n:03d}", rng.choice(_STATUSES), rng.randrange(1, 9)] for n in range(rng.randrange(12, 60))]
        late = sum(1 for _, status, _ in rows if status == "late")
        cases.append(
            EvalCase(
                case_id=f"filter-count-{index:02d}",
                task="How many shipments in files/shipments.csv have status late?",
                expected_values=(str(late),),
                category="filter_count",
                source_group=f"filter-count-{index:02d}",
                files={"files/shipments.csv": _csv(["shipment_id", "status", "days"], rows)},
                critical=True,
            )
        )
    for index in range(per_category):
        tiers = {f"C{n:02d}": rng.choice(("free", "pro", "enterprise")) for n in range(rng.randrange(6, 15))}
        orders = [(f"O{n:03d}", rng.choice(list(tiers)), rng.randrange(10, 900)) for n in range(rng.randrange(20, 80))]
        by_tier: defaultdict[str, int] = defaultdict(int)
        for _, customer, amount in orders:
            by_tier[tiers[customer]] += amount
        cases.append(
            EvalCase(
                case_id=f"join-{index:02d}",
                task=(
                    "Join files/orders.csv with files/customers.csv on customer_id and report total order "
                    "amount per tier."
                ),
                expected_values=tuple(sorted(by_tier)) + tuple(_fmt(by_tier[tier]) for tier in sorted(by_tier)),
                category="join",
                source_group=f"join-{index:02d}",
                files={
                    "files/orders.csv": _csv(["order_id", "customer_id", "amount"], orders),
                    "files/customers.csv": _csv(["customer_id", "tier"], [[c, t] for c, t in tiers.items()]),
                },
                critical=True,
            )
        )
    return cases


def build_dataset(name: str, experiment_id: str, cases: list[EvalCase]) -> Any:
    """Create an MLflow evaluation dataset (Unity Catalog table on Databricks) and add the cases."""
    try:
        dataset = mlflow.genai.datasets.get_dataset(name=name)
    except Exception:
        dataset = mlflow.genai.datasets.create_dataset(name=name, experiment_id=experiment_id)
    dataset.merge_records([case.record() for case in cases])
    return dataset


def label_trace(
    trace_id: str,
    *,
    review_status: str,
    source_group: str,
    expected_answer: str | None = None,
    expected_values: list[str] | None = None,
    route: str | None = None,
    critical: bool | None = None,
    reviewer: str = "human",
    structured_expectations: dict[str, Any] | None = None,
) -> None:
    """Write gold labels onto a trace as MLflow expectations."""
    labels: dict[str, Any] = {"review_status": review_status, "source_group": source_group}
    for name, value in (
        ("expected_answer", expected_answer),
        ("expected_values", expected_values),
        ("route", route),
        ("critical", critical),
        ("structured_expectations", structured_expectations),
    ):
        if value is not None:
            labels[name] = value
    validate_review_labels(labels)
    if not reviewer.strip():
        raise ValueError("review labels require a reviewer")
    source_type = AssessmentSourceType.HUMAN if review_status != "synthetic" else AssessmentSourceType.CODE
    source = AssessmentSource(source_type=source_type, source_id=reviewer)
    for name, value in labels.items():
        mlflow.log_expectation(trace_id=trace_id, name=name, value=value, source=source)


def parse_sse(text: str) -> list[dict[str, Any]]:
    """Decode the JSON data lines from a Fleet SSE response, ignoring malformed lines."""
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                continue
    return events


def run_case(client: httpx.Client, case: EvalCase, session_id: str) -> dict[str, Any]:
    """Run one case through the server's attach and Turn endpoints."""
    for path, content in case.files.items():
        response = client.post(
            f"/sessions/{session_id}/attach",
            files={"file": (path.rsplit("/", 1)[-1], content.encode(), "text/csv")},
        )
        response.raise_for_status()
    response = client.post(f"/sessions/{session_id}/turn", json={"task": case.task, "idempotency_key": case.case_id})
    response.raise_for_status()
    events = parse_sse(response.text)
    complete = next((event["payload"] for event in events if event.get("event") == "turn_complete"), None)
    if complete is None:
        failure = next((event for event in events if event.get("event") == "error"), {})
        return {"answer": "", "route": None, "trace_id": None, "error": str(failure.get("payload", failure))[:500]}
    return {
        "answer": complete.get("answer", ""),
        "route": complete.get("route"),
        "trace_id": complete.get("trace_id"),
        "error": None,
    }


@scorer
def expected_values_present(outputs: Any, expectations: dict[str, Any]) -> Feedback | None:
    """Deterministic check: every gold value appears in the answer (thousands separators ignored)."""
    expected = (expectations or {}).get("expected_values") or []
    if not expected:
        return None
    outputs = trace_object(outputs) or outputs
    missing = missing_values(str(outputs.get("answer", "") if isinstance(outputs, dict) else outputs), expected)
    return Feedback(
        value=not missing,
        rationale="All expected values found." if not missing else f"Missing from answer: {missing}",
    )


class EvaluationError(RuntimeError):
    """An evaluation is incomplete; its completed Turns must not be executed again for recovery."""


def read_evaluation_trace(
    trace_id: str,
    *,
    experiment_id: str,
    uc_location: tuple[str, str, str] | None = None,
    wait_seconds: float = 180.0,
    expected_labels: dict[str, Any] | None = None,
) -> Any:
    """Wait for one completed trace in the intended destination, including cold warehouse startup.

    Each read runs on a daemon thread so an SDK request cannot exceed the caller's readback
    window. A timeout abandons that read, never starts a concurrent retry, and never repeats a Turn.
    """
    deadline = time.monotonic() + wait_seconds
    while (remaining := deadline - time.monotonic()) > 0:
        result: Queue[tuple[Any, Exception | None]] = Queue(maxsize=1)

        def read(result: Queue[tuple[Any, Exception | None]] = result) -> None:
            try:
                result.put((mlflow.get_trace(trace_id, silent=True, flush=True), None))
            except Exception as exc:
                result.put((None, exc))

        threading.Thread(target=read, daemon=True).start()
        try:
            trace, error = result.get(timeout=remaining)
        except Empty:
            break
        if error is not None:
            raise EvaluationError(f"Trace readback failed ({type(error).__name__}); recover trace {trace_id}") from None
        if trace is not None:
            location = trace.info.trace_location
            if uc_location is not None:
                location = getattr(location, "uc_table_prefix", None) or location
                actual = tuple(getattr(location, key, None) for key in ("catalog_name", "schema_name", "table_prefix"))
                matches = actual == uc_location
            else:
                location = getattr(location, "mlflow_experiment", None) or location
                matches = str(getattr(location, "experiment_id", "")) == str(experiment_id)
            if not matches:
                raise EvaluationError(f"Trace {trace_id} is outside the configured evaluation destination")
            state = str(trace.info.state)
            if state not in {"OK", "IN_PROGRESS"}:
                raise EvaluationError(f"Trace {trace_id} completed with state {state}")
            if state == "OK" and trace.data.spans:
                if expected_labels is None:
                    return trace
                saved = labels_of(trace)
                if all(saved.get(key) == value for key, value in expected_labels.items()):
                    return trace
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    raise EvaluationError(
        f"Trace {trace_id} was not ready within {wait_seconds:g}s; recover this trace without rerunning"
    )


def evaluate_cases(
    cases: list[EvalCase],
    *,
    base_url: str,
    judge_model: str,
    judge_lm: Any = None,
    jev_lm: Any = None,
    experiment_id: str,
    uc_location: tuple[str, str, str] | None = None,
    session_prefix: str | None = None,
    timeout: float = 600.0,
    collect_only: bool = False,
    report: Callable[[str], None] = print,
) -> Any:
    """Run cases against a live server and label each Turn trace with its gold answer.

    With ``collect_only`` the judge suite is skipped and the saved trace IDs support later
    explicit scoring without executing the Turns again. Returns ``None`` in that mode.
    """
    session_prefix = session_prefix or f"eval-{uuid.uuid4().hex}"
    report(f"Evaluation run: {session_prefix} ({len(cases)} cases)")
    traces = []
    with httpx.Client(base_url=base_url, timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        for case in cases:
            session_id = f"{session_prefix}-{case.case_id}"
            report(f"Case {case.case_id}: Session {session_id}")
            try:
                outputs = run_case(client, case, session_id)
            except httpx.HTTPError as exc:
                raise EvaluationError(
                    f"Case {case.case_id} transport failed ({type(exc).__name__}); inspect Session {session_id} "
                    "before retrying"
                ) from None
            trace_id = outputs.get("trace_id")
            if trace_id:
                report(f"Case {case.case_id}: trace {trace_id}")
            if outputs.get("error") or not trace_id:
                raise EvaluationError(f"Case {case.case_id} failed or returned no trace; inspect Session {session_id}")
            readback_deadline = time.monotonic() + 180.0
            read_evaluation_trace(trace_id, experiment_id=experiment_id, uc_location=uc_location)
            labels = case.record()["expectations"]
            try:
                label_trace(
                    trace_id,
                    review_status=case.review_status,
                    source_group=case.source_group,
                    expected_answer=", ".join(case.expected_values) or None,
                    expected_values=labels["expected_values"],
                    route=case.route,
                    critical=case.critical,
                    reviewer="synthetic-generator",
                )
            except Exception as exc:
                raise EvaluationError(
                    f"Expectation write failed ({type(exc).__name__}); recover trace {trace_id} without rerunning"
                ) from None
            expected = {
                "review_status": case.review_status,
                "source_group": case.source_group,
                "expected_answer": ", ".join(case.expected_values) or None,
                "expected_values": list(case.expected_values) or None,
                "route": case.route,
                "critical": case.critical,
            }
            expected = {key: value for key, value in expected.items() if value is not None}
            trace = read_evaluation_trace(
                trace_id,
                experiment_id=experiment_id,
                uc_location=uc_location,
                wait_seconds=max(0.0, readback_deadline - time.monotonic()),
                expected_labels=expected,
            )
            saved = labels_of(trace)
            if any(saved.get(key) != value for key, value in expected.items() if value is not None):
                raise EvaluationError(f"Expectations are not readable yet; recover trace {trace_id} without rerunning")
            if not collect_only:
                traces.append(trace)
            report(f"Case {case.case_id}: collected and labelled")
    if collect_only:
        return None
    return score_evaluation_traces(traces, judge_model=judge_model, judge_lm=judge_lm, jev_lm=jev_lm, report=report)


def score_evaluation_traces(
    traces: list[Any],
    *,
    judge_model: str,
    judge_lm: Any = None,
    jev_lm: Any = None,
    report: Callable[[str], None] = print,
) -> Any:
    """Score already verified, labelled traces without executing their Turns again."""
    try:
        result = mlflow.genai.evaluate(
            data=traces,
            scorers=[*build_mlflow_scorer_suite(judge_model, judge_lm, jev_lm), expected_values_present],
        )
    except Exception as exc:
        raise EvaluationError(
            f"Scoring failed ({type(exc).__name__}); score the saved traces without rerunning Turns"
        ) from None
    report(f"Evaluation scoring run: {result.run_id}")
    frame = result.result_df
    if frame is None:
        raise EvaluationError(f"Scoring incomplete; inspect evaluation run {result.run_id} and the saved traces")
    error_columns = [name for name in frame.columns if name == "error_message" or name.endswith("/error_message")]
    expected_columns = {f"{name.rsplit('/', 1)[0]}/value" for name in result.metrics}
    if (
        frame.empty
        or not expected_columns.issubset(frame.columns)
        or any(frame[name].notna().any() for name in error_columns)
    ):
        raise EvaluationError(f"Scoring incomplete; inspect evaluation run {result.run_id} and the saved traces")
    return result
