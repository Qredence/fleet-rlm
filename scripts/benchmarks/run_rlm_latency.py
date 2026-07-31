"""Benchmark live Fleet RLM latency and run the MLflow-native quality gate.

This script deliberately does not alter ``config/fleet.toml`` or restart Fleet.
Run it once per active configuration variant after restarting the API. Provider
execution requires ``FLEET_LIVE=1``; receipts contain bounded aggregates only.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv

RECEIPT_SCHEMA = "fleet.rlm-latency/v1"
DATASET_NAME = "fleet-rlm-latency-quality-v1"
DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_MLFLOW_URL = "http://127.0.0.1:5001"
_LIVE_VALUES = frozenset({"1", "true", "yes"})
_REPO_ROOT = Path(__file__).resolve().parents[2]

LATENCY_WORKLOAD = """Analyze the following evidence and decide whether the customer can
prevent renewal of OF-7781 effective 2025-04-01. Resolve conflicts by authority
and effective date. Explain the controlling deadline, receipt versus sending
date, conflicting sources, and residual uncertainty.

A1 Master Agreement: written non-renewal notice must be received at least 30
calendar days before renewal. The notice period begins when the other party
receives notice.
A2 Amendment 2: contracts executed after 2024-03-01 require 45 days' notice.
A3 OF-7781 was executed 2024-01-15 and does not incorporate Amendment 2.
A4 OF-7781 renews 2025-04-01 unless 30 calendar days' written notice is received.
A5 Account manager email says 45 days are required; it is advice, not an amendment.
A6 Legal memo says Amendment 2 does not govern OF-7781.
A7 CRM note says no written notice was found.
A8 Mailbox metadata records the customer's written notice sent 2025-02-27 and received 2025-02-28.
A9 Internal policy summary says 45 days but identifies its source system as unknown.
A10 Internal policy summaries are informational and non-binding.

Use Python for date arithmetic. Use selected independent sub-LM comparisons
only if useful. End with exactly one typed SUBMIT."""

QUALITY_RECORDS: tuple[dict[str, Any], ...] = (
    {
        "inputs": {"query": LATENCY_WORKLOAD},
        "expectations": {
            "expected_response": (
                "Yes. The 30-day receipt deadline is 2025-03-02; receipt on 2025-02-28 was timely. "
                "A1, A3, A4, A6, A8, and A10 control or corroborate; A2 does not apply and A5, A7, "
                "and A9 are overridden or non-binding."
            ),
            "required_evidence": ["A1", "A3", "A4", "A6", "A8", "A10"],
            "required_uncertainty": (
                "Delivery validity remains conditional on the mailbox evidence being authentic and contractually valid."
            ),
            "forbidden_claims": ["45-day rule controls", "sending date alone controls"],
        },
    },
    {
        "inputs": {
            "query": (
                "B1 requires receipt 20 days before 2026-01-31. B2 shows sending on 2026-01-10. "
                "B3 shows receipt on 2026-01-12. Determine timeliness and cite the controlling event."
            )
        },
        "expectations": {
            "expected_response": (
                "Not timely: the receipt deadline was 2026-01-11 and receipt on 2026-01-12 was one day late."
            ),
            "required_evidence": ["B1", "B3"],
            "required_uncertainty": "None beyond the stated dates.",
            "forbidden_claims": ["sending date controls", "notice was timely"],
        },
    },
    {
        "inputs": {
            "query": (
                "C1 signed policy requires manager approval above $50,000. C2 draft FAQ says approval is optional. "
                "C3 board resolution makes C1 binding. C4 request is $72,000. Decide whether approval is required "
                "and resolve the conflict."
            )
        },
        "expectations": {
            "expected_response": (
                "Approval is required because the binding C1/C3 chain controls and $72,000 exceeds $50,000; "
                "draft C2 is non-binding."
            ),
            "required_evidence": ["C1", "C3", "C4"],
            "required_uncertainty": "Conditional on C1 and C3 remaining in force.",
            "forbidden_claims": ["C2 controls", "approval is optional"],
        },
    },
    {
        "inputs": {
            "query": (
                "D1 contains approved amounts 120, 80, and 45. D2 contains draft amounts 900 and 700 that must "
                "be excluded. D3 adds an approved credit of -15. Compute the approved net total and identify "
                "excluded evidence."
            )
        },
        "expectations": {
            "expected_response": "The approved net total is 230: 120 + 80 + 45 - 15. D2's draft values are excluded.",
            "required_evidence": ["D1", "D2", "D3"],
            "required_uncertainty": "None beyond the classification supplied.",
            "forbidden_claims": ["1830", "include D2"],
        },
    },
    {
        "inputs": {
            "query": (
                "E1 says access is allowed only after security approval. "
                "E2 records approval requested but no decision. "
                "E3 is an unverified chat saying approval probably happened. Decide whether access is currently "
                "authorized."
            )
        },
        "expectations": {
            "expected_response": (
                "The record is insufficient to establish authorization. E1 requires approval, E2 has no decision, "
                "and E3 is unverified."
            ),
            "required_evidence": ["E1", "E2", "E3"],
            "required_uncertainty": "Authorization is conditional on obtaining verified approval evidence.",
            "forbidden_claims": ["access is authorized", "E3 proves approval"],
        },
    },
)


class BenchmarkError(RuntimeError):
    """A live benchmark precondition or Turn contract failed."""


def percentile(values: Sequence[float], percentile_value: int) -> float:
    """Return a deterministic nearest-rank percentile."""
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0 < percentile_value <= 100:
        raise ValueError("percentile must be in (0, 100]")
    ordered = sorted(float(value) for value in values)
    rank = max(1, (percentile_value * len(ordered) + 99) // 100)
    return ordered[min(rank, len(ordered)) - 1]


def latency_gate(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the approved performance gate to two aggregate receipts."""
    baseline_p50 = float(baseline["end_to_end_ms"]["p50"])
    candidate_p50 = float(candidate["end_to_end_ms"]["p50"])
    checks = {
        "p50_reduced_by_20_percent": candidate_p50 <= baseline_p50 * 0.8,
        "p95_not_worse": float(candidate["end_to_end_ms"]["p95"]) <= float(baseline["end_to_end_ms"]["p95"]),
        "error_rate_not_worse": float(candidate["error_rate"]) <= float(baseline["error_rate"]),
        "quality_complete": bool(candidate.get("quality_complete")),
    }
    return {"passed": all(checks.values()), "checks": checks}


def quality_gate(evaluation: Mapping[str, Any]) -> bool:
    """Accept only a complete five-record evaluation with perfect two-judge means."""
    if evaluation.get("dry_run") or int(evaluation.get("records", 0)) != len(QUALITY_RECORDS):
        return False
    metrics = evaluation.get("metrics")
    if not isinstance(metrics, Mapping):
        return False

    def score(prefix: str) -> float | None:
        values = [
            float(value)
            for key, value in metrics.items()
            if str(key).startswith(prefix) and str(key).endswith(("/mean", "_mean"))
        ]
        return values[0] if values else None

    return score("correctness") == 1.0 and score("evidence_coverage") == 1.0


def _require_live() -> None:
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        raise BenchmarkError("FLEET_LIVE=1 is required for provider-backed execution")


def _load_repository_env() -> None:
    load_dotenv(_REPO_ROOT / ".env", override=False)


def _configure_judge_environment(judge_model: str) -> None:
    """Bridge explicit Fleet Databricks policy to MLflow's OpenAI adapter."""
    if not judge_model.startswith("openai:/"):
        return
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    base_url = os.environ.get("FLEET_DATABRICKS_AI_GATEWAY_BASE_URL", "").strip()
    if not token or not base_url:
        raise BenchmarkError("judge provider credentials are unavailable")
    os.environ.setdefault("OPENAI_API_KEY", token)
    os.environ.setdefault("OPENAI_API_BASE", base_url)
    os.environ.setdefault("OPENAI_BASE_URL", base_url)


def _sse_chunks(response: httpx.Response) -> Iterator[dict[str, Any]]:
    for line in response.iter_lines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _structured_answer(chunk: Mapping[str, Any]) -> str | None:
    data = chunk.get("data")
    if not isinstance(data, Mapping):
        return None
    value = data.get("value")
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("answer"), str):
        return str(value["answer"])
    return None


def _termination_mode_from_chunk(chunk: Mapping[str, Any]) -> str | None:
    """Derive termination only from explicit stream evidence."""
    chunk_type = chunk.get("type")
    if (
        chunk_type == "data-rlm-output"
        and isinstance(chunk.get("data"), Mapping)
        and chunk["data"].get("output") == "FINAL submitted"
    ):
        return "typed_submit"
    if chunk_type == "reasoning-delta" and chunk.get("delta") == "Extract forced final output":
        return "native_extraction_fallback"
    return None


def run_turn(client: httpx.Client, query: str, *, nonce: str) -> dict[str, Any]:
    """Execute one real Fleet Turn and return bounded operational results."""
    session = client.post("/api/sessions", json={"title": f"latency-{nonce}"})
    session.raise_for_status()
    session_id = str(session.json()["id"])
    prompt = f"{query}\n\nBenchmark nonce: {nonce}. It has no semantic meaning."
    answer_parts: list[str] = []
    answer: str | None = None
    usage: dict[str, Any] = {}
    trace_id: str | None = None
    run_id: str | None = None
    iterations = 0
    batch_calls = 0
    recursive_calls = 0
    first_event_ms: float | None = None
    termination_mode: str | None = None
    started = time.perf_counter()
    with client.stream(
        "POST",
        f"/api/sessions/{session_id}/turns",
        json={"text": prompt, "attachment_ids": [], "skill_selections": []},
        headers={"Idempotency-Key": f"rlm-latency-{uuid4()}"},
    ) as response:
        response.raise_for_status()
        for chunk in _sse_chunks(response):
            if first_event_ms is None:
                first_event_ms = (time.perf_counter() - started) * 1000
            metadata = chunk.get("messageMetadata")
            if isinstance(metadata, Mapping):
                if isinstance(metadata.get("traceId"), str):
                    trace_id = str(metadata["traceId"])
                if isinstance(metadata.get("runId"), str):
                    run_id = str(metadata["runId"])
            chunk_type = chunk.get("type")
            termination_mode = _termination_mode_from_chunk(chunk) or termination_mode
            if chunk_type == "text-delta" and isinstance(chunk.get("delta"), str):
                answer_parts.append(str(chunk["delta"]))
            elif chunk_type == "data-structured-result":
                answer = _structured_answer(chunk) or answer
            elif chunk_type == "data-usage" and isinstance(chunk.get("data"), Mapping):
                raw_usage = chunk["data"].get("usage")
                if isinstance(raw_usage, Mapping):
                    usage = dict(raw_usage)
                    iterations = int(usage.get("iterations", 0) or 0)
            elif chunk_type == "tool-input-available":
                tool_name = chunk.get("toolName")
                batch_calls += int(tool_name == "llm_query_batched")
                recursive_calls += int(tool_name == "rlm_query")
            elif chunk_type in {"error", "abort"}:
                raise BenchmarkError(str(chunk.get("errorText") or chunk.get("reason") or "Turn failed"))
            elif chunk_type == "finish" and chunk.get("finishReason") != "stop":
                raise BenchmarkError("Turn did not finish with stop")
    return {
        "answer": answer if answer is not None else "".join(answer_parts),
        "trace_id": trace_id,
        "run_id": run_id,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "first_event_ms": round(first_event_ms if first_event_ms is not None else -1.0, 3),
        "usage": usage,
        "iterations": iterations,
        "batch_calls": batch_calls,
        "recursive_calls": recursive_calls,
        "termination_mode": termination_mode,
    }


def _active_policy(client: httpx.Client) -> dict[str, Any]:
    response = client.get("/api/settings")
    response.raise_for_status()
    payload = response.json()
    profile = payload.get("active_profile")
    scope = next(
        (item for item in payload.get("scopes", []) if isinstance(item, Mapping) and item.get("name") == profile),
        None,
    )
    if not isinstance(profile, str) or not isinstance(scope, Mapping):
        raise BenchmarkError("Fleet settings do not expose the active profile")
    fields = {
        str(item["path"]): item.get("value")
        for item in scope.get("fields", [])
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }
    return {
        "profile": profile,
        "root_model": fields.get("llm.root.model"),
        "root_max_tokens": fields.get("llm.root.max_tokens"),
        "root_reasoning_effort": fields.get("llm.root.reasoning_effort"),
        "sub_model": fields.get("llm.sub.model"),
    }


def _execution_trace_id(mlflow_url: str, experiment_id: str, run_id: str) -> str | None:
    """Resolve the execution trace when preparation is traced separately."""
    import mlflow

    mlflow.set_tracking_uri(mlflow_url)
    for _attempt in range(20):
        traces = mlflow.search_traces(
            locations=[experiment_id],
            filter_string=f"tag.`fleet.run_id` = '{run_id}'",
            return_type="list",
        )
        for trace in traces:
            if any(span.name == "RLM.execute" for span in trace.data.spans):
                return str(trace.info.trace_id)
        time.sleep(0.25)
    return None


def _tag_trace(mlflow_url: str, trace_id: str, *, workload_id: str, variant: str, sample: str) -> None:
    import mlflow

    mlflow.set_tracking_uri(mlflow_url)
    client = mlflow.MlflowClient()
    client.set_trace_tag(trace_id, "fleet.workload_id", workload_id)
    client.set_trace_tag(trace_id, "fleet.perf_variant", variant)
    client.set_trace_tag(trace_id, "fleet.sample_kind", sample)


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    measured = [row for row in rows if row.get("sample_kind") == "measured"]
    durations = [float(row["duration_ms"]) for row in measured]
    successes = [row for row in measured if not row.get("error_category")]
    first_events = [float(row["first_event_ms"]) for row in successes if float(row["first_event_ms"]) >= 0]
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "cache_read_tokens": 0}
    for row in successes:
        for key, value in _usage_totals(row.get("usage")).items():
            usage[key] += value
    return {
        "sample_count": len(measured),
        "end_to_end_ms": {
            "mean": round(statistics.fmean(durations), 3) if durations else None,
            "p50": round(percentile(durations, 50), 3) if durations else None,
            "p95": round(percentile(durations, 95), 3) if durations else None,
        },
        "first_runtime_event_ms": {
            "p50": round(percentile(first_events, 50), 3) if first_events else None,
            "p95": round(percentile(first_events, 95), 3) if first_events else None,
        },
        "error_rate": 1.0 - (len(successes) / len(measured)) if measured else 1.0,
        "iterations": sum(int(row.get("iterations", 0)) for row in successes),
        "batch_calls": sum(int(row.get("batch_calls", 0)) for row in successes),
        "recursive_calls": sum(int(row.get("recursive_calls", 0)) for row in successes),
        "typed_submit_count": sum(row.get("termination_mode") == "typed_submit" for row in successes),
        "token_totals": usage,
        "trace_ids": [row["trace_id"] for row in successes if row.get("trace_id")],
        "quality_complete": False,
    }


def _usage_totals(value: object) -> dict[str, int]:
    """Flatten only the four approved operational token counters."""
    result = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "cache_read_tokens": 0}

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if isinstance(child, int) and not isinstance(child, bool):
                    if key in {"prompt_tokens", "input_tokens"}:
                        result["prompt_tokens"] += child
                    elif key in {"completion_tokens", "output_tokens"}:
                        result["completion_tokens"] += child
                    elif key == "reasoning_tokens":
                        result["reasoning_tokens"] += child
                    elif key in {
                        "cached_tokens",
                        "cache_read_input_tokens",
                        "cache_read_tokens",
                        "prompt_cache_hit_tokens",
                    }:
                        result["cache_read_tokens"] += child
                elif isinstance(child, (Mapping, list, tuple)):
                    visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return result


def _metrics_query(mlflow_url: str, experiment_id: str, *, variant: str) -> dict[str, Any]:
    common = {
        "experiment_ids": [experiment_id],
        "view_type": 2,
        "metric_name": "latency",
        "aggregations": [
            {"aggregation_type": 4, "percentile_value": 50},
            {"aggregation_type": 4, "percentile_value": 95},
        ],
        "dimensions": ["span_status"],
        "max_results": 100,
    }
    results: dict[str, Any] = {}
    for span_name in ("fleet_turn", "RLM.root_lm", "tool.llm_query_batched"):
        payload = {
            **common,
            "filters": [
                'trace.tag.fleet.workload_id = "evidence-conflict-v1"',
                f'trace.tag.fleet.perf_variant = "{variant}"',
                f'span.name = "{span_name}"',
            ],
        }
        response = httpx.post(
            f"{mlflow_url.rstrip('/')}/api/3.0/mlflow/traces/metrics",
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        results[span_name] = response.json()
    return results


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    _require_live()
    rows: list[dict[str, Any]] = []
    with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=httpx.Timeout(args.timeout)) as client:
        policy = _active_policy(client)
        for index in range(args.warmups + args.runs):
            sample = "warmup" if index < args.warmups else "measured"
            nonce = f"{args.variant}-{sample}-{uuid4()}"
            sample_started = time.perf_counter()
            try:
                row = run_turn(client, LATENCY_WORKLOAD, nonce=nonce)
                if row.get("run_id"):
                    execution_trace_id = _execution_trace_id(
                        args.mlflow_url,
                        args.experiment_id,
                        str(row["run_id"]),
                    )
                    row["trace_id"] = execution_trace_id
                if row.get("trace_id"):
                    _tag_trace(
                        args.mlflow_url,
                        str(row["trace_id"]),
                        workload_id="evidence-conflict-v1",
                        variant=args.variant,
                        sample=sample,
                    )
            except Exception as exc:
                row = {
                    "duration_ms": round((time.perf_counter() - sample_started) * 1000, 3),
                    "first_event_ms": -1.0,
                    "error_category": type(exc).__name__,
                    "trace_id": None,
                }
            row["sample_kind"] = sample
            rows.append(row)
    aggregate = _aggregate(rows)
    metrics: dict[str, Any]
    try:
        metrics = _metrics_query(args.mlflow_url, args.experiment_id, variant=args.variant)
    except Exception as exc:
        metrics = {"status": "unavailable", "error_category": type(exc).__name__}
    return {
        "schema": RECEIPT_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "variant": args.variant,
        "active_policy": policy,
        "warmups": args.warmups,
        "aggregate": aggregate,
        "mlflow_span_metrics": metrics,
    }


def prepare_evaluation(args: argparse.Namespace) -> dict[str, Any]:
    """Idempotently create the MLflow dataset and register its two judges."""
    import mlflow
    from mlflow.genai import datasets
    from mlflow.genai.judges import make_judge
    from mlflow.genai.scorers import Correctness, list_scorers

    if not args.judge_model:
        raise BenchmarkError("prepare-evaluation requires --judge-model with an MLflow-supported model URI")
    mlflow.set_tracking_uri(args.mlflow_url)
    mlflow.set_experiment(experiment_id=args.experiment_id)
    existing = [item for item in datasets.search_datasets([args.experiment_id]) if item.name == DATASET_NAME]
    if existing:
        dataset = existing[0]
    else:
        dataset = datasets.create_dataset(name=DATASET_NAME, experiment_id=args.experiment_id)
        dataset.merge_records(list(QUALITY_RECORDS))

    registered = {
        scorer.name: getattr(scorer, "model", None) for scorer in list_scorers(experiment_id=args.experiment_id)
    }
    if registered.get("correctness") != args.judge_model:
        Correctness(model=args.judge_model).register(experiment_id=args.experiment_id)
    if registered.get("evidence_coverage") != args.judge_model:
        make_judge(
            name="evidence_coverage",
            model=args.judge_model,
            feedback_value_type=bool,
            instructions=(
                "Evaluate {{ outputs }} against {{ expectations }}. Return true only when the conclusion is "
                "supported, every required_evidence identifier is used materially, required_uncertainty is "
                "preserved, and no forbidden_claims are asserted. Otherwise return false."
            ),
        ).register(experiment_id=args.experiment_id)
    return {"dataset_id": dataset.dataset_id, "dataset_name": dataset.name, "records": len(dataset.to_df())}


def run_evaluation(args: argparse.Namespace) -> dict[str, Any]:
    """Run MLflow GenAI evaluation against three dry-run or all five records."""
    _require_live()
    if not args.judge_model:
        raise BenchmarkError("evaluate requires --judge-model with an MLflow-supported model URI")
    _configure_judge_environment(args.judge_model)
    import mlflow
    from mlflow.genai import datasets
    from mlflow.genai.scorers import get_scorer

    mlflow.set_tracking_uri(args.mlflow_url)
    mlflow.set_experiment(experiment_id=args.experiment_id)
    dataset = datasets.get_dataset(name=DATASET_NAME)
    frame = dataset.to_df().head(3) if args.dry_run else dataset.to_df()

    def predict_fn(query: str) -> str:
        with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=httpx.Timeout(args.timeout)) as client:
            return str(run_turn(client, query, nonce=f"quality-{uuid4()}")["answer"])

    result = mlflow.genai.evaluate(
        data=frame,
        predict_fn=predict_fn,
        scorers=[
            get_scorer(name="correctness", experiment_id=args.experiment_id),
            get_scorer(name="evidence_coverage", experiment_id=args.experiment_id),
        ],
    )
    metrics = {str(key): value for key, value in result.metrics.items()}
    receipt = {"dataset_name": DATASET_NAME, "dry_run": args.dry_run, "records": len(frame), "metrics": metrics}
    receipt["quality_complete"] = quality_gate(receipt)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("benchmark", "prepare-evaluation", "evaluate", "compare"))
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--mlflow-url", default=DEFAULT_MLFLOW_URL)
    parser.add_argument("--experiment-id", default="1")
    parser.add_argument("--variant", default="baseline")
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=2_000.0)
    parser.add_argument("--judge-model", help="MLflow-supported judge URI, for example databricks:/<endpoint>")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--quality", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _load_repository_env()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "benchmark":
            if args.warmups < 0 or args.runs < 1:
                raise BenchmarkError("warmups must be nonnegative and runs must be positive")
            receipt = run_benchmark(args)
        elif args.command == "prepare-evaluation":
            receipt = prepare_evaluation(args)
        elif args.command == "evaluate":
            receipt = run_evaluation(args)
        else:
            if args.baseline is None or args.candidate is None:
                raise BenchmarkError("compare requires --baseline and --candidate")
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))["aggregate"]
            candidate = json.loads(args.candidate.read_text(encoding="utf-8"))["aggregate"]
            if args.quality is not None:
                candidate["quality_complete"] = quality_gate(json.loads(args.quality.read_text(encoding="utf-8")))
            receipt = latency_gate(baseline, candidate)
    except Exception as exc:
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "command": args.command,
            "status": "failed",
            "error_category": type(exc).__name__,
        }
        exit_code = 1
    else:
        exit_code = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
