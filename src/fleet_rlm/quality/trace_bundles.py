"""Trace bundle export and distillation helpers for offline GEPA runs."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal, TypedDict

TraceExportFormat = Literal["json", "jsonl", "both"]


class TraceExportArtifacts(TypedDict):
    json_path: str | None
    jsonl_path: str | None
    distilled_bundle_path: str
    summary: dict[str, Any]


def _artifact_root(root: str | Path | None = None) -> Path:
    return Path(root or os.environ.get("FLEET_RLM_OPTIMIZATION_DATA_ROOT", os.getcwd())).resolve()


def _safe_segment(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-") or "session"


def session_trace_export_dir(session_id: str, *, root: str | Path | None = None) -> Path:
    """Return the artifact directory for one session trace export."""
    return _artifact_root(root) / "traces" / "sessions" / _safe_segment(session_id)


def _text_blob(payload: dict[str, Any]) -> str:
    pieces: list[str] = []
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for source in (info, data, payload.get("metadata")):
        if isinstance(source, dict):
            pieces.append(json.dumps(source, default=str, sort_keys=True))
    for key in ("spans", "assessments"):
        raw = payload.get(key)
        if isinstance(raw, list):
            pieces.append(json.dumps(raw[:20], default=str, sort_keys=True))
    return "\n".join(pieces).lower()


def _classify_failure_categories(payload: dict[str, Any]) -> list[str]:
    blob = _text_blob(payload)
    categories: list[str] = []
    if any(token in blob for token in ("exception", "traceback", "failed", "error", "status_code")):
        categories.append("interface_failures")
    if any(token in blob for token in ("missing evidence", "citation", "unsupported", "not enough evidence")):
        categories.append("missing_evidence")
    if any(token in blob for token in ("tool", "function_call", "bad tool", "invalid tool")):
        categories.append("bad_tool_use")
    if any(token in blob for token in ("unsafe", "policy", "credential", "secret", "injection")):
        categories.append("unsafe_behavior")
    if any(token in blob for token in ("format", "json", "schema", "parse", "malformed")):
        categories.append("formatting_issues")
    if any(token in blob for token in ("loop", "iteration", "timeout", "budget", "token limit", "max_llm_calls")):
        categories.append("loop_inefficiency")
    return categories or ["needs_review"]


def _prompt_recommendations(categories: list[str]) -> list[str]:
    recommendations: list[str] = []
    if "interface_failures" in categories:
        recommendations.append("Add explicit recovery instructions for interface errors and failed calls.")
    if "missing_evidence" in categories:
        recommendations.append("Require cited evidence before final answers and mark unsupported claims.")
    if "bad_tool_use" in categories:
        recommendations.append("Clarify when to use tools, when to inspect outputs, and when to stop.")
    if "unsafe_behavior" in categories:
        recommendations.append("Strengthen safety, credential handling, and prompt-injection constraints.")
    if "formatting_issues" in categories:
        recommendations.append("Tighten output-format requirements and schema validation steps.")
    if "loop_inefficiency" in categories:
        recommendations.append("Add loop budget discipline and early-stop criteria.")
    if not recommendations:
        recommendations.append("Compare expected and actual behavior, then make the smallest prompt change.")
    return recommendations


def trace_payload_to_distilled_row(payload: dict[str, Any]) -> dict[str, Any]:
    """Distill a full trace payload into one GEPA proposer evidence row."""
    raw_info = payload.get("info")
    info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
    categories = _classify_failure_categories(payload)
    raw_assessments = payload.get("assessments")
    assessments: list[Any] = raw_assessments if isinstance(raw_assessments, list) else []
    raw_spans = payload.get("spans")
    spans: list[Any] = raw_spans if isinstance(raw_spans, list) else []
    raw_metadata = payload.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    return {
        "kind": "trace_evidence",
        "trace_id": info.get("trace_id") or payload.get("trace_id"),
        "client_request_id": info.get("client_request_id") or payload.get("client_request_id"),
        "session_id": payload.get("session_id"),
        "turn_id": payload.get("turn_id"),
        "failure_categories": categories,
        "assessment_count": len(assessments),
        "span_count": len(spans),
        "span_types": sorted(
            {
                str(span.get("span_type") or span.get("type"))
                for span in spans
                if isinstance(span, dict) and (span.get("span_type") or span.get("type"))
            }
        ),
        "metadata_keys": sorted(metadata.keys()),
        "prompt_change_recommendations": _prompt_recommendations(categories),
        "supporting_trace_path": payload.get("source_trace_path"),
    }


def distill_trace_payloads(payloads: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return distilled rows and a compact run-level summary."""
    rows = [trace_payload_to_distilled_row(payload) for payload in payloads]
    category_counts = Counter(category for row in rows for category in row.get("failure_categories", []))
    summary = {
        "version": 1,
        "trace_count": len(payloads),
        "failure_clusters": [
            {"category": category, "count": count} for category, count in sorted(category_counts.items())
        ],
    }
    return rows, summary


def write_session_trace_artifacts(
    *,
    session_id: str,
    payloads: list[dict[str, Any]],
    export_format: TraceExportFormat = "both",
    root: str | Path | None = None,
) -> TraceExportArtifacts:
    """Write full trace artifacts and the distilled GEPA evidence bundle."""
    export_dir = session_trace_export_dir(session_id, root=root)
    export_dir.mkdir(parents=True, exist_ok=True)
    base = export_dir / "mlflow-traces"

    json_path: Path | None = None
    jsonl_path: Path | None = None
    if export_format in ("json", "both"):
        json_path = base.with_suffix(".json")
        json_path.write_text(json.dumps(payloads, indent=2, sort_keys=True, default=str), encoding="utf-8")
    if export_format in ("jsonl", "both"):
        jsonl_path = base.with_suffix(".jsonl")
        jsonl_path.write_text(
            "\n".join(json.dumps(payload, sort_keys=True, default=str) for payload in payloads)
            + ("\n" if payloads else ""),
            encoding="utf-8",
        )

    distilled_rows, summary = distill_trace_payloads(payloads)
    distilled_path = export_dir / "mlflow-traces.distilled.jsonl"
    distilled_payloads = [{"kind": "trace_bundle_summary", **summary}, *distilled_rows]
    distilled_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True, default=str) for row in distilled_payloads)
        + ("\n" if distilled_payloads else ""),
        encoding="utf-8",
    )
    return {
        "json_path": str(json_path) if json_path else None,
        "jsonl_path": str(jsonl_path) if jsonl_path else None,
        "distilled_bundle_path": str(distilled_path),
        "summary": summary,
    }
