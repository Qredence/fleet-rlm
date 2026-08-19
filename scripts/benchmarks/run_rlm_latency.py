"""Benchmark live Fleet RLM latency and run the MLflow-native quality gate.

This script deliberately does not alter ``config/fleet.toml`` or restart Fleet.
Run it once per active configuration variant after restarting the API. Provider
execution requires ``FLEET_LIVE=1``; receipts contain bounded aggregates only.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import statistics
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv


def _evaluation_dataset_name(tracking_url: str) -> str:
    """Dataset name; UC-qualified (catalog.schema.table) for the Databricks backend."""
    if tracking_url != "databricks":
        return DATASET_NAME
    catalog = os.environ.get("FLEET_MLFLOW_TRACE_CATALOG", "ml")
    schema = os.environ.get("FLEET_MLFLOW_TRACE_SCHEMA", "genai")
    return f"{catalog}.{schema}.fleet_rlm_latency_quality_v1"


_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.benchmarks import judges as _judges
from scripts.benchmarks.corpus_chain import (
    CORPUS_SEEDS,
    CORPUS_WORKLOAD_ID,
    CorpusCase,
    corpus_workload,
    make_corpus_case,
    validate_corpus_evidence,
    validate_corpus_report,
    write_corpus,
)

# Re-exported judge contracts; conductors and tests import them from this module.
CORRECTNESS_DESCRIPTION = _judges.CORRECTNESS_DESCRIPTION
CORRECTNESS_INSTRUCTIONS = _judges.CORRECTNESS_INSTRUCTIONS
DEFAULT_JUDGE_MODEL = _judges.DEFAULT_JUDGE_MODEL
EVIDENCE_COVERAGE_DESCRIPTION = _judges.EVIDENCE_COVERAGE_DESCRIPTION
EVIDENCE_COVERAGE_INSTRUCTIONS = _judges.EVIDENCE_COVERAGE_INSTRUCTIONS
JUDGE_INFERENCE_PARAMS = _judges.JUDGE_INFERENCE_PARAMS
JUDGE_NAMES = _judges.JUDGE_NAMES
ensure_registered = _judges.ensure_registered

RECEIPT_SCHEMA = "fleet.rlm-latency/v1"
DATASET_NAME = "fleet-rlm-latency-quality-v1"
DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_MLFLOW_URL = "http://127.0.0.1:5001"
_LIVE_VALUES = frozenset({"1", "true", "yes"})
EVIDENCE_WORKLOAD_ID = "evidence-conflict-v1"
WORKLOAD_CHOICES = (EVIDENCE_WORKLOAD_ID, CORPUS_WORKLOAD_ID)
_TRAJECTORY_ITEM_LIMIT = 64
_TRAJECTORY_CHAR_LIMIT = 64 * 1024

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
    """
    Calculate a deterministic nearest-rank percentile for a sequence of values.

    Parameters:
        values (Sequence[float]): Values from which to calculate the percentile.
        percentile_value (int): Percentile to calculate, from greater than 0 through 100.

    Returns:
        float: The selected percentile value.

    Raises:
        ValueError: If values is empty or percentile_value is outside the range (0, 100].
    """
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0 < percentile_value <= 100:
        raise ValueError("percentile must be in (0, 100]")
    ordered = sorted(float(value) for value in values)
    rank = max(1, (percentile_value * len(ordered) + 99) // 100)
    return ordered[min(rank, len(ordered)) - 1]


def latency_gate(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    """
    Apply performance and quality criteria to baseline and candidate aggregate receipts.

    Parameters:
        baseline (Mapping[str, Any]): Aggregate receipt used as the performance reference.
        candidate (Mapping[str, Any]): Aggregate receipt evaluated against the baseline.

    Returns:
        dict[str, Any]: A result containing individual check outcomes and an overall
            ``passed`` value.
    """
    baseline_p50 = float(baseline["end_to_end_ms"]["p50"])
    candidate_p50 = float(candidate["end_to_end_ms"]["p50"])
    checks = {
        "p50_reduced_by_50_percent": candidate_p50 <= baseline_p50 * 0.5,
        "p95_not_worse": float(candidate["end_to_end_ms"]["p95"]) <= float(baseline["end_to_end_ms"]["p95"]),
        "error_rate_not_worse": float(candidate["error_rate"]) <= float(baseline["error_rate"]),
        "quality_complete": bool(candidate.get("quality_complete")),
    }
    return {"passed": all(checks.values()), "checks": checks}


def quality_gate(evaluation: Mapping[str, Any]) -> bool:
    """
    Determine whether an evaluation satisfies the complete quality requirements.

    Parameters:
        evaluation (Mapping[str, Any]): Evaluation results, including its run mode,
        record count, and judge metrics.

    Returns:
        bool: `True` if the evaluation is non-dry-run, contains all quality records,
        and has perfect correctness and evidence-coverage mean scores; `False` otherwise.
    """
    if evaluation.get("dry_run") or int(evaluation.get("records", 0)) != len(QUALITY_RECORDS):
        return False
    metrics = evaluation.get("metrics")
    if not isinstance(metrics, Mapping):
        return False

    def score(prefix: str) -> float | None:
        """
        Extract the first mean metric matching a key prefix.

        Parameters:
            prefix: Prefix used to select metric keys.

        Returns:
            The first matching metric converted to a float, or `None` when no matching metric exists.
        """
        values = [
            float(value)
            for key, value in metrics.items()
            if str(key).startswith(prefix) and str(key).endswith(("/mean", "_mean"))
        ]
        return values[0] if values else None

    return score("correctness") == 1.0 and score("evidence_coverage") == 1.0


def _require_live() -> None:
    """
    Require provider-backed execution to run in live mode.

    Raises:
        BenchmarkError: If `FLEET_LIVE` is not set to an accepted live-mode value.
    """
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        raise BenchmarkError("FLEET_LIVE=1 is required for provider-backed execution")


def _load_repository_env() -> None:
    """Load environment variables from the repository's `.env` file without overriding existing values."""
    load_dotenv(_REPO_ROOT / ".env", override=False)


def _configure_judge_environment(judge_model: str) -> None:
    """
    Configure OpenAI adapter credentials for a Databricks-hosted judge model.

    Parameters:
        judge_model (str): Judge model URI whose `openai:/` prefix selects the Databricks credential mapping.

    Raises:
        BenchmarkError: If the judge model uses the OpenAI adapter and required Databricks credentials are unavailable.
    """
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
    """
    Parse valid JSON objects from Server-Sent Event data lines.

    Parameters:
        response (httpx.Response): The response containing Server-Sent Event lines.

    Yields:
        dict[str, Any]: JSON object payloads from valid, non-terminal data events.
    """
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
    """Extract answer text from a structured stream chunk.

    Parameters:
        chunk (Mapping[str, Any]): A stream chunk containing structured result data.

    Returns:
        str | None: The answer text when present in the chunk; otherwise, `None`.
    """
    data = chunk.get("data")
    if not isinstance(data, Mapping):
        return None
    value = data.get("value")
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("answer"), str):
        return str(value["answer"])
    return None


def _append_trajectory_value(values: list[str], value: object) -> None:
    """Keep trajectory evidence bounded by item count and total characters."""
    if not isinstance(value, str) or len(values) >= _TRAJECTORY_ITEM_LIMIT:
        return
    remaining = _TRAJECTORY_CHAR_LIMIT - sum(len(item) for item in values)
    if remaining > 0:
        values.append(value[:remaining])


def _upload_corpus(client: httpx.Client, corpus_path: Path, *, seed: int) -> str:
    """Upload the host-generated corpus as a bounded compressed Attachment."""
    with corpus_path.open("rb") as handle:
        compressed = gzip.compress(handle.read(), mtime=0)
    response = client.post(
        "/api/attachments",
        files={
            "attachment": (
                f"fleet-corpus-{seed}.ndjson.gz",
                compressed,
                "application/gzip",
            )
        },
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping) or not isinstance(payload.get("id"), str):
        raise BenchmarkError("Fleet corpus attachment response is malformed")
    return payload["id"]


def _termination_mode_from_chunk(chunk: Mapping[str, Any]) -> str | None:
    """
    Identify the termination mode signaled by a stream chunk.

    Parameters:
        chunk (Mapping[str, Any]): A stream event payload.

    Returns:
        str | None: The detected termination mode, or `None` when the chunk contains no recognized termination signal.
    """
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


def run_turn(
    client: httpx.Client,
    query: str,
    *,
    nonce: str,
    attachment_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """
    Execute one Fleet Turn and collect its response, timing, usage, trace identifiers, and tool-call counts.

    Parameters:
        client (httpx.Client): HTTP client configured for the Fleet API.
        query (str): Prompt to submit for the Turn.
        nonce (str): Unique value used to identify the benchmark request.
        attachment_ids (Sequence[str]): Authorized Attachment IDs to pass to the Turn.

    Returns:
        dict[str, Any]: Bounded operational results, including the answer, identifiers, latency measurements,
        usage data, iteration count, tool-call counts, and termination mode.

    Raises:
        BenchmarkError: If the Turn reports an error, is aborted, or finishes for a reason other than `stop`.
    """
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
    recursive_batch_calls = 0
    peak_child_concurrency = 0
    requested_attachment_ids = {str(value) for value in attachment_ids}
    attachment_accessed = False
    trajectory = {"codes": [], "outputs": []}
    first_event_ms: float | None = None
    termination_mode: str | None = None
    started = time.perf_counter()
    with client.stream(
        "POST",
        f"/api/sessions/{session_id}/turns",
        json={"text": prompt, "attachment_ids": list(attachment_ids), "skill_selections": []},
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
                recursive_batch_calls += int(tool_name == "rlm_query_batched")
            elif chunk_type == "tool-output-available" and isinstance(chunk.get("output"), Mapping):
                raw_peak = chunk["output"].get("peak_child_concurrency")
                if isinstance(raw_peak, int) and not isinstance(raw_peak, bool):
                    peak_child_concurrency = max(peak_child_concurrency, raw_peak)
            elif chunk_type == "data-attachment" and isinstance(chunk.get("data"), Mapping):
                data = chunk["data"]
                accessed_id = data.get("attachment_id", data.get("attachmentId"))
                attachment_accessed = attachment_accessed or str(accessed_id) in requested_attachment_ids
            elif chunk_type == "data-rlm-code" and isinstance(chunk.get("data"), Mapping):
                _append_trajectory_value(trajectory["codes"], chunk["data"].get("code"))
            elif chunk_type == "data-rlm-output" and isinstance(chunk.get("data"), Mapping):
                _append_trajectory_value(trajectory["outputs"], chunk["data"].get("output"))
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
        "recursive_batch_calls": recursive_batch_calls,
        "peak_child_concurrency": peak_child_concurrency,
        "termination_mode": termination_mode,
        "attachment_accessed": attachment_accessed,
        "trajectory": trajectory,
    }


def _active_policy(client: httpx.Client) -> dict[str, Any]:
    """
    Retrieve the active Fleet policy and its root and sub-model settings.

    Parameters:
        client (httpx.Client): HTTP client configured for the Fleet API.

    Returns:
        dict[str, Any]: Active profile name and selected model, token-limit, and reasoning settings.

    Raises:
        BenchmarkError: If Fleet settings do not expose a valid active profile.
    """
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
    """
    Finds the MLflow trace for a Fleet run containing the execution span.

    Parameters:
        mlflow_url (str): MLflow tracking server URL.
        experiment_id (str): Experiment containing the trace.
        run_id (str): Fleet run identifier used to locate the trace.

    Returns:
        str | None: The trace ID when a matching execution trace is found, or `None` otherwise.
    """
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


def _attach_trace_identity(row: dict[str, Any], execution_trace_id: str | None) -> dict[str, Any]:
    """Require the public and execution traces to identify the same root trace."""
    stream_trace_id = row.get("trace_id")
    row["stream_trace_id"] = stream_trace_id
    row["execution_trace_id"] = execution_trace_id
    row["trace_id_match"] = (
        isinstance(stream_trace_id, str)
        and bool(stream_trace_id)
        and isinstance(execution_trace_id, str)
        and bool(execution_trace_id)
        and stream_trace_id == execution_trace_id
    )
    if not isinstance(stream_trace_id, str) or not stream_trace_id:
        raise BenchmarkError("SSE trace ID was missing")
    if not isinstance(execution_trace_id, str) or not execution_trace_id:
        raise BenchmarkError("execution trace was not found")
    if stream_trace_id != execution_trace_id:
        raise BenchmarkError("SSE and execution trace IDs did not match")
    # Keep the public SSE ID as the canonical receipt ID. Never overwrite it
    # with a separately discovered trace after this validation.
    row["trace_id"] = stream_trace_id
    return row


def _execution_trace_diagnostics(mlflow_url: str, trace_id: str) -> dict[str, Any]:
    """
    Collect bounded execution-trace diagnostics for benchmark comparisons.
    
    Parameters:
        mlflow_url (str): MLflow tracking server URL.
        trace_id (str): Identifier of the execution trace to inspect.
    
    Returns:
        dict[str, Any]: Diagnostic metrics for root language-model spans, context size,
            adapter and repair errors, response keys, and detail truncation. Returns a
            status of ``"unavailable"`` and the exception category when diagnostics
            cannot be collected.
    """
    try:
        import mlflow

        mlflow.set_tracking_uri(mlflow_url)
        trace = mlflow.get_trace(trace_id)
        spans = list(trace.data.spans)
        repair_error_count = 0
        detail_overflowed = False
        root_lm_spans = [span for span in spans if span.name == "RLM.root_lm"]
        root_lm_wall_times = [
            float(span.outputs["wall_time_ms"])
            for span in root_lm_spans
            if isinstance(getattr(span, "outputs", None), Mapping)
            and isinstance(span.outputs.get("wall_time_ms"), (int, float))
        ]
        root_lm_provider_times = [
            float(span.outputs["provider_response_ms"])
            for span in root_lm_spans
            if isinstance(getattr(span, "outputs", None), Mapping)
            and isinstance(span.outputs.get("provider_response_ms"), (int, float))
        ]
        root_lm_context_chars = [
            int(span.inputs["context_chars"])
            for span in root_lm_spans
            if isinstance(getattr(span, "inputs", None), Mapping) and isinstance(span.inputs.get("context_chars"), int)
        ]
        adapter_parse_error_count = 0
        last_lm_response_keys: list[str] = []
        for span in spans:
            outputs = getattr(span, "outputs", None)
            if isinstance(outputs, Mapping):
                result_kind = outputs.get("result_kind")
                if result_kind == "repair_error":
                    repair_error_count += 1
                if span.name == "RLM.execute" and outputs.get("failure_category") == "adapter_parse_error":
                    adapter_parse_error_count += 1
                last_lm_call = outputs.get("last_lm_call")
                if span.name == "RLM.execute" and isinstance(last_lm_call, Mapping):
                    keys = last_lm_call.get("response_keys")
                    if isinstance(keys, (list, tuple)):
                        last_lm_response_keys = [str(key) for key in keys[:32]]
                if span.name == "Turn.progress.warning" and "omitted" in str(outputs.get("message", "")):
                    detail_overflowed = True
        return {
            "root_lm_span_count": len(root_lm_spans),
            "root_lm_wall_time_ms": round(sum(root_lm_wall_times), 3),
            "root_lm_provider_response_ms": round(sum(root_lm_provider_times), 3),
            "root_lm_slowest_wall_time_ms": round(max(root_lm_wall_times), 3) if root_lm_wall_times else 0.0,
            "root_lm_max_context_chars": max(root_lm_context_chars) if root_lm_context_chars else 0,
            "adapter_parse_error_count": adapter_parse_error_count,
            "last_lm_response_keys": last_lm_response_keys,
            "repair_error_count": repair_error_count,
            "detail_overflowed": detail_overflowed,
        }
    except Exception as exc:
        return {"status": "unavailable", "error_category": type(exc).__name__}


def _tag_trace(mlflow_url: str, trace_id: str, *, workload_id: str, variant: str, sample: str) -> None:
    """Tag an MLflow trace with Fleet workload, performance variant, and sample metadata.

    Parameters:
        mlflow_url (str): MLflow tracking server URL.
        trace_id (str): Identifier of the trace to tag.
        workload_id (str): Workload identifier associated with the trace.
        variant (str): Performance variant associated with the trace.
        sample (str): Sample category associated with the trace.
    """
    import mlflow

    mlflow.set_tracking_uri(mlflow_url)
    client = mlflow.MlflowClient()
    client.set_trace_tag(trace_id, "fleet.workload_id", workload_id)
    client.set_trace_tag(trace_id, "fleet.perf_variant", variant)
    client.set_trace_tag(trace_id, "fleet.sample_kind", sample)


def _aggregate(rows: Sequence[Mapping[str, Any]], *, workload_id: str = EVIDENCE_WORKLOAD_ID) -> dict[str, Any]:
    """
    Aggregate measured benchmark rows into latency, error, usage, execution, trace, diagnostic, and corpus-quality metrics.
    
    Parameters:
        rows (Sequence[Mapping[str, Any]]): Benchmark sample records to aggregate.
        workload_id (str): Workload identifier used to determine whether corpus-quality metrics apply.
    
    Returns:
        dict[str, Any]: Aggregate metrics. Warmups and failed samples are excluded from success-based metrics, and quality is marked incomplete unless all corpus checks pass for the corpus workload.
    """
    measured = [row for row in rows if row.get("sample_kind") == "measured"]
    successes = [row for row in measured if not row.get("error_category")]
    durations = [float(row["duration_ms"]) for row in successes]
    first_events = [float(row["first_event_ms"]) for row in successes if float(row["first_event_ms"]) >= 0]
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "cache_read_tokens": 0}
    for row in successes:
        for key, value in _usage_totals(row.get("usage")).items():
            usage[key] += value
    trace_matches = [bool(row["trace_id_match"]) for row in measured if isinstance(row.get("trace_id_match"), bool)]
    corpus_report_results = [
        row.get("corpus_validation", {}).get("passed") is True
        if isinstance(row.get("corpus_validation"), Mapping)
        else False
        for row in measured
    ]
    corpus_evidence_results = [
        row.get("corpus_evidence", {}).get("passed") is True
        if isinstance(row.get("corpus_evidence"), Mapping)
        else False
        for row in measured
    ]
    corpus_quality_results = [row.get("corpus_quality_passed") is True for row in measured]
    corpus_report_complete = bool(measured) and all(corpus_report_results)
    corpus_evidence_complete = bool(measured) and all(corpus_evidence_results)
    corpus_quality_complete = (
        bool(measured) and corpus_report_complete and corpus_evidence_complete and all(corpus_quality_results)
    )
    diagnostics: list[Mapping[str, Any]] = []
    for row in measured:
        item = row.get("trace_diagnostics")
        if isinstance(item, Mapping):
            diagnostics.append(item)
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
        "recursive_batch_calls": sum(int(row.get("recursive_batch_calls", 0)) for row in successes),
        "peak_child_concurrency": max(int(row.get("peak_child_concurrency", 0)) for row in successes)
        if successes
        else 0,
        "typed_submit_count": sum(row.get("termination_mode") == "typed_submit" for row in successes),
        "token_totals": usage,
        "trace_ids": [row["trace_id"] for row in successes if row.get("trace_id")],
        "trace_id_match_rate": (sum(trace_matches) / len(trace_matches)) if trace_matches else 0.0,
        "root_lm_span_count": sum(int(item.get("root_lm_span_count", 0)) for item in diagnostics),
        "root_lm_wall_time_ms": round(sum(float(item.get("root_lm_wall_time_ms", 0.0)) for item in diagnostics), 3),
        "root_lm_provider_response_ms": round(
            sum(float(item.get("root_lm_provider_response_ms", 0.0)) for item in diagnostics), 3
        ),
        "root_lm_slowest_wall_time_ms": round(
            max((float(item.get("root_lm_slowest_wall_time_ms", 0.0)) for item in diagnostics), default=0.0), 3
        ),
        "root_lm_max_context_chars": max(
            (int(item.get("root_lm_max_context_chars", 0)) for item in diagnostics), default=0
        ),
        "adapter_parse_error_count": sum(int(item.get("adapter_parse_error_count", 0)) for item in diagnostics),
        "last_lm_response_keys": next(
            (
                list(item.get("last_lm_response_keys", []))
                for item in reversed(diagnostics)
                if isinstance(item.get("last_lm_response_keys"), list)
            ),
            [],
        ),
        "repair_error_count": sum(int(item.get("repair_error_count", 0)) for item in diagnostics),
        "detail_overflowed": any(item.get("detail_overflowed") is True for item in diagnostics),
        "corpus_report_complete": corpus_report_complete if workload_id == CORPUS_WORKLOAD_ID else None,
        "corpus_evidence_complete": corpus_evidence_complete if workload_id == CORPUS_WORKLOAD_ID else None,
        "corpus_quality_complete": corpus_quality_complete if workload_id == CORPUS_WORKLOAD_ID else None,
        "quality_complete": corpus_quality_complete if workload_id == CORPUS_WORKLOAD_ID else False,
    }


def _usage_totals(value: object) -> dict[str, int]:
    """
    Aggregate approved token counters from nested mappings and sequences.

    Parameters:
        value (object): Nested token usage data to inspect.

    Returns:
        dict[str, int]: Totals for prompt, completion, reasoning, and cache-read tokens.
    """
    result = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "cache_read_tokens": 0}

    def visit(item: object) -> None:
        """Collect approved token counters from nested mappings and sequences.

        Parameters:
            item (object): Nested usage data containing token counters.
        """
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


def _metrics_query(mlflow_url: str, experiment_id: str, *, workload_id: str, variant: str) -> dict[str, Any]:
    """
    Query MLflow latency metrics for the configured Fleet workload and performance variant.

    Parameters:
        mlflow_url (str): Base URL of the MLflow server.
        experiment_id (str): MLflow experiment identifier.
        workload_id (str): Fleet workload identifier used to filter traces.
        variant (str): Performance variant used to filter traces.

    Returns:
        dict[str, Any]: Latency metric results grouped by span name.
    """
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
                f'trace.tag.fleet.workload_id = "{workload_id}"',
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
    """
    Run live latency benchmark samples and return an aggregate performance receipt.

    Parameters:
        args (argparse.Namespace): Benchmark configuration, including API and MLflow endpoints, experiment ID,
        variant, warmup count, and measured run count.

    Returns:
        dict[str, Any]: Benchmark receipt containing the active policy, aggregate sample metrics,
        and MLflow span metrics.
    """
    _require_live()
    workload_id = str(args.workload)
    corpus_case: CorpusCase | None = make_corpus_case(args.corpus_seed) if workload_id == CORPUS_WORKLOAD_ID else None
    workload = corpus_workload(corpus_case) if corpus_case is not None else LATENCY_WORKLOAD
    rows: list[dict[str, Any]] = []
    with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=httpx.Timeout(args.timeout)) as client:
        policy = _active_policy(client)
        with tempfile.TemporaryDirectory(prefix="fleet-corpus-") as temp_dir:
            attachment_ids: tuple[str, ...] = ()
            if corpus_case is not None:
                corpus_path = Path(temp_dir) / "corpus.ndjson"
                write_corpus(corpus_case, corpus_path)
                attachment_ids = (_upload_corpus(client, corpus_path, seed=corpus_case.seed),)
            for index in range(args.warmups + args.runs):
                sample = "warmup" if index < args.warmups else "measured"
                nonce = f"{args.variant}-{sample}-{uuid4()}"
                sample_started = time.perf_counter()
                row: dict[str, Any] = {}
                try:
                    row = run_turn(client, workload, nonce=nonce, attachment_ids=attachment_ids)
                    execution_trace_id = (
                        _execution_trace_id(
                            args.mlflow_url,
                            args.experiment_id,
                            str(row["run_id"]),
                        )
                        if row.get("run_id")
                        else None
                    )
                    _attach_trace_identity(row, execution_trace_id)
                    row["trace_diagnostics"] = _execution_trace_diagnostics(
                        args.mlflow_url,
                        str(row["trace_id"]),
                    )
                    if row.get("trace_id"):
                        _tag_trace(
                            args.mlflow_url,
                            str(row["trace_id"]),
                            workload_id=workload_id,
                            variant=args.variant,
                            sample=sample,
                        )
                    if corpus_case is not None:
                        diagnostics = row.get("trace_diagnostics")
                        if isinstance(diagnostics, Mapping) and diagnostics.get("detail_overflowed") is True:
                            raise BenchmarkError("corpus detail event overflowed the bounded relay")
                        validation = validate_corpus_report(str(row.get("answer", "")), corpus_case)
                        trajectory = row.get("trajectory")
                        evidence = validate_corpus_evidence(
                            trajectory if isinstance(trajectory, Mapping) else {},
                            attachment_accessed=row.get("attachment_accessed") is True,
                        )
                        row["corpus_validation"] = validation.as_dict()
                        row["corpus_evidence"] = evidence.as_dict()
                        row["corpus_quality_passed"] = validation.passed and evidence.passed
                        if not row["corpus_quality_passed"]:
                            raise BenchmarkError("corpus report or execution evidence validation failed")
                except Exception as exc:
                    row = {
                        **row,
                        "duration_ms": row.get("duration_ms", round((time.perf_counter() - sample_started) * 1000, 3)),
                        "first_event_ms": row.get("first_event_ms", -1.0),
                        "error_category": type(exc).__name__,
                        "trace_id": row.get("trace_id"),
                    }
                row["workload_id"] = workload_id
                if corpus_case is not None:
                    row["corpus_seed"] = corpus_case.seed
                row["sample_kind"] = sample
                rows.append(row)
    aggregate = _aggregate(rows, workload_id=workload_id)
    metrics: dict[str, Any]
    try:
        metrics = _metrics_query(
            args.mlflow_url,
            args.experiment_id,
            workload_id=workload_id,
            variant=args.variant,
        )
    except Exception as exc:
        metrics = {"status": "unavailable", "error_category": type(exc).__name__}
    return {
        "schema": RECEIPT_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "variant": args.variant,
        "workload_id": workload_id,
        "corpus_seed": corpus_case.seed if corpus_case is not None else None,
        "active_policy": policy,
        "warmups": args.warmups,
        "aggregate": aggregate,
        "mlflow_span_metrics": metrics,
    }


def prepare_evaluation(args: argparse.Namespace) -> dict[str, Any]:
    """
    Create or reuse the MLflow quality-evaluation dataset and register its judges.

    Parameters:
        args (argparse.Namespace): Configuration containing the MLflow tracking URL, experiment ID, and judge model URI.

    Returns:
        dict[str, Any]: Dataset ID, dataset name, and record count.

    Raises:
        BenchmarkError: If no judge model URI is provided.
    """
    import mlflow
    from mlflow.genai import datasets

    if not args.judge_model:
        raise BenchmarkError("prepare-evaluation requires --judge-model with an MLflow-supported model URI")
    mlflow.set_tracking_uri(args.mlflow_url)
    mlflow.set_experiment(experiment_id=args.experiment_id)
    dataset_name = _evaluation_dataset_name(args.mlflow_url)
    existing = [item for item in datasets.search_datasets([args.experiment_id]) if item.name == dataset_name]
    if existing:
        dataset = existing[0]
    else:
        dataset = datasets.create_dataset(name=dataset_name, experiment_id=args.experiment_id)
        dataset.merge_records(list(QUALITY_RECORDS))

    for name in JUDGE_NAMES:
        ensure_registered(name, args.judge_model, experiment_id=args.experiment_id)
    return {"dataset_id": dataset.dataset_id, "dataset_name": dataset.name, "records": len(dataset.to_df())}


def run_evaluation(args: argparse.Namespace) -> dict[str, Any]:
    """
    Run MLflow GenAI evaluation on the quality dataset.

    Parameters:
        args (argparse.Namespace): Command-line options containing the judge model, MLflow and Fleet API settings,
        experiment ID, timeout, and dry-run flag.

    Returns:
        dict[str, Any]: Evaluation receipt containing the dataset name, evaluation mode, record count, metrics,
        and quality-gate result.

    Raises:
        BenchmarkError: If live execution is not enabled or no judge model is configured.
    """
    _require_live()
    if not args.judge_model:
        raise BenchmarkError("evaluate requires --judge-model with an MLflow-supported model URI")
    _configure_judge_environment(args.judge_model)
    import mlflow
    from mlflow.genai import datasets
    from mlflow.genai.scorers import get_scorer

    mlflow.set_tracking_uri(args.mlflow_url)
    mlflow.set_experiment(experiment_id=args.experiment_id)
    dataset_name = _evaluation_dataset_name(args.mlflow_url)
    dataset = datasets.get_dataset(name=dataset_name)
    frame = dataset.to_df().head(3) if args.dry_run else dataset.to_df()

    def predict_fn(query: str) -> str:
        """
        Generate an answer for a quality-evaluation query.

        Parameters:
            query (str): The query to submit for evaluation.

        Returns:
            str: The answer produced for the query.
        """
        with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=httpx.Timeout(args.timeout)) as client:
            return str(run_turn(client, query, nonce=f"quality-{uuid4()}")["answer"])

    run_name = args.run_name or f"quality-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    scorers: list[Any] = [
        get_scorer(name="correctness", experiment_id=args.experiment_id),
        get_scorer(name="evidence_coverage", experiment_id=args.experiment_id),
    ]
    if args.scorers:
        from scripts.benchmarks.scorers import build_scorers

        requested = [name.strip() for name in args.scorers.split(",") if name.strip()]
        scorers.extend(build_scorers(requested, judge_model=args.judge_model, guidelines=args.guidelines or None))
    with mlflow.start_run(run_name=run_name) as run:
        result = mlflow.genai.evaluate(
            data=frame,
            predict_fn=predict_fn,
            scorers=scorers,
        )
        run_id = run.info.run_id
    metrics = {str(key): value for key, value in result.metrics.items()}
    receipt = {
        "dataset_name": dataset_name,
        "dataset_id": getattr(dataset, "dataset_id", None),
        "run_id": run_id,
        "run_name": run_name,
        "dry_run": args.dry_run,
        "records": len(frame),
        "scorers": [getattr(scorer, "name", None) for scorer in scorers],
        "metrics": metrics,
    }
    receipt["quality_complete"] = quality_gate(receipt)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    """
    Create the command-line argument parser for benchmark, evaluation, and comparison workflows.

    Returns:
        argparse.ArgumentParser: Parser configured with command, endpoint, sampling, evaluation, input,
        and output options.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("benchmark", "prepare-evaluation", "evaluate", "compare"))
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--mlflow-url", default=DEFAULT_MLFLOW_URL)
    parser.add_argument("--experiment-id", default="1")
    parser.add_argument("--variant", default="baseline")
    parser.add_argument("--workload", choices=WORKLOAD_CHOICES, default=EVIDENCE_WORKLOAD_ID)
    parser.add_argument("--corpus-seed", choices=CORPUS_SEEDS, type=int, default=CORPUS_SEEDS[0])
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=2_000.0)
    parser.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        help="MLflow-supported judge URI (default: the probe-verified qwen serving endpoint)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--scorers",
        default="",
        help=(
            "Comma-separated extra scorers beyond correctness/evidence_coverage: "
            "response_present, tool_evidence_used, guidelines, retrieval_groundedness"
        ),
    )
    parser.add_argument(
        "--guidelines",
        default="",
        help="Guideline text for the guidelines scorer",
    )
    parser.add_argument(
        "--run-name",
        default="",
        help="MLflow run name for evaluate (default: quality-<UTC timestamp>); reuse a name to build baselines",
    )
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--quality", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the selected CLI command and write its result as a JSON receipt.

    Parameters:
        argv (Sequence[str] | None): Optional command-line arguments; uses the process arguments when omitted.

    Returns:
        int: `0` when the command succeeds, `1` when it fails.
    """
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
