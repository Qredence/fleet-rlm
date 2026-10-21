"""Notebook helpers for external task benchmarks against an existing Fleet API.

Dataset loading, selection and official verifiers belong to the notebooks. This
module only freezes tasks, collects receipts and evaluates saved Fleet traces.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from fleet_rlm.engine.signatures import parse_route
from fleet_rlm.optimize.artifacts import atomic_write
from fleet_rlm.optimize.evaluation import parse_sse
from fleet_rlm.workspace import validate_session_id


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _same_destination(recorded: dict, expected: dict) -> bool:
    """Compare only the destination keys the caller expects, ignoring recorded extras."""
    return expected == {key: (recorded or {}).get(key) for key in expected}


def save_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, indent=2, allow_nan=False).encode() + b"\n")


def freeze_tasks(directory: Path, cases: list[dict], metadata: dict) -> dict:
    """Freeze the exact inputs and host-only expectations once, before execution."""
    if not cases or len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("A benchmark requires nonempty, unique case IDs")
    for case in cases:
        validate_session_id(case["case_id"])
        if not isinstance(case["task"], str) or not case["task"].strip():
            raise ValueError("Each task must be nonempty text")
        for name, text in case.get("files", {}).items():
            if Path(name).name != name or not isinstance(text, str):
                raise ValueError("Attachment names must be basenames with text content")
    revision = metadata.get("dataset_revision", "")
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        raise ValueError("Resolve the HF dataset to a full commit SHA before freezing")
    if not isinstance(metadata.get("dataset"), str) or not metadata["dataset"].strip():
        raise ValueError("Metadata must name the dataset")
    payload = {"metadata": metadata, "cases": cases}
    bundle = {**payload, "digest": _digest(payload)}
    directory.mkdir(parents=True, exist_ok=False)
    save_json(directory / "bundle.json", bundle)
    return bundle


def load_tasks(directory: Path) -> dict:
    bundle = json.loads((directory / "bundle.json").read_text())
    if bundle["digest"] != _digest({key: bundle[key] for key in ("metadata", "cases")}):
        raise ValueError("Frozen tasks changed")
    return bundle


def _health(client: httpx.Client) -> dict:
    response = client.get("/health", timeout=15)
    response.raise_for_status()
    health = response.json()
    if health.get("status") != "ok" or not health.get("mlflow", {}).get("ready"):
        raise RuntimeError("Fleet and its configured MLflow destination must be ready")
    return health


def _turn(client: httpx.Client, session: str, case: dict) -> dict:
    response = client.post(f"/sessions/{session}/turn", json={"task": case["task"], "idempotency_key": case["case_id"]})
    response.raise_for_status()
    events = parse_sse(response.text)
    usage = [
        {key: event[key] for key in ("model", "usage", "duration_ms", "ok", "error_type") if key in event}
        for event in events
        if event.get("event") == "lm_end"
    ]
    outcome = next(
        (event for event in reversed(events) if event.get("event") in {"turn_complete", "error", "turn_cancelled"}),
        {},
    )
    payload = outcome.get("payload", {})
    turn_id = outcome.get("turn_id") or payload.get("turn_id") or payload.get("request_id")
    result = {"status": "uncertain", "usage": usage, "turn_id": turn_id, "output": payload}
    if turn_id:
        response = client.get(f"/sessions/{session}/turns/{turn_id}", timeout=15)
        response.raise_for_status()
        durable = response.json()
        result["durable_outcome"] = durable
        if durable.get("status") == "completed" and outcome.get("event") == "turn_complete":
            matches = all(
                durable.get("result", {}).get(key) == payload.get(key) for key in ("answer", "route", "trace_id")
            )
            if matches and payload.get("trace_id") and parse_route(payload.get("route")) is not None:
                result["status"] = "completed"
        elif durable.get("status") in {"failed", "cancelled"}:
            result["status"] = "failed"
    errors = [str(call.get("error_type", "")) for call in usage if call.get("ok") is False]
    # A completed Turn may tolerate an optional LM failure; only an incomplete outcome stops the run.
    if result["status"] != "completed":
        if any("RateLimit" in error for error in errors):
            result["stop_reason"] = "provider_rate_limit"
        if any(word in error for error in errors for word in ("Connection", "Service", "Unavailable")):
            result["stop_reason"] = "provider_service_failure"
    return result


def run_tasks(
    bundle_dir: Path,
    run_dir: Path,
    *,
    base_url: str,
    expected_destination: dict,
    allow_live: bool = False,
    timeout: float = 1200,
) -> dict:
    """Run each case once; incomplete receipts block continuation without replay."""
    if not allow_live:
        raise ValueError("Set allow_live=True deliberately to spend model/Daytona resources")
    if any(not expected_destination.get(key) for key in ("mode", "tracking_uri", "experiment_name")):
        raise ValueError("Expected destination must identify the mode, tracking URI and experiment")
    bundle = load_tasks(bundle_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    identity = {
        "bundle_digest": bundle["digest"],
        "base_url": base_url.rstrip("/"),
        "expected_destination": expected_destination,
    }
    run_path = run_dir / "run.json"
    run = json.loads(run_path.read_text()) if run_path.exists() else {**identity, "run_id": uuid.uuid4().hex}
    if any(run[key] != value for key, value in identity.items()):
        raise ValueError("Run directory belongs to different frozen tasks or API")
    # A crash leaves this marker and an uncertain receipt. Never steal a live run's lock.
    lock = run_dir / ".running"
    with lock.open("x"):
        pass
    try:
        run["status"] = "incomplete"
        save_json(run_path, run)
        with httpx.Client(base_url=base_url, timeout=httpx.Timeout(timeout, connect=10)) as client:
            for case in bundle["cases"]:
                receipt_path = run_dir / "receipts" / f"{case['case_id']}.json"
                if receipt_path.exists():
                    old = json.loads(receipt_path.read_text())
                    if old["status"] == "completed" and old.get("cleanup") == "released" and not old.get("stop_reason"):
                        continue
                    raise RuntimeError(f"Inspect existing receipt before continuing; never replay: {receipt_path}")
                session = f"bench-{uuid.uuid4().hex}"
                receipt = {"case_id": case["case_id"], "session_id": session, "status": "preflight_failed"}
                started = time.monotonic()
                created_runtime = False
                try:
                    health = _health(client)
                    run.setdefault("service", health)
                    if not _same_destination(health["mlflow"], expected_destination):
                        raise RuntimeError("Fleet tracking destination differs from the notebook settings")
                    if run["service"]["mlflow"] != health["mlflow"]:
                        raise RuntimeError("Fleet tracking destination changed during the run")
                    created_runtime = True  # Even a failed config read can have allocated a runtime.
                    response = client.get(f"/sessions/{session}/config")
                    response.raise_for_status()
                    config = response.json()
                    digest = config.get("configuration", {}).get("digest")
                    if not digest:
                        raise RuntimeError("Fleet did not report an effective configuration digest")
                    run.setdefault("configuration_digest", digest)
                    if run["configuration_digest"] != digest:
                        raise RuntimeError("Effective Fleet configuration changed during the run")
                    receipt.update(configuration=config, configuration_digest=digest)
                    save_json(run_path, run)
                    save_json(receipt_path, receipt)
                    for name, text in case.get("files", {}).items():
                        response = client.post(
                            f"/sessions/{session}/attach", files={"file": (name, text.encode(), "text/plain")}
                        )
                        response.raise_for_status()
                    receipt["status"] = "uncertain"
                    save_json(receipt_path, receipt)  # Persist before the first possible model call.
                    receipt.update(_turn(client, session, case))
                    _health(client)
                except Exception as exc:
                    receipt["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
                    receipt["stop_reason"] = "transport_or_service_failure"
                finally:
                    receipt["latency_seconds"] = time.monotonic() - started
                    if created_runtime:
                        try:
                            response = client.delete(f"/sessions/{session}", timeout=120)
                            receipt["cleanup"] = "released" if response.status_code in {200, 404} else "unresolved"
                        except httpx.HTTPError:
                            receipt["cleanup"] = "unresolved"
                    save_json(receipt_path, receipt)
                if receipt["status"] in {"uncertain", "preflight_failed"} or receipt.get("stop_reason"):
                    raise RuntimeError(f"Benchmark stopped; inspect {receipt_path}")
                if receipt.get("cleanup") != "released":
                    raise RuntimeError(f"Session cleanup unresolved; inspect {receipt_path}")
        run["status"] = "finished"
        return run
    finally:
        save_json(run_path, run)
        lock.unlink()


def score_tasks(bundle_dir: Path, run_dir: Path, official_score: Callable[[dict, str], float]) -> dict:
    """Score saved answers offline, including explicit failures and unattempted cases."""
    rows = []
    usage: dict[str, Counter] = defaultdict(Counter)
    bundle = load_tasks(bundle_dir)
    run = json.loads((run_dir / "run.json").read_text())
    if run["bundle_digest"] != bundle["digest"]:
        raise ValueError("Run directory belongs to different frozen tasks")
    for case in bundle["cases"]:
        path = run_dir / "receipts" / f"{case['case_id']}.json"
        receipt = json.loads(path.read_text()) if path.exists() else {"status": "unattempted"}
        row = {"case_id": case["case_id"], **receipt}
        row["trace_id"] = receipt.get("output", {}).get("trace_id")
        destination = run.get("service", {}).get("mlflow", {})
        host = destination.get("workspace_host") or destination.get("tracking_uri", "")
        if row["trace_id"] and host.startswith(("http://", "https://")):
            page = "/ml/experiments/" if destination.get("mode") == "databricks" else "/#/experiments/"
            row["trace_url"] = (
                f"{host.rstrip('/')}{page}{destination['experiment_id']}/traces"
                f"?selectedTraceId={quote(row['trace_id'], safe='')}"
            )
        if receipt["status"] == "completed":
            try:
                score = float(official_score(case, receipt["output"].get("answer", "")))
                if not math.isfinite(score) or not 0 <= score <= 1:
                    raise ValueError("Verifier score must be between zero and one")
                row["official_score"] = score
            except Exception as exc:
                row["score_error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        for call in receipt.get("usage", []):
            for key, value in (call.get("usage") or {}).items():
                if type(value) in (int, float):
                    usage[call.get("model", "unknown")][key] += value
        rows.append(row)
    completed = [row for row in rows if row["status"] == "completed"]
    scored = [row for row in completed if "official_score" in row]
    attempted = [row for row in rows if "latency_seconds" in row]
    correct = sum(row["official_score"] == 1 for row in scored)
    summary = {
        "bundle_digest": bundle["digest"],
        "configuration_digest": run.get("configuration_digest"),
        "expected": len(rows),
        "completed": len(completed),
        "scored": len(scored),
        "correct": correct,
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "completed_case_accuracy": correct / len(completed) if completed and len(scored) == len(completed) else None,
        "all_selected_case_accuracy": correct / len(rows),
        "mean_official_score_scored": (sum(row["official_score"] for row in scored) / len(scored) if scored else None),
        "total_latency_seconds": sum(row["latency_seconds"] for row in attempted),
        "mean_latency_seconds": (
            sum(row["latency_seconds"] for row in attempted) / len(attempted) if attempted else None
        ),
        "reported_provider_usage": dict(usage),
        "usage_calls_without_tokens": sum(not call.get("usage") for row in rows for call in row.get("usage", [])),
        "cases_without_usage": sum(not row.get("usage") for row in rows if row["status"] != "unattempted"),
        "score_errors": sum("score_error" in row for row in rows),
        "complete": run["status"] == "finished" and len(scored) == len(rows),
        "cases": rows,
    }
    save_json(run_dir / "scores.json", summary)
    return summary


def evaluate_saved_tasks(bundle_dir: Path, run_dir: Path, settings: Any, *, allow_judges: bool = False) -> Any:
    """Apply Fleet's native suite and saved official scores; partial runs are recorded, never called complete."""
    if not allow_judges:
        raise ValueError("Set allow_judges=True deliberately to spend evaluation model resources")
    import mlflow
    import pandas as pd
    from mlflow.entities import Feedback
    from mlflow.genai.scorers import scorer

    from fleet_rlm.observability import configure_mlflow_observability
    from fleet_rlm.optimize.evaluation import label_trace, read_evaluation_trace
    from fleet_rlm.optimize.scorers import build_mlflow_scorer_suite

    bundle = load_tasks(bundle_dir)
    run = json.loads((run_dir / "run.json").read_text())
    scored = json.loads((run_dir / "scores.json").read_text())
    if run["bundle_digest"] != bundle["digest"] or scored["bundle_digest"] != bundle["digest"]:
        raise ValueError("Saved scores belong to different frozen tasks")
    recorded = (run.get("service") or {}).get("mlflow")
    if not recorded:
        raise ValueError("Run has no recorded Fleet destination; it stopped before the preflight check")
    target = settings.mlflow_target()
    descriptor = settings.mlflow_descriptor()
    if not _same_destination(recorded, descriptor):
        raise ValueError("Notebook tracking settings differ from the recorded Fleet destination")
    if not target.eval_experiment_name:
        raise ValueError("Configure Fleet's evaluation experiment first")
    settings.prepare_mlflow_auth()
    configure_mlflow_observability(
        target.eval_experiment_name,
        target.tracking_uri,
        trace_catalog=settings.mlflow_trace_catalog,
        trace_schema=settings.mlflow_trace_schema,
        trace_table_prefix=settings.mlflow_eval_trace_table_prefix,
        tracing_sql_warehouse_id=settings.mlflow_tracing_sql_warehouse_id,
    )
    for name, value in settings.judge_environment().items():
        os.environ[name] = value
    cases = {case["case_id"]: case for case in bundle["cases"]}
    trace_location = descriptor.get("trace_location")
    uc_location = (
        tuple(trace_location[key] for key in ("catalog", "schema", "table_prefix")) if trace_location else None
    )
    traces, scores_by_trace = [], {}
    for row in scored["cases"]:
        if "official_score" not in row:
            continue
        trace = read_evaluation_trace(
            row["trace_id"], experiment_id=run["service"]["mlflow"]["experiment_id"], uc_location=uc_location
        )
        case = cases[row["case_id"]]
        label_trace(
            row["trace_id"],
            review_status="benchmark",
            source_group=case.get("source_group", case["case_id"]),
            expected_answer=case.get("expected_answer"),
            reviewer=bundle["metadata"]["dataset"],
        )
        # Read labels back through the existing delivery contract before Fleet judges use them.
        trace = read_evaluation_trace(
            row["trace_id"],
            experiment_id=run["service"]["mlflow"]["experiment_id"],
            uc_location=uc_location,
            expected_labels={
                key: value
                for key, value in {
                    "review_status": "benchmark",
                    "source_group": case.get("source_group", case["case_id"]),
                    "expected_answer": case.get("expected_answer"),
                }.items()
                if value is not None
            },
        )
        traces.append(trace)
        scores_by_trace[trace.info.trace_id] = row["official_score"]

    @scorer(name="benchmark_correctness")
    def benchmark_correctness(trace: Any) -> Feedback:
        return Feedback(value=scores_by_trace[trace.info.trace_id], rationale="Pinned official benchmark verifier")

    if not traces:
        raise ValueError("No completed, officially scored traces to evaluate")
    coverage = {key: scored[key] for key in ("expected", "completed", "scored")}
    with mlflow.start_run(run_name=f"benchmark-{run['run_id']}"):
        mlflow.set_tags(
            {
                "fleet.workload": "benchmark",
                "benchmark.dataset": bundle["metadata"]["dataset"],
                **{f"benchmark.{key}": str(value) for key, value in coverage.items()},
            }
        )
        mlflow.log_dict(bundle["metadata"], "benchmark/source.json")
        mlflow.log_dict(scored, "benchmark/scores.json")
        mlflow.log_input(
            mlflow.data.from_pandas(
                pd.DataFrame([{"case_id": case["case_id"], "task": case["task"]} for case in bundle["cases"]]),
                name=bundle["metadata"]["dataset"],
                source=bundle["metadata"].get("dataset_url"),
            ),
            context="benchmark",
        )
        result = mlflow.genai.evaluate(
            data=traces,
            scorers=[
                *build_mlflow_scorer_suite(settings.fleet_judge_model, settings.build_judge_lm()),
                benchmark_correctness,
            ],
        )
        frame = result.result_df
        error_columns = (
            []
            if frame is None
            else [name for name in frame.columns if name == "error_message" or name.endswith("/error_message")]
        )
        scoring_complete = (
            frame is not None and not frame.empty and not any(frame[name].notna().any() for name in error_columns)
        )
        save_json(
            run_dir / "evaluation.json",
            {
                "run_id": result.run_id,
                "metrics": result.metrics,
                "complete": scoring_complete and bool(scored["complete"]),
                "scoring_complete": scoring_complete,
                **coverage,
            },
        )
        if not scoring_complete:
            raise RuntimeError(
                f"Evaluation incomplete; inspect saved MLflow run {result.run_id} without rerunning tasks"
            )
        return result
