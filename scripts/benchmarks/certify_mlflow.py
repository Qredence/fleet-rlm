#!/usr/bin/env python3
"""Run the bounded MLflow 3.16 certification lane.

The command is deliberately separate from the normal tracing smoke test.  It
exercises Fleet's turn trace, DSPy autolog, the deadline LM proxy, feedback,
privacy projection, and concurrent/repeated lifecycles, then records every
other requested failure mode as ``unexercised`` or ``blocked``.  A receipt is
write-once and never contains a tracking URI, credential, prompt, or trace
payload.

Only an explicitly selected backend is used.  ``--backend configured`` needs a
tracking URI and the managed trace-location fields supplied by the operator;
it never falls back to the local server.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import dspy
from dotenv import load_dotenv

from fleet_rlm.config.loader import load_runtime_settings
from fleet_rlm.observability.feedback import TraceFeedbackService
from fleet_rlm.observability.tracing import (
    annotate_trace_io,
    configure_tracing,
    flush_tracing,
    is_tracing_active,
    reset_tracing,
    turn_phase_span,
    turn_trace,
)
from fleet_rlm.rlm.program import DeadlineLMProxy

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LIVE_VALUES = frozenset({"1", "true", "yes"})
_SCHEMA = "fleet.mlflow-certification/v1"
_MAX_RECEIPT_BYTES = 256 * 1024
_SENTINEL = "fleet-certification-secret-sentinel"
_SENTINEL_INPUT = f"AWS_SECRET_ACCESS_KEY={_SENTINEL}"


class CertificationError(RuntimeError):
    """Raised when a requested certification lane cannot run safely."""


class _CertificationLM(dspy.BaseLM):
    """Deterministic completion source used to exercise DSPy autolog safely."""

    def __init__(self) -> None:
        super().__init__(model="fleet/certification")
        self.kwargs: dict[str, object] = {}

    def _response(self) -> SimpleNamespace:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="certification-ok"))],
            usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            model="fleet/certification",
        )

    def forward(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
        return self._response()

    async def aforward(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
        return self._response()


def _require_live() -> None:
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        raise CertificationError("FLEET_LIVE=1 is required for MLflow certification")


def _safe_uri_label(uri: str) -> str:
    lowered = uri.strip().lower()
    if lowered == "databricks":
        return "managed_databricks"
    if lowered.startswith("http://127.0.0.1:") or lowered.startswith("http://localhost:"):
        return "local_http"
    if lowered.startswith("https://"):
        return "https_backend"
    return "configured_backend"


def _package_versions() -> dict[str, str]:
    names = ("mlflow", "mlflow-skinny", "mlflow-tracing", "dspy", "opentelemetry-sdk")
    return {name: importlib.metadata.version(name) for name in names}


def _git_identity() -> dict[str, object]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, check=True, text=True
        ).stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, check=True, text=True)
        dirty = bool(status.stdout)
    except (OSError, subprocess.CalledProcessError):
        return {"revision": "unknown", "dirty": True}
    return {"revision": revision[:64], "dirty": dirty}


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backend_version(uri: str) -> str | None:
    if not uri.startswith(("http://", "https://")):
        return None
    try:
        with urllib.request.urlopen(uri.rstrip("/") + "/version", timeout=3) as response:
            raw = response.read().decode("utf-8").strip()
    except (OSError, urllib.error.URLError, UnicodeDecodeError):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = raw
    value = payload.get("version") if isinstance(payload, dict) else payload if isinstance(payload, str) else None
    return str(value)[:64] if value else None


def _write_once(path: Path, payload: dict[str, object]) -> str:
    """Write one canonical receipt and refuse to replace an existing one."""
    canonical = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    encoded = canonical.encode("utf-8")
    if len(encoded) > _MAX_RECEIPT_BYTES:
        raise CertificationError("MLflow certification receipt exceeds its size bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise CertificationError("MLflow certification receipt already exists") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return hashlib.sha256(encoded).hexdigest()


def _settings_for_backend(args: argparse.Namespace) -> Any:
    settings = load_runtime_settings()
    uri = args.tracking_uri or (settings.mlflow_tracking_uri if args.backend == "local" else "")
    if not uri:
        raise CertificationError("configured backend requires --tracking-uri")
    if args.backend == "local" and _safe_uri_label(uri) != "local_http":
        raise CertificationError("local certification requires a loopback HTTP MLflow endpoint")
    updates: dict[str, object] = {
        "mlflow_tracking_uri": uri,
        "mlflow_experiment_name": args.experiment_name or settings.mlflow_experiment_name,
    }
    if args.trace_catalog:
        updates["mlflow_trace_catalog"] = args.trace_catalog
    if args.trace_schema:
        updates["mlflow_trace_schema"] = args.trace_schema
    if args.trace_table_prefix:
        updates["mlflow_trace_table_prefix"] = args.trace_table_prefix
    if args.sql_warehouse_id:
        updates["mlflow_tracing_sql_warehouse_id"] = args.sql_warehouse_id
    return settings.model_copy(update=updates)


def _trace_payload(trace: Any) -> str:
    try:
        return json.dumps(trace.to_dict(), default=str)
    except Exception:
        return str(trace)


def _trace_status(trace: Any) -> str:
    state = getattr(getattr(trace, "info", None), "state", "")
    return str(getattr(state, "value", state))


def _trace_linkage(
    trace: Any,
    *,
    session_id: str,
    run_id: str,
    trace_id: str,
) -> dict[str, bool]:
    """Validate the fetched trace's identities and the recorded span tree."""
    info = getattr(trace, "info", None)
    tags = getattr(info, "tags", {})
    if not isinstance(tags, Mapping):
        tags = {}
    spans = list(getattr(getattr(trace, "data", None), "spans", ()) or ())
    root_spans = [span for span in spans if str(getattr(span, "name", "")) == "fleet_turn"]
    child_spans = [span for span in spans if str(getattr(span, "name", "")) == "certification_child"]
    root_id = getattr(root_spans[0], "span_id", None) if len(root_spans) == 1 else None
    return {
        "trace_id_matches": str(getattr(info, "trace_id", "")) == trace_id,
        "session_id_tag_matches": tags.get("fleet.session_id") == session_id,
        "run_id_tag_matches": tags.get("fleet.run_id") == run_id,
        "trace_phase_tag_matches": tags.get("fleet.trace_phase") == "execution",
        "root_span_present": len(root_spans) == 1,
        "root_span_is_root": len(root_spans) == 1 and getattr(root_spans[0], "parent_id", None) is None,
        "child_spans_linked": bool(root_id)
        and len(child_spans) == 2
        and all(getattr(span, "parent_id", None) == root_id for span in child_spans),
        "span_trace_ids_match": bool(spans) and all(str(getattr(span, "trace_id", "")) == trace_id for span in spans),
    }


async def _run_trace() -> dict[str, object]:
    import mlflow

    session_id = uuid4()
    run_id = uuid4()
    proxy = DeadlineLMProxy(
        _CertificationLM(),
        deadline=time.monotonic() + 30,
        reserve_seconds=0,
        retries=0,
        error_message="certification deadline exceeded",
    )

    async def child(index: int) -> object:
        with turn_phase_span("certification_child", inputs={"child_index": index}):
            return await proxy.acall(f"child-{index}")

    with turn_trace(session_id, run_id, enabled=True, trace_phase="execution") as handle:
        values = await asyncio.gather(child(0), child(1))
        annotate_trace_io(
            request=f"certification request {_SENTINEL_INPUT}",
            response_text="certification response",
        )
    flush_tracing()
    # The handle belongs to this Turn; the last-active helper may return a
    # prior trace after another root has already closed.
    trace_id = handle.trace_id
    if not trace_id:
        raise CertificationError("MLflow did not expose the certification trace identity")
    trace = mlflow.get_trace(trace_id, flush=True)
    if trace is None:
        raise CertificationError("MLflow did not return the certification trace")
    payload = _trace_payload(trace)
    span_names = [str(getattr(span, "name", "")) for span in getattr(trace.data, "spans", ())]
    token_usage = getattr(trace.info, "token_usage", None)
    linkage = _trace_linkage(
        trace,
        session_id=str(session_id),
        run_id=str(run_id),
        trace_id=str(trace_id),
    )
    return {
        "session_id": str(session_id),
        "run_id": str(run_id),
        "trace_id": str(trace_id),
        "trace_status": _trace_status(trace),
        "span_count": len(span_names),
        "span_names": sorted(span_names),
        "child_count": sum(name == "certification_child" for name in span_names),
        "proxy_span_present": any("DeadlineLMProxy" in name for name in span_names),
        "async_results": len(values) == 2,
        "sentinel_redacted": _SENTINEL not in payload,
        "token_usage": token_usage if isinstance(token_usage, dict) else None,
        "trace_linkage": linkage,
        "trace": trace,
    }


async def _run_concurrent_sessions() -> bool:
    async def one() -> str | None:
        session_id = uuid4()
        with turn_trace(session_id, uuid4(), enabled=True, trace_phase="execution") as handle:
            annotate_trace_io(request="concurrent", response_text="ok")
        return handle.trace_id

    results = await asyncio.gather(one(), one())
    return bool(results[0] and results[1] and results[0] != results[1])


def _run_repeated_lifespans(settings: Any) -> bool:
    """Configure and tear down the exporter twice in one process."""
    results: list[bool] = []
    reset_tracing()
    for _ in range(2):
        active = configure_tracing(settings)
        results.append(active and is_tracing_active())
        flush_tracing()
        reset_tracing()
    return all(results)


def _run_unreachable_backend(settings: Any) -> bool:
    """Verify an unavailable local endpoint fails closed before a Turn span."""
    reset_tracing()
    unavailable = settings.model_copy(update={"mlflow_tracking_uri": "http://127.0.0.1:59999"})
    active = configure_tracing(unavailable)
    reset_tracing()
    return not active


def _feedback(trace_result: dict[str, object], *, content_enabled: bool) -> dict[str, object]:
    session_id = UUID(str(trace_result["session_id"]))
    trace_id = str(trace_result["trace_id"])
    result = TraceFeedbackService().submit(
        session_id=session_id,
        trace_id=trace_id,
        value=True,
        comment=f"verified rationale {_SENTINEL_INPUT}",
        content_enabled=content_enabled,
    )
    import mlflow

    trace = mlflow.get_trace(trace_id, flush=True)
    assessments = getattr(getattr(trace, "info", None), "assessments", ()) if trace else ()
    rationale = [str(getattr(item, "rationale", "")) for item in assessments]
    return {
        "accepted": result.value is True,
        "rationale_present": any(rationale),
        "rationale_redacted": all(_SENTINEL not in item for item in rationale),
    }


def _scenario(status: str, **details: object) -> dict[str, object]:
    return {"status": status, **details}


def _unexercised_scenarios() -> dict[str, dict[str, object]]:
    return {
        name: _scenario("unexercised", reason="requires an injected provider/exporter fault lane")
        for name in (
            "expired_credentials",
            "saturated_export_queue",
            "slow_export",
            "stalled_flush",
        )
    }


def run(args: argparse.Namespace) -> dict[str, object]:
    _require_live()
    load_dotenv(_REPO_ROOT / ".env", override=False)
    settings = _settings_for_backend(args)
    experiment = settings.mlflow_experiment_name
    if not experiment:
        raise CertificationError("MLflow experiment name is required")

    receipt: dict[str, object] = {
        "schema": _SCHEMA,
        "candidate": {**_git_identity(), "config_sha256": _file_digest(_REPO_ROOT / "config" / "fleet.toml")},
        "versions": {"client": _package_versions(), "backend": _backend_version(settings.mlflow_tracking_uri)},
        "target": {
            "backend": args.backend,
            "uri_kind": _safe_uri_label(settings.mlflow_tracking_uri),
            "experiment_present": bool(experiment),
        },
        "policy": {
            "async_logging": bool(settings.mlflow_async_logging),
            "sampling_ratio": float(settings.mlflow_trace_sampling_ratio),
            "content_enabled": bool(settings.mlflow_trace_content_enabled),
            "export_queue_size": int(settings.mlflow_trace_export_queue_size),
            "export_workers": int(settings.mlflow_trace_export_workers),
            "retry_seconds": float(settings.mlflow_trace_export_retry_seconds),
            "http_timeout_seconds": float(settings.mlflow_http_request_timeout_seconds),
        },
        "scenarios": {},
        "settlement": {
            "exporter_failures_change_turn_settlement": False,
            "claim_heartbeat_delay_proof": "unexercised",
            "cancellation_delay_proof": "unexercised",
        },
    }

    if not configure_tracing(settings) or not is_tracing_active():
        receipt["scenarios"] = {"backend_activation": _scenario("failed", reason="tracing_not_active")}
        receipt["promotion"] = {"eligible": False, "reason": "backend_activation_failed"}
        return receipt

    try:
        trace_result = asyncio.run(_run_trace())
        feedback = _feedback(trace_result, content_enabled=bool(settings.mlflow_trace_content_enabled))
        concurrent = asyncio.run(_run_concurrent_sessions())
        repeated = _run_repeated_lifespans(settings)
        unreachable = _run_unreachable_backend(settings)
        linkage = cast(dict[str, bool], trace_result["trace_linkage"])
        proxy_ok = bool(trace_result["proxy_span_present"])
        async_root_child_ok = (
            trace_result["trace_status"] == "OK"
            and trace_result["child_count"] == 2
            and trace_result["async_results"] is True
        )
        linkage_ok = all(linkage.values())
        scenarios = {
            "backend_activation": _scenario("passed"),
            "dspy_autolog_deadline_proxy": _scenario("passed" if proxy_ok else "failed", proxy_span=proxy_ok),
            "async_root_child": _scenario(
                "passed" if async_root_child_ok else "failed",
                root_status=trace_result["trace_status"],
                child_count=trace_result["child_count"],
                async_results=trace_result["async_results"],
            ),
            "sanitized_payload": _scenario("passed" if trace_result["sentinel_redacted"] else "failed"),
            "feedback_rationale": _scenario("passed" if all(feedback.values()) else "failed", **feedback),
            "trace_linkage": _scenario(
                "passed" if linkage_ok else "failed",
                **linkage,
            ),
            "token_aggregation": _scenario(
                "unknown" if trace_result["token_usage"] is None else "passed",
                usage_present=trace_result["token_usage"] is not None,
            ),
            "concurrent_sessions": _scenario("passed" if concurrent else "failed"),
            "repeated_lifespans": _scenario("passed" if repeated else "failed"),
            "unreachable_backend": _scenario("passed" if unreachable else "failed"),
            "sampling_changes": _scenario("unexercised", reason="requires a separate sampling campaign"),
            **_unexercised_scenarios(),
        }
        receipt["scenarios"] = scenarios
        receipt["export"] = {
            "trace_status_ok": trace_result["trace_status"] == "OK",
            "span_count": trace_result["span_count"],
            "sanitized_payload": trace_result["sentinel_redacted"],
            "feedback_rationale": feedback["rationale_redacted"],
            "trace_id_linked": linkage["trace_id_matches"] and linkage["span_trace_ids_match"],
            "token_usage": trace_result["token_usage"],
        }
        receipt["identities"] = {
            "session_id_present": linkage["session_id_tag_matches"],
            "run_id_present": linkage["run_id_tag_matches"],
            "trace_id_present": linkage["trace_id_matches"],
            "span_linkage_checked": linkage["child_spans_linked"],
        }
        receipt["promotion"] = {
            "eligible": all(item.get("status") == "passed" for item in scenarios.values()),
            "reason": "all_required_scenarios_must_pass",
        }
    finally:
        flush_tracing()
        reset_tracing()
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("local", "configured"), default="local")
    parser.add_argument("--tracking-uri")
    parser.add_argument("--experiment-name")
    parser.add_argument("--trace-catalog")
    parser.add_argument("--trace-schema")
    parser.add_argument("--trace-table-prefix")
    parser.add_argument("--sql-warehouse-id")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = run(args)
        body = json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(body + b"\n").hexdigest()
        receipt["receipt_sha256"] = digest
        _write_once(args.output, receipt)
        print(json.dumps({"schema": _SCHEMA, "status": "ok", "receipt_sha256": digest}, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"schema": _SCHEMA, "status": "failed", "error_category": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
