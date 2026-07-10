#!/usr/bin/env python3
"""QRE-301 live end-to-end tracing validation harness.

This script runs a live websocket turn against fleet-rlm, captures conversational
and execution-stream payloads from the unified execution endpoint, and verifies
persistence in session state + Postgres.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import websockets
from sqlalchemy import and_, func, select, text

from fleet_rlm.integrations.database import (
    Artifact,
    DatabaseManager,
    FleetRepository,
    Run,
    RunStatus,
    RunStep,
    select_database_url,
)
from fleet_rlm.integrations.observability.config import MlflowConfig

_DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
_DEFAULT_WORKSPACE_ID = "default"
_DEFAULT_USER_ID = "alice"
_DEFAULT_DOCS_PATH = str(Path(__file__).resolve().parent.parent / "AGENTS.md")
_DEFAULT_OUTPUT_DIR = str(Path(__file__).resolve().parent.parent / "output/phase-04/qre-301")
_DEFAULT_TIMEOUT_SECONDS = 240
_DEFAULT_PROMPT = (
    "Analyze this repository architecture and summarize key execution flows, "
    "focusing on websocket streaming, execution events, and persistence pathways."
)
_PROMOTION_RUNS_PER_BACKEND = 3
_PROMOTION_ARTIFACT_MARKER = "QRE-301-PROMOTION-ARTIFACT-READBACK"


@dataclass(slots=True)
class ValidationResult:
    run_id: str
    session_id: str
    workspace_id: str
    user_id: str
    chat_terminal_kind: str
    execution_step_count: int
    run_status: str
    run_step_count: int
    artifact_count: int
    mlflow_trace_id: str | None
    output_dir: Path
    duration_seconds: float = 0.0
    token_count: int | None = None
    fallback_detected: bool = False


@dataclass(frozen=True, slots=True)
class PromotionRunEvidence:
    """Comparable terminal evidence captured for one configured backend run."""

    backend: str
    run_index: int
    duration_seconds: float
    token_count: int | None
    fallback_detected: bool
    terminal_error: str | None


@dataclass(frozen=True, slots=True)
class PromotionSummary:
    """Median comparison and promotion verdict for the complete local matrix."""

    legacy_duration_median_seconds: float
    direct_duration_median_seconds: float
    legacy_token_median: float | None
    direct_token_median: float | None
    duration_regression_ratio: float | None
    token_regression_ratio: float | None
    passed: bool
    failure_reasons: tuple[str, ...]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run QRE-301 live end-to-end tracing validation.")
    parser.add_argument("--server-url", default=_DEFAULT_SERVER_URL)
    parser.add_argument("--workspace-id", default=_DEFAULT_WORKSPACE_ID)
    parser.add_argument("--user-id", default=_DEFAULT_USER_ID)
    parser.add_argument("--session-id")
    parser.add_argument("--docs-path", default=_DEFAULT_DOCS_PATH)
    parser.add_argument("--prompt", default=_DEFAULT_PROMPT)
    parser.add_argument("--timeout-seconds", type=int, default=_DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--output-dir", default=_DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--verify-mlflow",
        action="store_true",
        help="Assert mlflow_trace_id in the terminal payload and verify the trace exists via mlflow CLI.",
    )
    parser.add_argument(
        "--require-mlflow-trace-id",
        action="store_true",
        help="Fail when the terminal payload does not include mlflow_trace_id (respects MLFLOW_ENABLED=false skip).",
    )
    parser.add_argument(
        "--promotion-gate",
        action="store_true",
        help=(
            "Run three legacy and three direct-RLM local turns against separately configured servers, "
            "then persist a median-based promotion verdict."
        ),
    )
    parser.add_argument(
        "--legacy-server-url",
        help="URL of a local server started with EXECUTION_BACKEND=legacy_agent_runtime.",
    )
    parser.add_argument(
        "--direct-server-url",
        help="URL of a local server started with EXECUTION_BACKEND=direct_rlm.",
    )
    parser.add_argument(
        "--max-promotion-regression-ratio",
        type=float,
        default=0.25,
        help="Maximum allowed direct-vs-legacy median duration or token increase (default: 0.25).",
    )
    return parser.parse_args()


def _nested_mappings(payload: Any) -> list[dict[str, Any]]:
    """Return every mapping nested in an untrusted transport payload."""
    mappings: list[dict[str, Any]] = []
    pending = [payload]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            mappings.append(current)
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return mappings


def _extract_token_count(*payloads: Any) -> int | None:
    """Prefer a terminal total token count, falling back to usage records."""
    totals: list[int] = []
    parts: list[int] = []
    for payload in payloads:
        for mapping in _nested_mappings(payload):
            for key in ("total_tokens", "token_count", "tokens"):
                value = mapping.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    totals.append(value)
            input_tokens = mapping.get("input_tokens")
            output_tokens = mapping.get("output_tokens")
            if (
                isinstance(input_tokens, int)
                and not isinstance(input_tokens, bool)
                and isinstance(output_tokens, int)
                and not isinstance(output_tokens, bool)
            ):
                total = max(0, input_tokens) + max(0, output_tokens)
                if total > 0:
                    parts.append(total)
    if totals:
        return max(totals)
    return sum(parts) if parts else None


def _detect_fallback(*payloads: Any) -> bool:
    """Detect an explicit fallback/degraded terminal path without guessing from prose."""
    for payload in payloads:
        for mapping in _nested_mappings(payload):
            if any(
                mapping.get(key) is True
                for key in ("fallback", "fallback_used", "degraded", "runtime_degraded", "runtime_fallback_used")
            ):
                return True
            status = str(mapping.get("status", "")).lower()
            if status in {"fallback", "degraded"}:
                return True
    return False


def _terminal_error_from_payload(terminal_payload: dict[str, Any]) -> str | None:
    if terminal_payload.get("type") == "error":
        return str(terminal_payload.get("message") or "terminal websocket error")
    for mapping in _nested_mappings(terminal_payload):
        if str(mapping.get("kind", "")).lower() == "error":
            return str(mapping.get("error") or mapping.get("message") or "terminal runtime error")
    return None


def _extract_run_evidence(
    *,
    backend: str,
    run_index: int,
    duration_seconds: float,
    terminal_payload: dict[str, Any],
    execution_events: list[dict[str, Any]],
) -> PromotionRunEvidence:
    """Normalize terminal wire evidence into a persistence-friendly row."""
    return PromotionRunEvidence(
        backend=backend,
        run_index=run_index,
        duration_seconds=round(duration_seconds, 6),
        token_count=_extract_token_count(terminal_payload, execution_events),
        fallback_detected=_detect_fallback(terminal_payload, execution_events),
        terminal_error=_terminal_error_from_payload(terminal_payload),
    )


def _ratio(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None or baseline <= 0:
        return None
    return (candidate - baseline) / baseline


def _median_token_count(rows: list[PromotionRunEvidence]) -> float | None:
    values = [row.token_count for row in rows if row.token_count is not None]
    return float(statistics.median(values)) if values else None


def _build_promotion_summary(
    legacy_rows: list[PromotionRunEvidence],
    direct_rows: list[PromotionRunEvidence],
    *,
    max_regression_ratio: float,
) -> PromotionSummary:
    """Compare complete backend matrices without treating missing evidence as a pass."""
    if len(legacy_rows) != _PROMOTION_RUNS_PER_BACKEND or len(direct_rows) != _PROMOTION_RUNS_PER_BACKEND:
        raise ValueError(f"Promotion gate requires exactly {_PROMOTION_RUNS_PER_BACKEND} runs per backend.")
    if max_regression_ratio < 0:
        raise ValueError("max_regression_ratio must be non-negative.")

    legacy_duration = float(statistics.median(row.duration_seconds for row in legacy_rows))
    direct_duration = float(statistics.median(row.duration_seconds for row in direct_rows))
    legacy_tokens = _median_token_count(legacy_rows)
    direct_tokens = _median_token_count(direct_rows)
    duration_ratio = _ratio(direct_duration, legacy_duration)
    token_ratio = _ratio(direct_tokens, legacy_tokens)
    reasons: list[str] = []
    for row in [*legacy_rows, *direct_rows]:
        if row.terminal_error:
            reasons.append(f"{row.backend} run {row.run_index} terminal error: {row.terminal_error}")
        if row.fallback_detected:
            reasons.append(f"{row.backend} run {row.run_index} used a fallback/degraded path.")
    if duration_ratio is not None and duration_ratio > max_regression_ratio:
        reasons.append(f"duration regression {duration_ratio:.1%} exceeds {max_regression_ratio:.1%} threshold.")
    if token_ratio is None:
        reasons.append("token evidence is missing for one or both backend matrices.")
    elif token_ratio > max_regression_ratio:
        reasons.append(f"token regression {token_ratio:.1%} exceeds {max_regression_ratio:.1%} threshold.")
    return PromotionSummary(
        legacy_duration_median_seconds=legacy_duration,
        direct_duration_median_seconds=direct_duration,
        legacy_token_median=legacy_tokens,
        direct_token_median=direct_tokens,
        duration_regression_ratio=duration_ratio,
        token_regression_ratio=token_ratio,
        passed=not reasons,
        failure_reasons=tuple(reasons),
    )


def _with_promotion_failure(summary: PromotionSummary, reason: str) -> PromotionSummary:
    """Preserve an orchestration failure in the immutable matrix verdict."""
    return replace(summary, passed=False, failure_reasons=(*summary.failure_reasons, reason))


def _validate_promotion_targets(*, legacy_server_url: str | None, direct_server_url: str | None) -> None:
    if not legacy_server_url or not direct_server_url:
        raise ValueError("Promotion mode requires --legacy-server-url and --direct-server-url.")
    if legacy_server_url.rstrip("/") == direct_server_url.rstrip("/"):
        raise ValueError("Promotion mode requires distinct legacy and direct server targets.")


def _validate_promotion_prerequisites() -> None:
    required = ("DAYTONA_API_KEY", "DSPY_LM_MODEL", "DSPY_LM_API_KEY", "MLFLOW_TRACKING_URI")
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if not (os.getenv("DATABASE_URL", "").strip() or os.getenv("DATABASE_ADMIN_URL", "").strip()):
        missing.append("DATABASE_URL or DATABASE_ADMIN_URL")
    if not MlflowConfig.from_env().enabled:
        missing.append("MLFLOW_ENABLED=true")
    if missing:
        raise RuntimeError("Promotion prerequisites missing: " + ", ".join(missing))


def _extract_mlflow_trace_id(terminal_payload: dict[str, Any]) -> str | None:
    candidates: list[dict[str, Any]] = []
    data = terminal_payload.get("data")
    if isinstance(data, dict):
        candidates.append(data)
        nested = data.get("payload")
        if isinstance(nested, dict):
            candidates.append(nested)
    nested_payload = terminal_payload.get("payload")
    if isinstance(nested_payload, dict):
        candidates.append(nested_payload)

    for container in candidates:
        for key in ("mlflow_trace_id", "mlflowTraceId"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _verify_mlflow_trace_exists(trace_id: str) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5001").strip()
    env = {**os.environ, "MLFLOW_TRACKING_URI": tracking_uri}
    result = subprocess.run(
        ["uv", "run", "mlflow", "traces", "get", "--trace-id", trace_id],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"mlflow traces get failed for {trace_id}: {detail}")


def _make_ws_url(server_url: str, path: str, query: str = "") -> str:
    parsed = urlparse(server_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_parsed = parsed._replace(
        scheme=scheme,
        path=path,
        params="",
        query=query,
        fragment="",
    )
    return urlunparse(ws_parsed)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""))


async def _assert_ready_and_runtime_status(
    client: httpx.AsyncClient,
    output_dir: Path,
    *,
    expected_backend: str | None = None,
) -> dict[str, Any]:
    ready_resp = await client.get("/ready")
    ready_resp.raise_for_status()
    ready = ready_resp.json()
    if ready.get("planner") != "ready":
        raise RuntimeError(f"/ready planner is not ready: {ready}")
    if ready.get("database") != "ready":
        raise RuntimeError(f"/ready database is not ready: {ready}")

    runtime_status_resp = await client.get("/api/v1/runtime/status")
    runtime_status_resp.raise_for_status()
    runtime_status = runtime_status_resp.json()
    actual_backend = str(runtime_status.get("execution_backend") or "").strip()
    if expected_backend and actual_backend != expected_backend:
        raise RuntimeError(
            "Runtime status execution_backend mismatch: "
            f"expected {expected_backend!r}, got {actual_backend or 'missing'!r}."
        )
    _write_json(output_dir / "runtime-status.json", runtime_status)
    return runtime_status


async def _collect_chat_until_terminal(
    chat_ws: Any, timeout_seconds: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        remaining = max(0.1, deadline - asyncio.get_running_loop().time())
        raw = await asyncio.wait_for(chat_ws.recv(), timeout=remaining)
        payload = json.loads(raw)
        events.append(payload)

        if payload.get("type") == "error":
            raise RuntimeError(f"Chat websocket error: {payload}")

        if payload.get("type") == "execution_completed":
            return events, payload

        if payload.get("type") != "event":
            continue
        kind = payload.get("data", {}).get("kind")
        if kind in {"final", "error", "cancelled"}:
            return events, payload

    raise TimeoutError("Timed out waiting for terminal chat event")


async def _collect_execution_until_completed(execution_ws: Any, timeout_seconds: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        remaining = max(0.1, deadline - asyncio.get_running_loop().time())
        raw = await asyncio.wait_for(execution_ws.recv(), timeout=remaining)
        payload = json.loads(raw)
        events.append(payload)
        if payload.get("type") == "execution_completed":
            return events

    raise TimeoutError("Timed out waiting for execution_completed event")


async def _persist_artifact_via_command(
    chat_ws: Any,
    *,
    workspace_id: str,
    user_id: str,
    session_id: str,
) -> None:
    command_message = {
        "type": "command",
        "command": "write_to_file",
        "args": {
            "path": f"/data/workspaces/{workspace_id}/users/{user_id}/artifacts/{session_id}-qre301.txt",
            "content": "QRE-301 artifact persistence probe",
            "append": False,
        },
        "session_id": session_id,
    }
    await chat_ws.send(json.dumps(command_message))
    command_raw = await asyncio.wait_for(chat_ws.recv(), timeout=30)
    command_result = json.loads(command_raw)
    if command_result.get("type") != "command_result":
        raise RuntimeError(f"Unexpected command response envelope: {command_result}")
    if command_result.get("result", {}).get("status") != "ok":
        raise RuntimeError(f"write_to_file command failed: {command_result}")

    # Trigger a local persist pass after command side effects.
    cancel_msg = {
        "type": "cancel",
        "session_id": session_id,
    }
    await chat_ws.send(json.dumps(cancel_msg))
    cancel_raw = await asyncio.wait_for(chat_ws.recv(), timeout=30)
    cancel_payload = json.loads(cancel_raw)
    if cancel_payload.get("type") != "cancelled":
        raise RuntimeError(f"Unexpected cancel response: {cancel_payload}")


async def _fetch_session_summary(
    client: httpx.AsyncClient,
    *,
    workspace_id: str,
    user_id: str,
    session_id: str,
    output_dir: Path,
) -> dict[str, Any]:
    response = await client.get("/api/v1/sessions/state")
    response.raise_for_status()
    payload = response.json()
    _write_json(output_dir / "session-state.json", payload)

    for session in payload.get("sessions", []):
        if (
            session.get("workspace_id") == workspace_id
            and session.get("user_id") == user_id
            and session.get("session_id") == session_id
        ):
            if int(session.get("history_turns", 0)) < 1:
                raise RuntimeError("Session summary found but history_turns < 1; expected persisted chat turn.")
            return session

    raise RuntimeError("Target session not found in /api/v1/sessions/state for QRE-301 validation.")


async def _fetch_trace_debug(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    trace_id: str | None,
    output_dir: Path,
) -> dict[str, Any]:
    """Capture the session-scoped trace-debug projection for promotion evidence."""
    params = {"trace_id": trace_id} if trace_id else None
    response = await client.get(f"/api/v1/sessions/{session_id}/trace-debug", params=params)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("trace-debug endpoint returned a non-object payload.")
    _validate_trace_debug_payload(payload, expected_trace_id=trace_id)
    _write_json(output_dir / "trace-debug.json", payload)
    return payload


def _validate_trace_debug_payload(payload: dict[str, Any], *, expected_trace_id: str | None) -> None:
    """Require a resolved trace with spans and measurable performance evidence."""
    trace_id = str(payload.get("trace_id") or "").strip()
    if not trace_id:
        raise RuntimeError("trace-debug payload is missing a trace id.")
    if expected_trace_id and trace_id != expected_trace_id:
        raise RuntimeError("trace-debug payload trace id does not match the terminal trace id.")

    spans = payload.get("spans")
    span_count = payload.get("span_count")
    if not isinstance(spans, list) or not spans or not isinstance(span_count, int) or span_count != len(spans):
        raise RuntimeError("trace-debug payload is missing resolved spans.")

    performance = payload.get("performance_summary")
    if not isinstance(performance, dict):
        raise RuntimeError("trace-debug payload is missing a performance summary.")
    numeric_fields = ("total_duration_ms", "llm_duration_ms", "repl_duration_ms", "tool_duration_ms", "total_tokens")
    if not any(
        isinstance(performance.get(field), int)
        and not isinstance(performance.get(field), bool)
        and performance[field] > 0
        for field in numeric_fields
    ):
        raise RuntimeError("trace-debug payload has no measurable performance evidence.")


async def _upload_promotion_attachment(
    client: httpx.AsyncClient,
    *,
    session_id: str,
) -> tuple[str, str]:
    """Stage a synthetic attachment through the public ID-only upload contract."""
    sentinel = f"QRE-301 promotion attachment sentinel for session {session_id}.\n".encode("utf-8")
    expected_checksum = hashlib.sha256(sentinel).hexdigest()
    response = await client.post(
        "/api/v1/files/upload",
        data={"session_id": session_id},
        files={"file": ("qre301-promotion-sentinel.md", sentinel, "text/markdown")},
    )
    response.raise_for_status()
    payload = response.json()
    attachment = payload.get("attachment") if isinstance(payload, dict) else None
    if not isinstance(attachment, dict):
        raise RuntimeError("Attachment upload did not return attachment metadata.")
    attachment_id = str(attachment.get("id") or "").strip()
    checksum = str(attachment.get("checksum") or "").strip()
    if not attachment_id or checksum != expected_checksum:
        raise RuntimeError("Synthetic promotion attachment checksum or ID did not round-trip.")
    return attachment_id, checksum


def _assert_artifact_readback(
    execution_events: list[dict[str, Any]],
    *,
    marker: str,
    checksum: str,
) -> None:
    """Require a bounded artifact-tool readback, not merely a model claim."""
    def decoded_mapping(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return None
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            try:
                decoded = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                return None
        return decoded if isinstance(decoded, dict) else None

    def matches(decoded: dict[str, Any] | None) -> bool:
        if decoded is None:
            return False
        content = decoded.get("content")
        artifact = decoded.get("artifact")
        artifact_ref = artifact.get("ref") if isinstance(artifact, dict) else None
        resolved_checksum = (artifact.get("checksum") if isinstance(artifact, dict) else None) or (
            artifact_ref.get("checksum") if isinstance(artifact_ref, dict) else None
        )
        return (
            isinstance(content, str)
            and marker in content
            and decoded.get("artifact_backed") is True
            and str(resolved_checksum or "") == checksum
        )

    for event in execution_events:
        step = event.get("step")
        if not isinstance(step, dict):
            continue
        step_input = step.get("input")
        if not isinstance(step_input, dict):
            continue
        if (
            str(step_input.get("event_kind") or "").lower() == "tool_result"
            and str(step_input.get("tool_name") or "") == "read_artifact"
            and matches(decoded_mapping(step.get("output")))
        ):
            return

    # Preserve compatibility with pre-ExecutionStep event payloads while the
    # promotion harness accepts evidence from older local servers.
    for mapping in _nested_mappings(execution_events):
        is_tool_result = str(mapping.get("event_kind") or mapping.get("kind") or "").lower() == "tool_result"
        if not is_tool_result or str(mapping.get("tool_name") or "") != "read_artifact":
            continue
        for field in ("tool_output", "output", "result", "observation"):
            if matches(decoded_mapping(mapping.get(field))):
                return
    raise RuntimeError("Promotion artifact readback is missing the expected marker or checksum.")


async def _verify_db_persistence(
    *,
    database_url: str,
    workspace_id: str,
    user_id: str,
    run_id: str,
    output_dir: Path,
    require_artifact: bool = True,
) -> dict[str, Any]:
    db = DatabaseManager(database_url)
    repo = FleetRepository(db)
    identity = await repo.upsert_identity(
        entra_tenant_id=workspace_id,
        entra_user_id=user_id,
        email=f"{user_id}@example.com",
        full_name="QRE 301 Validation",
    )

    verification: dict[str, Any] = {
        "tenant_id": str(identity.tenant_id),
        "run_id_external": run_id,
        "run_found": False,
        "run_status": None,
        "run_step_count": 0,
        "artifact_count": 0,
    }

    try:
        async with db.session() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(identity.tenant_id)},
                )
                run = (
                    await session.execute(
                        select(Run).where(
                            and_(
                                Run.tenant_id == identity.tenant_id,
                                Run.external_run_id == run_id,
                            )
                        )
                    )
                ).scalar_one_or_none()
                if run is None:
                    raise RuntimeError(f"Run not found in DB for external_run_id={run_id!r}.")
                verification["run_found"] = True
                verification["run_status"] = run.status.value
                if run.status != RunStatus.COMPLETED:
                    raise RuntimeError(f"Run status is {run.status.value!r}, expected 'completed'.")

                run_step_count = (
                    await session.execute(
                        select(func.count())
                        .select_from(RunStep)
                        .where(
                            and_(
                                RunStep.tenant_id == identity.tenant_id,
                                RunStep.run_id == run.id,
                            )
                        )
                    )
                ).scalar_one()
                verification["run_step_count"] = int(run_step_count or 0)
                if verification["run_step_count"] <= 0:
                    raise RuntimeError("No run_steps persisted for validated run.")

                artifact_count = (
                    await session.execute(
                        select(func.count())
                        .select_from(Artifact)
                        .where(
                            and_(
                                Artifact.tenant_id == identity.tenant_id,
                                Artifact.run_id == run.id,
                            )
                        )
                    )
                ).scalar_one()
                verification["artifact_count"] = int(artifact_count or 0)
                if require_artifact and verification["artifact_count"] <= 0:
                    raise RuntimeError("No artifacts persisted for validated run after write_to_file.")
    finally:
        await db.dispose()

    _write_json(output_dir / "db-verification.json", verification)
    return verification


async def _run_validation(
    args: argparse.Namespace,
    *,
    server_url: str | None = None,
    session_id: str | None = None,
    selected_skill_ids: list[str] | None = None,
    attachment_refs: list[str] | None = None,
    artifact_marker: str | None = None,
    artifact_checksum: str | None = None,
    require_trace_debug: bool = False,
    require_persisted_artifact: bool = True,
    require_mlflow: bool = False,
    verify_mlflow_trace: bool = False,
    expected_backend: str | None = None,
    prompt: str | None = None,
) -> ValidationResult:
    database_url = select_database_url(
        runtime_url=os.getenv("DATABASE_URL"),
        admin_url=os.getenv("DATABASE_ADMIN_URL"),
        prefer_admin=True,
    )
    if not database_url:
        raise RuntimeError("DATABASE_ADMIN_URL or DATABASE_URL must be set for DB persistence verification.")

    effective_server_url = server_url or args.server_url
    session_id = session_id or args.session_id or f"qre301-{uuid.uuid4().hex[:10]}"
    mlflow_trace_id: str | None = None
    started_at = time.monotonic()
    timestamp_str = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir) / f"{timestamp_str}-{session_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    headers = {
        "X-Debug-Tenant-Id": args.workspace_id,
        "X-Debug-User-Id": args.user_id,
        "X-Debug-Email": f"{args.user_id}@example.com",
        "X-Debug-Name": "QRE 301 Validation",
    }
    ws_headers = list(headers.items())

    async with httpx.AsyncClient(base_url=effective_server_url, timeout=30.0, headers=headers) as client:
        runtime_status = await _assert_ready_and_runtime_status(
            client,
            output_dir,
            expected_backend=expected_backend,
        )

        chat_ws_url = _make_ws_url(effective_server_url, "/api/v1/ws/execution")
        execution_ws_url = _make_ws_url(
            effective_server_url,
            "/api/v1/ws/execution/events",
            query=f"session_id={session_id}",
        )

        async with websockets.connect(
            execution_ws_url,
            additional_headers=ws_headers,
            max_size=4 * 1024 * 1024,
        ) as execution_ws:
            async with websockets.connect(
                chat_ws_url,
                additional_headers=ws_headers,
                max_size=4 * 1024 * 1024,
            ) as chat_ws:
                message = {
                    "type": "message",
                    "content": prompt or args.prompt,
                    "docs_path": args.docs_path,
                    "trace": True,
                    "session_id": session_id,
                }
                if selected_skill_ids:
                    message["selected_skill_ids"] = selected_skill_ids
                if attachment_refs:
                    message["attachment_refs"] = attachment_refs
                await chat_ws.send(json.dumps(message))

                chat_task = asyncio.create_task(_collect_chat_until_terminal(chat_ws, args.timeout_seconds))
                execution_task = asyncio.create_task(
                    _collect_execution_until_completed(execution_ws, args.timeout_seconds)
                )
                chat_events_with_terminal, execution_events = await asyncio.gather(chat_task, execution_task)
                chat_events, terminal_chat_payload = chat_events_with_terminal

                _write_jsonl(output_dir / "chat-events.jsonl", chat_events)
                _write_jsonl(output_dir / "execution-events.jsonl", execution_events)

                execution_started = next(
                    (event for event in execution_events if event.get("type") == "execution_started"),
                    None,
                )
                if execution_started is None:
                    raise RuntimeError("execution_started event missing from stream.")
                run_id = str(execution_started.get("run_id", "")).strip()
                if not run_id:
                    raise RuntimeError("execution_started event did not include run_id.")

                step_events = [event for event in execution_events if event.get("type") == "execution_step"]
                if not step_events:
                    raise RuntimeError("No execution_step events captured.")

                if any(event.get("run_id") != run_id for event in execution_events):
                    raise RuntimeError("Execution stream contains inconsistent run_id values.")

                for event in execution_events:
                    if event.get("workspace_id") != args.workspace_id:
                        raise RuntimeError("Execution event workspace_id mismatch.")
                    if event.get("user_id") != args.user_id:
                        raise RuntimeError("Execution event user_id mismatch.")
                    if event.get("session_id") != session_id:
                        raise RuntimeError("Execution event session_id mismatch.")

                terminal_kind = terminal_chat_payload.get("data", {}).get("kind") or terminal_chat_payload.get("type")
                if terminal_kind not in {"final", "execution_completed"}:
                    raise RuntimeError(
                        f"Terminal chat event kind is {terminal_kind!r}; expected 'final' or 'execution_completed'."
                    )

                mlflow_trace_id = _extract_mlflow_trace_id(terminal_chat_payload)
                if MlflowConfig.from_env().enabled and (
                    args.verify_mlflow or args.require_mlflow_trace_id or require_mlflow
                ):
                    if not mlflow_trace_id:
                        raise RuntimeError("Terminal chat payload is missing mlflow_trace_id.")
                    if args.verify_mlflow or verify_mlflow_trace:
                        _verify_mlflow_trace_exists(mlflow_trace_id)
                elif not MlflowConfig.from_env().enabled:
                    mlflow_trace_id = None

                if artifact_marker and artifact_checksum:
                    _assert_artifact_readback(
                        execution_events,
                        marker=artifact_marker,
                        checksum=artifact_checksum,
                    )

        _ = await _fetch_session_summary(
            client,
            workspace_id=args.workspace_id,
            user_id=args.user_id,
            session_id=session_id,
            output_dir=output_dir,
        )
        if require_trace_debug:
            await _fetch_trace_debug(
                client,
                session_id=session_id,
                trace_id=mlflow_trace_id,
                output_dir=output_dir,
            )

    db_verification = await _verify_db_persistence(
        database_url=database_url,
        workspace_id=args.workspace_id,
        user_id=args.user_id,
        run_id=run_id,
        output_dir=output_dir,
        require_artifact=require_persisted_artifact,
    )

    duration_seconds = time.monotonic() - started_at
    evidence = _extract_run_evidence(
        backend="unknown",
        run_index=0,
        duration_seconds=duration_seconds,
        terminal_payload=terminal_chat_payload,
        execution_events=execution_events,
    )

    summary_lines = [
        "# QRE-301 Live Validation Summary",
        "",
        f"- Timestamp: `{datetime.now(UTC).isoformat()}`",
        f"- Server URL: `{effective_server_url}`",
        f"- Configured execution backend: `{runtime_status.get('execution_backend', 'n/a')}`",
        f"- Workspace/User/Session: `{args.workspace_id}/{args.user_id}/{session_id}`",
        f"- docs_path: `{args.docs_path}`",
        f"- run_id: `{run_id}`",
        f"- chat terminal kind: `{terminal_kind}`",
        f"- mlflow_trace_id: `{mlflow_trace_id or 'n/a'}`",
        f"- execution_step events: `{len(step_events)}`",
        f"- DB run status: `{db_verification['run_status']}`",
        f"- DB run_steps: `{db_verification['run_step_count']}`",
        f"- DB artifacts: `{db_verification['artifact_count']}`",
        f"- Duration seconds: `{duration_seconds:.3f}`",
        f"- Token count: `{evidence.token_count if evidence.token_count is not None else 'n/a'}`",
        f"- Fallback/degraded path: `{evidence.fallback_detected}`",
        "",
        "## Evidence Files",
        f"- `{output_dir / 'chat-events.jsonl'}`",
        f"- `{output_dir / 'execution-events.jsonl'}`",
        f"- `{output_dir / 'runtime-status.json'}`",
        f"- `{output_dir / 'session-state.json'}`",
        f"- `{output_dir / 'db-verification.json'}`",
    ]
    (output_dir / "summary.md").write_text("\n".join(summary_lines) + "\n")

    return ValidationResult(
        run_id=run_id,
        session_id=session_id,
        workspace_id=args.workspace_id,
        user_id=args.user_id,
        chat_terminal_kind=str(terminal_kind),
        execution_step_count=len(step_events),
        run_status=str(db_verification["run_status"]),
        run_step_count=int(db_verification["run_step_count"]),
        artifact_count=int(db_verification["artifact_count"]),
        mlflow_trace_id=mlflow_trace_id,
        output_dir=output_dir,
        duration_seconds=duration_seconds,
        token_count=evidence.token_count,
        fallback_detected=evidence.fallback_detected,
    )


def _promotion_headers(args: argparse.Namespace) -> dict[str, str]:
    return {
        "X-Debug-Tenant-Id": args.workspace_id,
        "X-Debug-User-Id": args.user_id,
        "X-Debug-Email": f"{args.user_id}@example.com",
        "X-Debug-Name": "QRE 301 Promotion Validation",
    }


def _promotion_evidence_payload(
    *,
    rows: list[PromotionRunEvidence],
    summary: PromotionSummary,
    output_dirs: list[Path],
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "runs": [
            {
                "backend": row.backend,
                "run_index": row.run_index,
                "duration_seconds": row.duration_seconds,
                "token_count": row.token_count,
                "fallback_detected": row.fallback_detected,
                "terminal_error": row.terminal_error,
            }
            for row in rows
        ],
        "comparison": {
            "legacy_duration_median_seconds": summary.legacy_duration_median_seconds,
            "direct_duration_median_seconds": summary.direct_duration_median_seconds,
            "legacy_token_median": summary.legacy_token_median,
            "direct_token_median": summary.direct_token_median,
            "duration_regression_ratio": summary.duration_regression_ratio,
            "token_regression_ratio": summary.token_regression_ratio,
            "passed": summary.passed,
            "failure_reasons": list(summary.failure_reasons),
        },
        "run_evidence_dirs": [str(path) for path in output_dirs],
    }


async def _run_promotion_gate(args: argparse.Namespace) -> PromotionSummary:
    """Run the explicit six-turn local promotion matrix and persist raw evidence."""
    _validate_promotion_targets(
        legacy_server_url=args.legacy_server_url,
        direct_server_url=args.direct_server_url,
    )
    _validate_promotion_prerequisites()
    assert args.legacy_server_url is not None
    assert args.direct_server_url is not None

    promotion_root = Path(args.output_dir) / f"promotion-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    promotion_root.mkdir(parents=True, exist_ok=True)
    rows: list[PromotionRunEvidence] = []
    output_dirs: list[Path] = []

    async def record_run(
        *,
        backend: str,
        run_index: int,
        server_url: str,
        session_id: str | None = None,
        selected_skill_ids: list[str] | None = None,
        attachment_refs: list[str] | None = None,
        artifact_marker: str | None = None,
        artifact_checksum: str | None = None,
        require_trace_debug: bool = False,
        prompt: str | None = None,
    ) -> None:
        started_at = time.monotonic()
        try:
            result = await _run_validation(
                args,
                server_url=server_url,
                session_id=session_id,
                selected_skill_ids=selected_skill_ids,
                attachment_refs=attachment_refs,
                artifact_marker=artifact_marker,
                artifact_checksum=artifact_checksum,
                require_trace_debug=require_trace_debug,
                require_persisted_artifact=False,
                require_mlflow=True,
                verify_mlflow_trace=True,
                expected_backend=backend,
                prompt=prompt,
            )
        except Exception as exc:
            rows.append(
                PromotionRunEvidence(
                    backend=backend,
                    run_index=run_index,
                    duration_seconds=round(time.monotonic() - started_at, 6),
                    token_count=None,
                    fallback_detected=False,
                    terminal_error=str(exc),
                )
            )
            return
        output_dirs.append(result.output_dir)
        rows.append(
            PromotionRunEvidence(
                backend=backend,
                run_index=run_index,
                duration_seconds=round(result.duration_seconds, 6),
                token_count=result.token_count,
                fallback_detected=result.fallback_detected,
                terminal_error=None,
            )
        )

    artifact_content = f"# QRE-301 promotion artifact\n\n{_PROMOTION_ARTIFACT_MARKER}\n"
    artifact_checksum = hashlib.sha256(artifact_content.encode("utf-8")).hexdigest()
    backend_targets = (
        ("legacy_agent_runtime", args.legacy_server_url),
        ("direct_rlm", args.direct_server_url),
    )
    session_ids: dict[str, str] = {}
    for backend, server_url in backend_targets:
        session_id = f"qre301-promotion-{backend}-{uuid.uuid4().hex[:10]}"
        session_ids[backend] = session_id
        async with httpx.AsyncClient(
            base_url=server_url,
            timeout=30.0,
            headers=_promotion_headers(args),
        ) as client:
            attachment_id, _attachment_checksum = await _upload_promotion_attachment(
                client,
                session_id=session_id,
            )
        for run_index in range(1, _PROMOTION_RUNS_PER_BACKEND + 1):
            scenario_prompt = (
                f"{args.prompt}\n\n"
                "Promotion-gate scenario: use the selected long-context skill and the staged sentinel attachment. "
                "Create a session-scoped Markdown artifact with create_artifact(category='reports', "
                f"relative_path='qre301-promotion-{run_index}.md') containing exactly this Markdown content:\n"
                f"{artifact_content}"
                "Then call read_artifact for that artifact and include its readback in the tool result. "
                "Do not take a fallback or degraded execution path."
            )
            await record_run(
                backend=backend,
                run_index=run_index,
                server_url=server_url,
                session_id=session_id,
                selected_skill_ids=["long-context"],
                attachment_refs=[attachment_id],
                artifact_marker=_PROMOTION_ARTIFACT_MARKER,
                artifact_checksum=artifact_checksum,
                require_trace_debug=True,
                prompt=scenario_prompt,
            )

    summary = _build_promotion_summary(
        [row for row in rows if row.backend == "legacy_agent_runtime"],
        [row for row in rows if row.backend == "direct_rlm" and row.run_index <= _PROMOTION_RUNS_PER_BACKEND],
        max_regression_ratio=args.max_promotion_regression_ratio,
    )
    for backend, server_url in backend_targets:
        async with httpx.AsyncClient(
            base_url=server_url,
            timeout=30.0,
            headers=_promotion_headers(args),
        ) as client:
            resumed = await _fetch_session_summary(
                client,
                workspace_id=args.workspace_id,
                user_id=args.user_id,
                session_id=session_ids[backend],
                output_dir=promotion_root,
            )
        if int(resumed.get("history_turns", 0)) < _PROMOTION_RUNS_PER_BACKEND:
            summary = _with_promotion_failure(summary, f"resumed {backend} session did not retain all three turns")
    evidence = _promotion_evidence_payload(rows=rows, summary=summary, output_dirs=output_dirs)
    _write_json(promotion_root / "promotion-evidence.json", evidence)
    failure_lines = [f"- {reason}" for reason in summary.failure_reasons] or ["- none"]
    summary_lines = [
        "# QRE-301 Direct-RLM Promotion Gate",
        "",
        f"- Status: `{'passed' if summary.passed else 'failed'}`",
        f"- Legacy median duration: `{summary.legacy_duration_median_seconds:.3f}s`",
        f"- Direct median duration: `{summary.direct_duration_median_seconds:.3f}s`",
        f"- Legacy median tokens: `{summary.legacy_token_median if summary.legacy_token_median is not None else 'n/a'}`",
        f"- Direct median tokens: `{summary.direct_token_median if summary.direct_token_median is not None else 'n/a'}`",
        f"- Duration regression: `{summary.duration_regression_ratio if summary.duration_regression_ratio is not None else 'n/a'}`",
        f"- Token regression: `{summary.token_regression_ratio if summary.token_regression_ratio is not None else 'n/a'}`",
        "",
        "## Failures",
        *failure_lines,
    ]
    (promotion_root / "summary.md").write_text("\n".join(summary_lines) + "\n")
    if not summary.passed:
        raise RuntimeError(f"Promotion gate failed; evidence: {promotion_root}")
    print(f"QRE-301 promotion gate passed; evidence: {promotion_root}")
    return summary


def _print_success(result: ValidationResult) -> None:
    print("QRE-301 validation passed")
    print(f"  run_id: {result.run_id}")
    print(f"  session: {result.workspace_id}/{result.user_id}/{result.session_id}")
    print(f"  terminal chat event: {result.chat_terminal_kind}")
    if result.mlflow_trace_id:
        print(f"  mlflow_trace_id: {result.mlflow_trace_id}")
    print(f"  execution steps: {result.execution_step_count}")
    print(
        "  DB persistence: "
        f"status={result.run_status}, steps={result.run_step_count}, "
        f"artifacts={result.artifact_count}"
    )
    print(f"  evidence: {result.output_dir}")


def main() -> int:
    args = _parse_args()
    try:
        if args.promotion_gate:
            asyncio.run(_run_promotion_gate(args))
            return 0
        result = asyncio.run(_run_validation(args))
    except KeyboardInterrupt:
        print("QRE-301 validation cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"QRE-301 validation failed: {exc}", file=sys.stderr)
        return 1

    _print_success(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
