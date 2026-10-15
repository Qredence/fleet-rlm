"""Evaluation datasets, gold labels, and MLflow evaluation runs for Fleet Turns.

Cases are generated from the current Turn contract (attach files, send a task, receive an answer),
never from historical traces or stored Turns.
"""

from __future__ import annotations

import csv
import io
import json
import random
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import mlflow
from mlflow.entities import AssessmentSource, AssessmentSourceType, Feedback
from mlflow.genai.scorers import scorer

from fleet_rlm.engine.routes import Route

REVIEW_STATUSES = ("approved", "benchmark", "synthetic", "rejected")
_REGIONS = ("north", "south", "east", "west", "central")
_STATUSES = ("on_time", "late", "cancelled")
_DIRECT = (
    ("Convert 72 degrees Fahrenheit to Celsius, rounded to one decimal place.", "22.2"),
    ("What is 17 multiplied by 23?", "391"),
    ("How many days are there in a leap year?", "366"),
    ("What is the sum of the integers from 1 to 100?", "5050"),
    ("What is 15 percent of 240?", "36"),
    ("How many minutes are in 3.5 hours?", "210"),
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

    def record(self) -> dict[str, Any]:
        expectations: dict[str, Any] = {
            "expected_values": list(self.expected_values),
            "expected_facts": [f"The answer states {value}." for value in self.expected_values],
            "review_status": self.review_status,
            "source_group": self.source_group,
            "category": self.category,
            "answer_type": self.answer_type,
            "context_chars": self.context_chars or sum(len(text) for text in self.files.values()) or len(self.task),
            "benchmark": self.benchmark,
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
            category="direct",
            source_group=f"direct-{index:02d}",
            route=Route.DIRECT.value,
        )
        for index, (task, answer) in enumerate(_DIRECT)
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
    reviewer: str = "human",
) -> None:
    """Write gold labels onto a trace as MLflow expectations."""
    if review_status not in REVIEW_STATUSES:
        raise ValueError(f"review_status must be one of {REVIEW_STATUSES}")
    if not source_group.strip():
        raise ValueError("source_group must not be empty")
    labels: dict[str, Any] = {"review_status": review_status, "source_group": source_group}
    if expected_answer is not None:
        labels["expected_answer"] = expected_answer
    if expected_values:
        labels["expected_values"] = list(expected_values)
    if route:
        labels["route"] = route
    source_type = AssessmentSourceType.HUMAN if review_status != "synthetic" else AssessmentSourceType.CODE
    source = AssessmentSource(source_type=source_type, source_id=reviewer)
    for name, value in labels.items():
        mlflow.log_expectation(trace_id=trace_id, name=name, value=value, source=source)


def _parse_sse(text: str) -> list[dict[str, Any]]:
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
    events = _parse_sse(response.text)
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
def expected_values_present(outputs: Any, expectations: dict[str, Any]) -> Feedback:
    """Deterministic check: every gold value appears in the answer (thousands separators ignored)."""
    expected = (expectations or {}).get("expected_values") or []
    if not expected:
        return Feedback(value=None, rationale="No expected values labeled for this case.")
    answer = str(outputs.get("answer", "") if isinstance(outputs, dict) else outputs).lower().replace(",", "")
    # Boundaries stop "12" from matching inside "120" or "1.25".
    missing = [
        value for value in expected if not re.search(rf"(?<![\w.]){re.escape(str(value).lower())}(?![\w])", answer)
    ]
    return Feedback(
        value=not missing,
        rationale="All expected values found." if not missing else f"Missing from answer: {missing}",
    )


def evaluate_cases(
    cases: list[EvalCase],
    *,
    base_url: str,
    judge_model: str,
    session_prefix: str = "eval",
    timeout: float = 600.0,
) -> Any:
    """Run cases against a live server, label the resulting traces, and score them with the suite."""
    from fleet_rlm.optimize.scorers import build_mlflow_scorer_suite

    traces = []
    with httpx.Client(base_url=base_url, timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        for case in cases:
            outputs = run_case(client, case, f"{session_prefix}-{case.case_id}")
            if not outputs["trace_id"]:
                continue
            labels = case.record()["expectations"]
            label_trace(
                outputs["trace_id"],
                review_status=case.review_status,
                source_group=case.source_group,
                expected_values=labels["expected_values"],
                route=case.route,
                reviewer="synthetic-generator",
            )
            traces.append(mlflow.get_trace(outputs["trace_id"]))
    return mlflow.genai.evaluate(
        data=traces, scorers=[*build_mlflow_scorer_suite(judge_model), expected_values_present]
    )


def stopping_evidence(trace: Any) -> dict[str, Any] | None:
    """Project chronological task execution, excluding predictors and bootstrap code."""
    from fleet_rlm.observability import activity_preview, activity_text

    spans = trace.search_spans() if trace is not None else []
    roots = [span for span in spans if span.name == "fleet_turn"]
    if not roots or not isinstance(roots[0].inputs, dict):
        return None
    steps = []
    for span in sorted(spans, key=lambda item: getattr(item, "start_time_ns", 0)):
        if span.name != "sandbox.exec" or span.attributes.get("phase") != "task":
            continue
        inputs, outputs = span.inputs, span.outputs
        if not isinstance(inputs, dict) or not isinstance(outputs, dict) or not inputs.get("code"):
            return None
        steps.append({"code": activity_text(inputs["code"]), "observation": activity_preview(outputs)})
    if not steps:
        return None
    evidence = {"task": activity_text(str(roots[0].inputs.get("task", ""))), "steps": steps}
    # Do not silently discard steps and then judge an incomplete trajectory.
    return evidence if len(json.dumps(evidence)) <= 40_000 else None


def evaluate_stopping_traces(traces: list[Any], *, judge_model: str) -> Any:
    """Explicit stopping regression evaluation; never called on the request path."""
    from mlflow.genai.scorers import Guidelines

    judge = Guidelines(
        name="verification_stopping_discipline",
        model=judge_model,
        guidelines=(
            "Evaluate the chronological execution evidence. Once an answer and a supporting check agree, "
            "the next action should submit it. Further verification is justified when a check fails, "
            "results disagree, or the task explicitly requests independent verification. "
            "Do not treat correction after a Python error as unnecessary verification."
        ),
    )

    @scorer
    def verification_stopping_discipline(trace: Any) -> Feedback:
        evidence = stopping_evidence(trace)
        if evidence is None:
            return Feedback(value=None, rationale="Complete task execution evidence is unavailable.")
        return judge(inputs={"task": evidence["task"]}, outputs={"execution_steps": evidence["steps"]})

    @scorer
    def task_completed(trace: Any) -> Feedback:
        roots = [span for span in trace.search_spans() if span.name == "fleet_turn"]
        completed = bool(
            str(trace.info.state) == "OK"
            and roots
            and isinstance(roots[0].outputs, dict)
            and roots[0].outputs.get("answer")
        )
        return Feedback(value=completed, rationale="A successful turn must deliver an answer.")

    return mlflow.genai.evaluate(
        data=traces, scorers=[task_completed, expected_values_present, verification_stopping_discipline]
    )
