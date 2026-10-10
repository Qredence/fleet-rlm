"""Opt-in end-to-end proof of the Daytona-backed Fleet RLM MVP."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.metadata
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.local_scope import LocalScope
from fleet_rlm.app import create_app
from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.interpreter import sync_sandbox
from fleet_rlm.json_types import JsonValue
from fleet_rlm.observability.tracing import _local_tracking_server_available
from fleet_rlm.paths import SESSION_WORKSPACE_MOUNT_PATH, volume_paths_from_settings
from fleet_rlm.rlm.events import ToolEventView
from fleet_rlm.rlm.result import truncate_public_text
from fleet_rlm.sessions.bindings import SandboxBinding
from fleet_rlm.skills.catalog import stable_skill_id
from fleet_rlm.workspace.storage import DaytonaSandboxVolumeFs
from tests.live._mvp_support import (
    _SECRET_NAMES,
    _assert_secret_free,
    _assert_sse_stop,
    _call_shapes,
    _live_settings,
    _sandbox_environment_names,
    _semantic_tool_diagnostic,
    _sse_chunks,
    _sse_finish_diagnostic,
    _strict_cleanup,
    live_runtime,
)

# The live marker rides the live cases individually so the deterministic
# failure-receipt cases at the end of this module run in the non-live lane.

_CONTRACT_ID = "fleet.live-daytona-mvp"
_CAPABILITY_ID = "fleet.live-daytona-mvp"
_WORKSPACE_PATH = "notes/findings.md"
_RECEIPT_SCHEMA = "fleet.daytona-mvp-proof/v2"
_EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"

# Bounds for the failure-receipt ``diagnostic`` block: a receipt records triage
# facts, never a stream dump. Per-string, per-collection, per-depth, and a
# final encoded ceiling are all enforced before the payload is written. The
# depth budget reaches the semantic-tool bound shapes (section → shapes → call →
# argument), which is where a type-binding failure is diagnosed.
_DIAGNOSTIC_TEXT_CHARS = 200
_DIAGNOSTIC_MAX_ENTRIES = 12
_DIAGNOSTIC_MAX_DEPTH = 6
_DIAGNOSTIC_MAX_CHARS = 6_000


class LiveDaytonaMVPResult(dspy.Signature):
    """Return the bounded typed result for the live Daytona proof."""

    request: str = dspy.InputField()
    session_context: dict = dspy.InputField()
    skill_cards: list[dict] = dspy.InputField()
    attachments: list[dict] = dspy.InputField()
    answer: str = dspy.OutputField()
    findings: list[dict[str, str]] = dspy.OutputField()


class NativeSemanticProofResult(dspy.Signature):
    """Return a small typed result for the native semantic-call canary."""

    request: str = dspy.InputField()
    session_context: dict = dspy.InputField()
    skill_cards: list[dict] = dspy.InputField()
    attachments: list[dict] = dspy.InputField()
    answer: str = dspy.OutputField()
    evidence: str = dspy.OutputField()


class LargeContextProofResult(dspy.Signature):
    """Return the verified aggregate over the seeded synthetic corpus."""

    request: str = dspy.InputField()
    session_context: dict = dspy.InputField()
    skill_cards: list[dict] = dspy.InputField()
    attachments: list[dict] = dspy.InputField()
    answer: str = dspy.OutputField()
    evidence: str = dspy.OutputField()


@dataclass(slots=True)
class _LargeContextLedger:
    calls: int = 0
    expected: tuple[tuple[str, int], ...] = (
        ("ledger-01.txt", 14),
        ("ledger-02.txt", 27),
        ("ledger-03.txt", 9),
        ("ledger-04.txt", 31),
        ("ledger-05.txt", 19),
    )

    def verify_aggregate(self, entries: list[dict[str, object]], total: int) -> dict[str, object]:
        self.calls += 1
        normalized_entries: list[tuple[str, int]] = []
        for entry in entries:
            amount = entry.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool):
                raise ValueError("synthetic corpus amount must be an integer")
            normalized_entries.append((str(entry.get("reference", "")), amount))
        normalized = tuple(sorted(normalized_entries))
        expected = tuple(sorted(self.expected))
        if self.calls != 1 or normalized != expected or total != 100:
            raise ValueError("synthetic corpus facts or aggregate did not match the seeded files")
        return {"ok": True, "total": total, "source_count": len(normalized)}


@dataclass(slots=True)
class _ProofCapabilityPreparer:
    delegate: Any
    tools: tuple[dspy.Tool, ...]
    event_views: MappingProxyType[str, ToolEventView]
    signature: Any = LiveDaytonaMVPResult

    async def prepare(self, turn: Any, environment: Any, attachments: Any, *, deadline: float) -> Any:
        prepared = await self.delegate.prepare(turn, environment, attachments, deadline=deadline)
        # Signature instructions are recomposed from Fleet fragments and skill
        # bodies at worker start; live steering rides the Turn request text.
        prepared.spec = replace(
            prepared.spec,
            signature=self.signature,
            output_schema_id=_CONTRACT_ID,
            output_schema_version="1",
            tools=(*prepared.spec.tools, *self.tools),
            tool_event_views={**prepared.spec.tool_event_views, **self.event_views},
        )
        return prepared


@dataclass(slots=True)
class _ProofLedger:
    token: str = field(default_factory=lambda: f"iteration-{uuid4()}")
    token_calls: int = 0
    semantic_calls: list[dict[str, Any]] = field(default_factory=list)
    reload_calls: list[dict[str, Any]] = field(default_factory=list)
    workspace_checksum: str | None = None

    def issue_iteration_token(self) -> str:
        self.token_calls += 1
        if self.token_calls != 1:
            raise ValueError("iteration token may be issued only once")
        return self.token

    def verify_semantic_work(
        self,
        iteration_token: str,
        single_result: str,
        batch_results: list[str],
        accumulator: list[str],
    ) -> dict[str, object]:
        if iteration_token != self.token:
            raise ValueError("iteration token mismatch")
        if len(batch_results) != 3 or any(value.startswith("[ERROR]") for value in batch_results):
            raise ValueError("batched semantic work is incomplete")
        expected_tokens = ("ALPHA", "BETA", "GAMMA")
        if any(token not in value.upper() for token, value in zip(expected_tokens, batch_results, strict=True)):
            raise ValueError("batched semantic results are out of order")
        if "ROOT" not in single_result.upper():
            raise ValueError("single semantic result is invalid")
        expected_accumulator = [self.token, single_result, *batch_results]
        if accumulator != expected_accumulator:
            raise ValueError("interpreter accumulator did not persist")
        checksum = hashlib.sha256(
            json.dumps(expected_accumulator, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        self.semantic_calls.append(
            {
                "single_result": single_result,
                "batch_results": list(batch_results),
                "accumulator": list(accumulator),
                "checksum": checksum,
            }
        )
        return {"ok": True, "batch_count": len(batch_results), "checksum": checksum}

    def verify_workspace_reload(self, workspace_content: str, accumulator_present: bool) -> dict[str, object]:
        checksum = hashlib.sha256(workspace_content.encode()).hexdigest()
        if accumulator_present:
            raise ValueError("interpreter state survived across Runs")
        if self.workspace_checksum is None or checksum != self.workspace_checksum:
            raise ValueError("workspace content changed across Sandbox replacement")
        self.reload_calls.append(
            {
                "accumulator_present": accumulator_present,
                "workspace_checksum": checksum,
            }
        )
        return {"ok": True, "checksum": checksum}


@dataclass(slots=True)
class _FirstStreamDeltaProbe:
    first_delta_at: float | None = None

    def observe_body(self, body: bytes) -> None:
        for line in body.splitlines():
            if not line.startswith(b"data: "):
                continue
            try:
                chunk = json.loads(line[6:])
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if (
                isinstance(chunk, dict)
                and chunk.get("type") in {"reasoning-delta", "data-rlm-code"}
                and self.first_delta_at is None
            ):
                self.first_delta_at = time.perf_counter()


class _FirstStreamDeltaMiddleware:
    def __init__(self, app: Any, *, probe: _FirstStreamDeltaProbe) -> None:
        self.app = app
        self.probe = probe

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        pending = bytearray()

        async def recording_send(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.body":
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    pending.extend(body)
                    lines = bytes(pending).splitlines(keepends=True)
                    pending.clear()
                    for line in lines:
                        if line.endswith((b"\n", b"\r")):
                            self.probe.observe_body(line)
                        else:
                            pending.extend(line)
            await send(message)

        await self.app(scope, receive, recording_send)


def _assert_skill_lifecycle(chunks: list[dict[str, Any]], *, skill_id: UUID, version: str) -> None:
    events = [
        chunk["data"]
        for chunk in chunks
        if chunk.get("type") == "data-skill"
        and isinstance(chunk.get("data"), dict)
        and chunk["data"].get("skill_id") == str(skill_id)
    ]
    assert len(events) == 2
    assert events[0]["version"] == version
    assert events[0]["trust"] == "system"
    assert events[1] == {
        "skill_id": str(skill_id),
        "name": "long-context",
        "version": version,
        "phase": "loaded",
    }


def _streaming_evidence(chunks: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """Return only bounded evidence that native deltas reached the SSE stream."""
    fields: set[str] = set()
    delta_count = 0
    for chunk in chunks:
        if chunk.get("type") == "reasoning-delta":
            fields.add("reasoning")
            delta_count += 1
        elif chunk.get("type") == "data-rlm-code":
            data = chunk.get("data")
            if isinstance(data, dict) and data.get("is_delta") is True:
                fields.add("code")
                delta_count += 1
    return delta_count, sorted(fields)


def _assistant_messages(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in page["items"] if item["role"] == "assistant"]


def _structured_part(message: dict[str, Any]) -> dict[str, Any]:
    return next(part for part in message["parts"] if part["type"] == "data-structured-result")


def _assert_bytes_secret_free(secret_values: tuple[str, ...], values: list[bytes]) -> None:
    encoded_secrets = tuple(secret.encode() for secret in (*_SECRET_NAMES, *secret_values) if secret)
    if any(secret in value for value in values for secret in encoded_secrets):
        pytest.fail("provider credential leaked into Workspace Volume Scope")


def _git_value(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _candidate_metadata(settings: Settings) -> dict[str, object]:
    return {
        "sha": _git_value("rev-parse", "HEAD"),
        "branch": _git_value("branch", "--show-current"),
        "tracked_tree_clean": not bool(_git_value("status", "--porcelain", "--untracked-files=no")),
        "versions": {
            "python": sys.version.split()[0],
            "dspy": importlib.metadata.version("dspy"),
            "daytona": importlib.metadata.version("daytona"),
        },
        "lockfile_sha256": hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest(),
        "models": {
            "root": settings.root_model,
            "sub": settings.sub_model,
        },
    }


def _atomic_write_receipt(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_receipt_if_requested(payload: dict[str, object]) -> None:
    raw_path = os.environ.get(_EVIDENCE_ENV)
    if raw_path:
        _atomic_write_receipt(Path(raw_path).expanduser().resolve(), payload)


def _bound_diagnostic(value: Any, *, depth: int = 0) -> Any:
    """Recursively cap a diagnostic value so a failure receipt cannot balloon."""
    if isinstance(value, str):
        return truncate_public_text(value, max_len=_DIAGNOSTIC_TEXT_CHARS)
    if depth >= _DIAGNOSTIC_MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, dict):
        entries = list(value.items())[:_DIAGNOSTIC_MAX_ENTRIES]
        return {str(key): _bound_diagnostic(item, depth=depth + 1) for key, item in entries}
    if isinstance(value, (list, tuple)):
        return [_bound_diagnostic(item, depth=depth + 1) for item in list(value)[:_DIAGNOSTIC_MAX_ENTRIES]]
    if isinstance(value, (set, frozenset)):
        items = sorted(value, key=repr)[:_DIAGNOSTIC_MAX_ENTRIES]
        return [_bound_diagnostic(item, depth=depth + 1) for item in items]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return truncate_public_text(str(value), max_len=_DIAGNOSTIC_TEXT_CHARS)


def _stream_diagnostic(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Bounded, metadata-only stream summary mirroring ``_sse_finish_diagnostic``.

    Those facts currently reach only a pytest assertion message, so a failed live
    run leaves nothing durable behind. The same evidence keeps the failure
    receipt triageable: chunk histogram, finish reasons, error texts, tool
    errors, the semantic-tool classification, and SUBMIT call shapes.
    """
    if not chunks:
        return {}
    chunk_types: dict[str, int] = {}
    tool_names_by_call: dict[str, str] = {}
    finish_reasons: list[str] = []
    error_texts: list[str] = []
    tool_errors: list[dict[str, str]] = []
    for chunk in chunks:
        kind = str(chunk.get("type", "?"))
        chunk_types[kind] = chunk_types.get(kind, 0) + 1
        if kind in {"tool-input-available", "tool_call"}:
            call_id = str(chunk.get("toolCallId", ""))
            name = str(chunk.get("toolName", ""))
            if call_id and name:
                tool_names_by_call[call_id] = name
        elif kind in {"finish", "turn_finish"}:
            finish_reasons.append(str(chunk.get("finishReason", "")))
        elif kind in {"error", "turn_error"}:
            error_texts.append(str(chunk.get("errorText", "")))
        elif kind == "tool-output-error" or (kind == "tool_result" and chunk.get("error")):
            tool_errors.append(
                {
                    "toolName": tool_names_by_call.get(str(chunk.get("toolCallId", "")), "unknown"),
                    "errorText": str(chunk.get("errorText", chunk.get("error", ""))),
                }
            )
    bounded: dict[str, Any] = _bound_diagnostic(
        {
            "chunk_types": dict(sorted(chunk_types.items())),
            "finish_reasons": finish_reasons,
            "error_texts": error_texts,
            "tool_errors": tool_errors,
            "semantic_tool": _semantic_tool_diagnostic(chunks),
            "submit_call_shapes": _call_shapes(chunks, "SUBMIT"),
        }
    )
    encoded = json.dumps(bounded, ensure_ascii=False, default=str, sort_keys=True)
    if len(encoded) <= _DIAGNOSTIC_MAX_CHARS:
        return bounded
    return {
        "truncated": True,
        "encoded_chars": len(encoded),
        "sections": sorted(bounded),
        # Sorted keys lead with the histogram and the short lists, so the head
        # keeps the highest-value triage evidence when the budget is exceeded.
        "head": truncate_public_text(encoded, max_len=_DIAGNOSTIC_MAX_CHARS),
    }


def _semantic_input_shape(values: Mapping[str, Any]) -> JsonValue:
    """Project only the argument shapes of a verify_semantic_work call."""
    return {
        "iteration_token_type": type(values.get("iteration_token")).__name__,
        "single_result_type": type(values.get("single_result")).__name__,
        "batch_results_type": type(values.get("batch_results")).__name__,
        "batch_result_item_types": tuple(sorted({type(value).__name__ for value in values.get("batch_results", ())}))
        if isinstance(values.get("batch_results"), (list, tuple))
        else (),
        "batch_count": len(values["batch_results"]) if isinstance(values.get("batch_results"), (list, tuple)) else 0,
        "accumulator_type": type(values.get("accumulator")).__name__,
        "accumulator_item_types": tuple(sorted({type(value).__name__ for value in values.get("accumulator", ())}))
        if isinstance(values.get("accumulator"), (list, tuple))
        else (),
        "accumulator_count": len(values["accumulator"]) if isinstance(values.get("accumulator"), (list, tuple)) else 0,
    }


def _failure_diagnostic(streams: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Per-stream diagnostics keyed by phase label; streams without chunks are omitted."""
    return {label: _stream_diagnostic(chunks) for label, chunks in streams.items() if chunks}


def _failure_receipt(
    *,
    candidate: dict[str, object],
    started_at: str,
    category: str,
    phase: str,
    diagnostic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": _RECEIPT_SCHEMA,
        "candidate": {key: candidate[key] for key in ("sha", "branch", "tracked_tree_clean")},
        "timing": {
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
        },
        "failure": {"category": category, "phase": phase},
        "diagnostic": diagnostic or {},
        "passed": False,
    }


def _assert_tracking_server_reachable(tracking_uri: str) -> None:
    """Fail closed when a lane's span-evidence precondition is unmet.

    ``configure_tracing`` fails soft, so an unreachable tracking server would
    otherwise surface only as a confusing ``len(trace_ids) == 1`` assertion — or
    as a lane that appears to certify spans while producing none. The production
    probe is reused so the precondition matches the real failure mode. Non-HTTP
    URIs (SQLite, Databricks) are not socket-probed, exactly as in production.
    """
    if _local_tracking_server_available(tracking_uri):
        return
    pytest.fail(
        f"native semantic lane requires a reachable MLflow tracking server at {tracking_uri!r} "
        "(settings.mlflow_tracking_uri); start it or point the policy at a reachable URI, because "
        "configure_tracing fails soft and this lane certifies one RLM.execute span"
    )


def _session_volume_files(sandbox: Any, session_dir: str) -> list[bytes]:
    files: list[bytes] = []
    for info in sandbox.fs.list_files(session_dir, depth=8):
        if info.is_dir or not info.path:
            continue
        files.append(sandbox.fs.download_file(info.path))
    return files


async def _replace_binding(resources: Any, binding: SandboxBinding) -> SandboxBinding:
    return await resources.replace(
        binding,
        workspace_id=binding.workspace_id,
        user_id=LocalScope().user_id,
    )


def _run_id_from_sse(chunks: list[dict[str, Any]], *, label: str, resources: Any) -> UUID:
    starts = [chunk for chunk in chunks if chunk.get("type") in {"start", "turn_start"}]
    if len(starts) != 1:
        runtime = getattr(resources, "runtime", resources)
        pending_ownership = bool(getattr(runtime, "has_pending_ownership", False))
        runtime_roots = len(getattr(runtime, "roots", ())) if runtime is not None else 0
        tracked_sandboxes = len(getattr(runtime, "_tracked_sandbox_ids", ())) if runtime is not None else 0
        pytest.fail(
            f"{label}: expected exactly one start event, got {len(starts)}; "
            f"{_sse_finish_diagnostic(chunks)} "
            f"cleanup_state={{pending_ownership:{pending_ownership}, runtime_roots:{runtime_roots}, "
            f"tracked_sandboxes:{tracked_sandboxes}}}"
        )
    return UUID(str(starts[0].get("messageId", starts[0].get("runId"))))


@pytest.mark.live_daytona
@pytest.mark.timeout(900)
def test_direct_pi_digit_uses_deterministic_repl_without_optional_capabilities(tmp_path: Path) -> None:
    settings = _live_settings(tmp_path).model_copy(
        update={
            "rlm_max_iters": 3,
            "turn_timeout_seconds": 840,
        }
    )
    app = create_app(settings=settings)
    sandbox_ids: set[str] = set()
    cleanup_failures: tuple[str, ...] = ()

    with TestClient(app) as client:
        resources, _ = live_runtime(app)
        portal = client.portal
        assert portal is not None
        try:
            created = client.post("/api/sessions", json={"title": "Direct Pi digit proof"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])

            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Tell me the 14952th digit after the decimal point of Pi"},
                headers={"Idempotency-Key": f"live-direct-pi-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            _assert_sse_stop(chunks, label="direct_pi_digit")

            code_chunks = [chunk for chunk in chunks if chunk.get("type") in {"data-rlm-code", "code"}]
            output_chunks = [chunk for chunk in chunks if chunk.get("type") in {"data-rlm-output", "output"}]
            usage_chunks = [chunk for chunk in chunks if chunk.get("type") in {"data-usage", "usage"}]
            structured = [
                chunk for chunk in chunks if chunk.get("type") in {"data-structured-result", "structured_result"}
            ]
            assert len(usage_chunks) == 1
            # The default one-output Signature is projected as text; multi-output
            # Signatures use structured-result events.
            assert structured == []
            text = "".join(
                str(chunk.get("delta", "")) for chunk in chunks if chunk.get("type") in {"text-delta", "text"}
            )
            assert text == "1"

            usage_chunk = usage_chunks[0]
            usage = (
                usage_chunk["data"].get("usage", usage_chunk["data"])
                if usage_chunk.get("type") == "data-usage"
                else usage_chunk.get("usage", {})
            )
            assert 2 <= int(usage["iterations"]) <= 3
            # Raw SSE code-chunk count is not the iteration bound: an iteration that yields no
            # live execution (malformed model code) shifts live/trajectory step alignment, and
            # trajectory reconciliation legitimately re-emits the corrected step plus the
            # canonical backfill under the same stable step IDs (TUI cards upsert). The stable
            # contract is distinct code steps, bounded by max_iters.
            code_steps = {
                chunk.get("data", {}).get("step") if chunk.get("type") == "data-rlm-code" else chunk.get("step")
                for chunk in code_chunks
            }
            code_steps.discard(None)
            assert 2 <= len(code_steps) <= 3

            tool_names = [
                str(chunk.get("toolName", ""))
                for chunk in chunks
                if chunk.get("type") in {"tool-input-available", "tool_call"}
            ]
            assert "llm_query" not in tool_names
            assert "llm_query_batched" not in tool_names
            forbidden_capabilities = {
                "load_skill",
                "read_skill_resource",
                "read_session_history",
                "read_attachment",
                "list_workspace_files",
                "stat_workspace_file",
                "read_workspace_text",
                "write_workspace_text",
                "append_workspace_text",
                "create_artifact",
                "publish_workspace_artifact",
            }
            assert forbidden_capabilities.isdisjoint(tool_names)

            outputs = [
                str(
                    chunk.get("data", {}).get("output", "")
                    if chunk.get("type") == "data-rlm-output"
                    else chunk.get("output", "")
                )
                for chunk in output_chunks
            ]
            assert not any(
                output.lstrip().startswith(("[Error]", "Execution error", "Execution failed")) for output in outputs
            )
            submit_shapes = _call_shapes(chunks, "SUBMIT")
            assert len(submit_shapes) == 1
            assert submit_shapes[0]["positional_count"] == 0
            assert submit_shapes[0]["keyword_names"] == ["answer"]

            page = client.get(f"/api/sessions/{session_id}/turns")
            assert page.status_code == 200
            assistant = _assistant_messages(page.json())
            assert len(assistant) == 1
            text_parts = [part for part in assistant[0]["parts"] if part["type"] == "text"]
            assert len(text_parts) == 1
            assert text_parts[0]["text"] == "1"

            binding = portal.call(resources._bindings.get, session_id)
            assert binding is not None
            assert binding.sandbox_id is not None
            sandbox_ids.add(binding.sandbox_id)
            assert portal.call(resources._platform.get, binding.sandbox_id) is not None
        finally:
            cleanup_failures = portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()


@pytest.mark.live_daytona
@pytest.mark.timeout(900)
def test_complete_daytona_mvp_through_fastapi(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _live_settings(tmp_path).model_copy(
        update={
            # Keep wrap-up from rewriting the required third cell (append + publish
            # + SUBMIT) into SUBMIT-only after long Sub-LM work. Default policy
            # reserves 300s of the 840s proof Turn.
            "rlm_wrap_up_seconds": 60,
        }
    )
    caplog.set_level(logging.DEBUG)
    started_at = datetime.now(UTC)
    started_at_text = started_at.isoformat()
    candidate = _candidate_metadata(settings)
    models = candidate.pop("models")
    ledger = _ProofLedger()
    app = create_app(settings=settings)
    sandbox_ids: set[str] = set()
    resources: Any | None = None
    phase = "composition"
    turn_chunks: dict[str, list[dict[str, Any]]] = {}
    receipt_written = False
    scenario_passed = False
    cleanup_failures: tuple[str, ...] = ()
    success_receipt: dict[str, object] | None = None
    first_delta_probe = _FirstStreamDeltaProbe()

    token_tool = dspy.Tool(
        ledger.issue_iteration_token,
        name="issue_iteration_token",
        desc="Issue the opaque token used to prove state across RLM iterations.",
    )
    semantic_tool = dspy.Tool(
        ledger.verify_semantic_work,
        name="verify_semantic_work",
        desc=(
            "One-shot assertion for ordered recursive semantic work and the persisted Python accumulator. "
            "Call exactly once after the single and batch queries succeed; never retry or repeat it."
        ),
        arg_desc={
            "iteration_token": "Opaque string returned by issue_iteration_token; pass it unchanged.",
            "single_result": "String returned by the single llm_query call.",
            "batch_results": (
                "Ordered Python list of exactly three strings returned by llm_query_batched for ALPHA, BETA, GAMMA."
            ),
            "accumulator": (
                "Existing persisted Python list containing iteration_token, then single_result, then batch_results; "
                "do not recreate or convert it."
            ),
        },
    )
    reload_tool = dspy.Tool(
        ledger.verify_workspace_reload,
        name="verify_workspace_reload",
        desc="Verify a fresh interpreter read the durable Session Workspace content.",
    )
    skill = app.state.skill_catalog.require(stable_skill_id("long-context"))
    proof_tools = (token_tool, semantic_tool, reload_tool)
    proof_views = MappingProxyType(
        {
            "issue_iteration_token": ToolEventView(
                output_projection=lambda _result: {"issued": True},
            ),
            "verify_semantic_work": ToolEventView(
                input_projection=_semantic_input_shape,
                output_projection=lambda result: {
                    "ok": bool(result.get("ok")),
                    "batch_count": int(result.get("batch_count", 0)),
                    "checksum": str(result.get("checksum", "")),
                },
            ),
            "verify_workspace_reload": ToolEventView(
                input_projection=lambda values: {
                    "content_chars": len(str(values.get("workspace_content", ""))),
                    "accumulator_present": bool(values.get("accumulator_present")),
                },
                output_projection=lambda result: {
                    "ok": bool(result.get("ok")),
                    "checksum": str(result.get("checksum", "")),
                },
            ),
        }
    )

    secret_values = tuple(
        value
        for secret in (settings.daytona_api_key, settings.llm_api_key)
        if secret is not None
        for value in (secret.get_secret_value(),)
        if value
    )

    try:
        app.add_middleware(_FirstStreamDeltaMiddleware, probe=first_delta_probe)
        with TestClient(app) as client:
            resources, preparation = live_runtime(app)
            object.__setattr__(
                preparation,
                "capabilities",
                _ProofCapabilityPreparer(preparation.capabilities, proof_tools, proof_views),
            )
            portal = client.portal
            assert portal is not None
            portal_loop = portal.call(lambda: asyncio.get_running_loop())
            try:
                phase = "first_turn"
                created = client.post("/api/sessions", json={"title": "Live Daytona MVP proof"})
                assert created.status_code == 201
                session_id = UUID(created.json()["id"])

                first_started = time.perf_counter()
                first = client.post(
                    f"/api/sessions/{session_id}/turns",
                    json={
                        "text": (
                            "FIRST: execute the complete recursive Daytona MVP proof."
                            " Use exactly 3 iterations; do not improvise, explore, print for inspection,"
                            " or retry. Ignore generic explore-first habits: this request is fully specified."
                            " 1) The FIRST code cell must immediately call, once:"
                            " iteration_token = issue_iteration_token();"
                            ' accumulator = [iteration_token]; print("FIRST_ITERATION_READY").'
                            " 2) The SECOND code cell must contain only these statements in this order"
                            " (no parsing request, no regex, no extra logic):"
                            ' single_result = llm_query("Return exactly ROOT");'
                            ' batch_results = llm_query_batched(["Return exactly ALPHA", "Return exactly BETA",'
                            ' "Return exactly GAMMA"]);'
                            " accumulator.extend([single_result, *batch_results]);"
                            " verification = verify_semantic_work(iteration_token=iteration_token,"
                            " single_result=single_result, batch_results=batch_results, accumulator=accumulator);"
                            ' checksum = verification["checksum"];'
                            ' content = f"single={single_result} batch={batch_results} checksum={checksum}";'
                            ' workspace_result = append_workspace_text(path="notes/findings.md", content=content);'
                            ' artifact_result = publish_workspace_artifact(path="notes/findings.md",'
                            ' kind="markdown", title="Findings"); print("SECOND_ITERATION_READY").'
                            " 3) Set non-empty string-only summary/findings; call exactly"
                            " SUBMIT(answer=summary, findings=findings) with keywords. No fallback."
                        ),
                        "skill_selections": [{"id": str(skill.card.id), "expected_version": skill.card.version}],
                    },
                    headers={"Idempotency-Key": f"live-mvp-first-{uuid4()}"},
                )
                assert first.status_code == 200
                first_chunks, first_done = _sse_chunks(first)
                turn_chunks["first_turn"] = first_chunks
                first_run_id = _run_id_from_sse(first_chunks, label="first_turn", resources=resources)
                assert first_delta_probe.first_delta_at is not None, _sse_finish_diagnostic(first_chunks)
                first_delta_ms = int((first_delta_probe.first_delta_at - first_started) * 1000)
                _assert_skill_lifecycle(first_chunks, skill_id=skill.card.id, version=skill.card.version)
                assert first_done == 1
                assert sum(chunk["type"] == "start" for chunk in first_chunks) == 1
                assert sum(chunk["type"] == "finish" for chunk in first_chunks) == 1
                _assert_sse_stop(first_chunks, label="first_turn")
                code_chunks = [chunk for chunk in first_chunks if chunk["type"] == "data-rlm-code"]
                assert len(code_chunks) >= 3
                generated_code = [str(chunk["data"]["code"]) for chunk in code_chunks]
                assert "issue_iteration_token" in generated_code[0]
                assert re.search(r"\baccumulator\s*=", generated_code[0])
                semantic_steps = [code for code in generated_code[1:] if "llm_query_batched" in code]
                if not semantic_steps:
                    pytest.fail(f"first_turn: no semantic batch step; {_sse_finish_diagnostic(first_chunks)}")
                semantic_step = semantic_steps[0]
                assert re.search(r"\bllm_query\s*\(", semantic_step)
                assert "verify_semantic_work" in semantic_step
                semantic_tree = ast.parse(semantic_step)
                assert not any(
                    isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
                    and (
                        any(isinstance(target, ast.Name) and target.id == "accumulator" for target in node.targets)
                        if isinstance(node, ast.Assign)
                        else isinstance(getattr(node, "target", None), ast.Name)
                        and getattr(node.target, "id", None) == "accumulator"
                    )
                    for node in ast.walk(semantic_tree)
                )
                assert re.search(r"\bSUBMIT\s*\(", generated_code[-1])
                tool_names = [chunk["toolName"] for chunk in first_chunks if chunk["type"] == "tool-input-available"]
                assert tool_names.count("issue_iteration_token") == 1
                assert tool_names.count("verify_semantic_work") == 1
                assert "append_workspace_text" in tool_names
                assert "publish_workspace_artifact" in tool_names
                assert ledger.token_calls == 1
                assert len(ledger.semantic_calls) == 1
                structured_chunks = [chunk for chunk in first_chunks if chunk["type"] == "data-structured-result"]
                assert len(structured_chunks) == 1
                assert structured_chunks[0]["data"]["schema_id"] == _CONTRACT_ID

                first_page = client.get(f"/api/sessions/{session_id}/turns")
                assert first_page.status_code == 200
                first_assistant = _assistant_messages(first_page.json())[-1]
                first_structured = _structured_part(first_assistant)
                assert first_structured["data"]["schema_id"] == _CONTRACT_ID
                assert first_structured["data"]["value"] == structured_chunks[0]["data"]["value"]

                phase = "first_durability"
                binding = portal.call(resources._bindings.get, session_id)
                assert binding is not None
                assert binding.sandbox_id is not None
                assert binding.volume_id is not None
                sandbox_ids.add(binding.sandbox_id)
                first_sandbox = sync_sandbox(portal.call(resources._platform.get, binding.sandbox_id), portal_loop)
                assert first_sandbox is not None
                first_fs = DaytonaSandboxVolumeFs(first_sandbox)
                paths = volume_paths_from_settings(settings)
                snapshot_bytes = first_fs.read_bytes(str(paths.run_result_path(session_id, first_run_id)))
                snapshot = json.loads(snapshot_bytes)
                assert snapshot["contract_id"] == _CONTRACT_ID
                assert snapshot["outputs"] == first_structured["data"]["value"]
                snapshot_checksum = hashlib.sha256(snapshot_bytes).hexdigest()
                assert len(snapshot_checksum) == 64
                workspace_content = first_fs.read_bytes(str(paths.session_workspace_dir(session_id) / _WORKSPACE_PATH))
                ledger.workspace_checksum = hashlib.sha256(workspace_content).hexdigest()

                env_names = _sandbox_environment_names(first_sandbox)
                assert not set(_SECRET_NAMES) & env_names
                _assert_secret_free(
                    secret_values,
                    first_chunks,
                    first_page.json(),
                    snapshot,
                    workspace_content.decode(),
                    sorted(env_names),
                )

                phase = "sandbox_replacement"
                replacement = portal.call(
                    _replace_binding,
                    resources,
                    SandboxBinding(
                        session_id=session_id,
                        sandbox_id=binding.sandbox_id,
                        workspace_id=binding.workspace_id,
                        volume_id=binding.volume_id,
                        volume_subpath=binding.volume_subpath,
                        mount_path=binding.mount_path,
                        provider_state="unrecoverable",
                    ),
                )
                assert replacement.sandbox_id is not None
                sandbox_ids.add(replacement.sandbox_id)
                assert replacement.sandbox_id != binding.sandbox_id
                assert replacement.volume_id == binding.volume_id
                assert replacement.mount_path == binding.mount_path
                assert replacement.volume_subpath == binding.volume_subpath
                resources.track_sandbox(replacement.sandbox_id)

                phase = "second_turn"
                second = client.post(
                    f"/api/sessions/{session_id}/turns",
                    json={
                        "text": (
                            "SECOND: verify fresh interpreter state and durable workspace reload."
                            " Exactly two RLM iterations; do not create accumulator, explore, parse, or retry."
                            ' 1) First evaluate the boolean expression "accumulator" in globals() and store'
                            " the result as accumulator_present. It MUST be that boolean outcome — never the"
                            ' literal string "accumulator", never a hardcoded True or False. Then call'
                            ' workspace_result = read_workspace_text(path="notes/findings.md", max_chars=10000).'
                            ' Require dictionary key workspace_result["ok"]; call exactly once'
                            " reload_verification = verify_workspace_reload("
                            ' workspace_content=workspace_result["content"],'
                            " accumulator_present=accumulator_present);"
                            ' require reload_verification["ok"], set workspace_checksum, and'
                            ' print("RELOAD_ITERATION_READY").'
                            " 2) Set non-empty string-only summary and findings, then call exactly"
                            " SUBMIT(answer=summary, findings=findings) with keywords."
                        ),
                        "skill_selections": [{"id": str(skill.card.id), "expected_version": skill.card.version}],
                    },
                    headers={"Idempotency-Key": f"live-mvp-second-{uuid4()}"},
                )
                assert second.status_code == 200
                second_chunks, second_done = _sse_chunks(second)
                turn_chunks["second_turn"] = second_chunks
                second_run_id = _run_id_from_sse(second_chunks, label="second_turn", resources=resources)
                first_stream_count, first_stream_fields = _streaming_evidence(first_chunks)
                second_stream_count, second_stream_fields = _streaming_evidence(second_chunks)
                _assert_skill_lifecycle(second_chunks, skill_id=skill.card.id, version=skill.card.version)
                assert second_done == 1
                assert sum(chunk["type"] == "start" for chunk in second_chunks) == 1
                assert sum(chunk["type"] == "finish" for chunk in second_chunks) == 1
                _assert_sse_stop(second_chunks, label="second_turn")
                second_code = [chunk for chunk in second_chunks if chunk["type"] == "data-rlm-code"]
                assert len(second_code) >= 2
                second_generated_code = [str(chunk["data"]["code"]) for chunk in second_code]
                assert "globals()" in second_generated_code[0]
                assert "read_workspace_text" in second_generated_code[0]
                assert "verify_workspace_reload" in second_generated_code[0]
                assert re.search(r"\bSUBMIT\s*\(", second_generated_code[-1])
                second_tool_names = [
                    chunk["toolName"] for chunk in second_chunks if chunk["type"] == "tool-input-available"
                ]
                assert "read_workspace_text" in second_tool_names
                assert second_tool_names.count("verify_workspace_reload") == 1
                assert ledger.reload_calls == [
                    {
                        "accumulator_present": False,
                        "workspace_checksum": ledger.workspace_checksum,
                    }
                ]

                phase = "reload_and_secret_audit"
                reloaded_page = client.get(f"/api/sessions/{session_id}/turns")
                assert reloaded_page.status_code == 200
                assistants = _assistant_messages(reloaded_page.json())
                assert len(assistants) == 2
                assert assistants[0] == first_assistant
                assert _structured_part(assistants[0]) == first_structured
                replacement_sandbox = sync_sandbox(
                    portal.call(resources._platform.get, replacement.sandbox_id), portal_loop
                )
                assert replacement_sandbox is not None
                replacement_env_names = _sandbox_environment_names(replacement_sandbox)
                assert not set(_SECRET_NAMES) & replacement_env_names
                scoped_files = _session_volume_files(replacement_sandbox, str(paths.session_dir(session_id)))
                _assert_bytes_secret_free(secret_values, scoped_files)
                application_logs = [record.getMessage() for record in caplog.records]
                _assert_secret_free(
                    (*_SECRET_NAMES, *secret_values),
                    first_chunks,
                    second_chunks,
                    reloaded_page.json(),
                    sorted(replacement_env_names),
                    application_logs,
                )

                typed_result_checksum = hashlib.sha256(
                    json.dumps(
                        first_structured["data"]["value"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
                finished_at = datetime.now(UTC)
                success_receipt = {
                    "schema": _RECEIPT_SCHEMA,
                    "candidate": candidate,
                    "timing": {
                        "started_at": started_at_text,
                        "finished_at": finished_at.isoformat(),
                        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
                    },
                    "models": models,
                    "resources": {
                        "session_id": str(session_id),
                        "run_ids": [str(first_run_id), str(second_run_id)],
                        "sandbox_ids": [binding.sandbox_id, replacement.sandbox_id],
                        "volume_id": binding.volume_id,
                    },
                    "counts": {
                        "iterations": len(code_chunks) + len(second_code),
                        "single_lm_calls": 1,
                        "batched_lm_calls": 3,
                        "host_tool_calls": len(tool_names) + len(second_tool_names),
                        "sse_start": 2,
                        "sse_finish": 2,
                        "sse_done": first_done + second_done,
                    },
                    "streaming": {
                        "first_delta_ms": first_delta_ms,
                        "delta_count": first_stream_count + second_stream_count,
                        "fields": sorted(set(first_stream_fields) | set(second_stream_fields)),
                    },
                    "checksums": {
                        "snapshot_sha256": snapshot_checksum,
                        "workspace_sha256": ledger.workspace_checksum,
                        "typed_result_sha256": typed_result_checksum,
                    },
                    "assertions": {
                        "typed_submit": True,
                        "stateful_iterations": True,
                        "fresh_replacement_context": True,
                        "workspace_survived_replacement": True,
                        "history_reload_identical": True,
                        "secret_audit_passed": True,
                        "cleanup_passed": False,
                    },
                    "failure": None,
                    "passed": False,
                }
                _assert_secret_free((*_SECRET_NAMES, *secret_values), success_receipt)
                scenario_passed = True
            finally:
                failed_phase = phase
                phase = "cleanup"
                cleanup_failures = portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
                if scenario_passed and not cleanup_failures:
                    assert success_receipt is not None
                    assertions = success_receipt["assertions"]
                    assert isinstance(assertions, dict)
                    assertions["cleanup_passed"] = True
                    success_receipt["passed"] = True
                    _write_receipt_if_requested(success_receipt)
                    receipt_written = True
                else:
                    category = "cleanup_failed" if cleanup_failures else "proof_failed"
                    failure_phase = "cleanup" if cleanup_failures else failed_phase
                    _write_receipt_if_requested(
                        _failure_receipt(
                            candidate=candidate,
                            started_at=started_at_text,
                            category=category,
                            phase=failure_phase,
                            diagnostic=_failure_diagnostic(turn_chunks),
                        )
                    )
                    receipt_written = True
                if cleanup_failures:
                    raise AssertionError("live Daytona cleanup failed: " + ", ".join(cleanup_failures))
    except BaseException:
        if not receipt_written:
            _write_receipt_if_requested(
                _failure_receipt(
                    candidate=candidate,
                    started_at=started_at_text,
                    category="proof_failed",
                    phase=phase,
                    diagnostic=_failure_diagnostic(turn_chunks),
                )
            )
        raise


@pytest.mark.live_daytona
@pytest.mark.timeout(900)
def test_native_semantic_calls_through_fastapi(tmp_path: Path) -> None:
    """Verify single and ordered batch semantic calls through the live Daytona broker."""
    settings = _live_settings(tmp_path).model_copy(
        update={
            "rlm_recursion_enabled": False,
            "rlm_max_iters": 6,
            "rlm_max_llm_calls": 12,
            "turn_timeout_seconds": 840,
            "rlm_wrap_up_seconds": 60,
            "mlflow_tracing_enabled": True,
        }
    )
    if settings.rlm_recursion_enabled:
        pytest.fail("Native-only semantic verification requires rlm.recursion_enabled=false in config/fleet.toml")
    started_at = datetime.now(UTC)
    started_at_text = started_at.isoformat()
    candidate = _candidate_metadata(settings)
    models = candidate.pop("models")
    ledger = _ProofLedger()
    app = create_app(settings=settings)
    sandbox_ids: set[str] = set()
    resources: Any | None = None
    turn_chunks: dict[str, list[dict[str, Any]]] = {}
    receipt_written = False
    scenario_passed = False
    cleanup_failures: tuple[str, ...] = ()
    success_receipt: dict[str, object] | None = None

    token_tool = dspy.Tool(
        ledger.issue_iteration_token,
        name="issue_iteration_token",
        desc="Issue the opaque token used to prove state across RLM iterations.",
    )
    semantic_tool = dspy.Tool(
        ledger.verify_semantic_work,
        name="verify_semantic_work",
        desc="Verify one native semantic query, its ordered batch, and the persistent accumulator exactly once.",
        arg_desc={
            "iteration_token": "Opaque string returned by issue_iteration_token; pass it unchanged.",
            "single_result": "String returned by the single llm_query call.",
            "batch_results": "Ordered list returned by llm_query_batched for ALPHA, BETA, GAMMA.",
            "accumulator": "Existing accumulator with the token, single result, and all batch results.",
        },
    )
    proof_views = MappingProxyType(
        {
            "issue_iteration_token": ToolEventView(
                output_projection=lambda _result: {"issued": True},
            ),
            "verify_semantic_work": ToolEventView(
                input_projection=lambda values: {
                    "iteration_token_type": type(values.get("iteration_token")).__name__,
                    "single_result_type": type(values.get("single_result")).__name__,
                    "batch_count": len(values.get("batch_results", ()))
                    if isinstance(values.get("batch_results"), (tuple, list))
                    else 0,
                    "accumulator_count": len(values.get("accumulator", ()))
                    if isinstance(values.get("accumulator"), (tuple, list))
                    else 0,
                },
                output_projection=lambda result: {
                    "ok": bool(result.get("ok")),
                    "batch_count": int(result.get("batch_count", 0)),
                    "checksum": str(result.get("checksum", "")),
                },
            ),
        }
    )

    try:
        phase = "tracking_precondition"
        _assert_tracking_server_reachable(settings.mlflow_tracking_uri)
        # Past the precondition, failures before the Turn are composition failures.
        phase = "composition"
        with TestClient(app) as client:
            resources, preparation = live_runtime(app)
            object.__setattr__(
                preparation,
                "capabilities",
                _ProofCapabilityPreparer(
                    preparation.capabilities,
                    (token_tool, semantic_tool),
                    proof_views,
                    NativeSemanticProofResult,
                ),
            )
            portal = client.portal
            assert portal is not None
            try:
                created = client.post("/api/sessions", json={"title": "Native Daytona semantic proof"})
                assert created.status_code == 201
                session_id = UUID(created.json()["id"])

                phase = "native_semantic_calls"
                response = client.post(
                    f"/api/sessions/{session_id}/turns",
                    json={
                        "text": (
                            "Execute the native Daytona semantic-call proof in exactly three iterations. Do not"
                            " explore, improvise, retry, or write files. 1) The first code cell must call"
                            " iteration_token = issue_iteration_token(), set accumulator = [iteration_token], and"
                            " print FIRST_ITERATION_READY. 2) The second code cell must, in this order, call"
                            ' single_result = llm_query("Return exactly ROOT");'
                            ' batch_results = llm_query_batched(["Return exactly ALPHA", "Return exactly BETA",'
                            ' "Return exactly GAMMA"]); accumulator.extend([single_result, *batch_results]);'
                            " verification = verify_semantic_work(iteration_token=iteration_token,"
                            " single_result=single_result, batch_results=batch_results, accumulator=accumulator);"
                            " require verification['ok'] and print SEMANTIC_VERIFICATION_READY. Do not recreate"
                            " the accumulator. 3) Set a non-empty string summary and evidence that never include"
                            " the iteration token's value, then call exactly"
                            " SUBMIT(answer=summary, evidence=evidence) with keywords. Do not call"
                            " rlm_query or rlm_query_batched."
                        ),
                    },
                    headers={"Idempotency-Key": f"native-semantic-{uuid4()}"},
                )
                assert response.status_code == 200
                chunks, done = _sse_chunks(response)
                turn_chunks["native_semantic_calls"] = chunks
                assert done == 1
                _assert_sse_stop(chunks, label="native_semantic_calls")
                assert sum(chunk.get("type") in {"start", "turn_start"} for chunk in chunks) == 1
                assert sum(chunk.get("type") in {"finish", "turn_finish"} for chunk in chunks) == 1

                code_chunks = [chunk for chunk in chunks if chunk.get("type") in {"data-rlm-code", "code"}]
                generated_code = [
                    str(
                        chunk.get("data", {}).get("code", "")
                        if chunk.get("type") == "data-rlm-code"
                        else chunk.get("code", "")
                    )
                    for chunk in code_chunks
                ]
                assert any("issue_iteration_token" in code for code in generated_code)
                assert len(_call_shapes(chunks, "llm_query")) == 1
                assert len(_call_shapes(chunks, "llm_query_batched")) == 1

                tool_names = [
                    str(chunk.get("toolName", ""))
                    for chunk in chunks
                    if chunk.get("type") in {"tool-input-available", "tool_call"}
                ]
                assert tool_names.count("issue_iteration_token") == 1
                assert tool_names.count("verify_semantic_work") == 1
                assert "rlm_query" not in tool_names
                assert "rlm_query_batched" not in tool_names
                assert not any(
                    chunk.get("type") == "error"
                    or chunk.get("type") == "tool-output-error"
                    or (chunk.get("type") == "tool_result" and chunk.get("error"))
                    for chunk in chunks
                )
                assert ledger.token_calls == 1
                assert len(ledger.semantic_calls) == 1

                usage_chunks = [chunk for chunk in chunks if chunk.get("type") in {"data-usage", "usage"}]
                assert len(usage_chunks) == 1
                usage_chunk = usage_chunks[0]
                usage = (
                    usage_chunk["data"].get("usage", usage_chunk["data"])
                    if usage_chunk.get("type") == "data-usage"
                    else usage_chunk.get("usage", {})
                )
                assert int(usage["iterations"]) <= settings.rlm_max_iters
                assert int(usage["recursive_call_count"]) == 0
                metrics = usage["delegation_metrics"]
                assert int(metrics["sub_lm_calls_depth_0"]) == 4
                assert int(metrics["recursive_children_started"]) == 0
                assert int(metrics["recursive_batch_calls"]) == 0

                submit_shapes = _call_shapes(chunks, "SUBMIT")
                assert len(submit_shapes) == 1
                assert submit_shapes[0]["keyword_names"] == ["answer", "evidence"]
                structured = [
                    chunk for chunk in chunks if chunk.get("type") in {"data-structured-result", "structured_result"}
                ]
                assert len(structured) == 1
                assert (
                    structured[0].get("data", {}).get("schema_id")
                    if structured[0].get("type") == "data-structured-result"
                    else structured[0].get("schemaId")
                ) == _CONTRACT_ID

                trace_ids = {
                    trace_id
                    for chunk in chunks
                    for trace_id in (
                        chunk.get("traceId"),
                        *(
                            metadata.get("traceId")
                            for metadata in (chunk.get("messageMetadata"), chunk.get("metadata"))
                            if isinstance(metadata, dict)
                        ),
                    )
                    if isinstance(trace_id, str)
                }
                assert len(trace_ids) == 1
                trace_id = trace_ids.pop()
                from mlflow import MlflowClient

                trace = MlflowClient(tracking_uri=settings.mlflow_tracking_uri).get_trace(
                    trace_id, display=False, flush=True
                )
                execution_spans = [span for span in trace.data.spans if span.name == "RLM.execute"]
                assert len(execution_spans) == 1
                termination_mode = execution_spans[0].outputs["termination_mode"]
                assert termination_mode == "typed_submit"

                run_id = _run_id_from_sse(chunks, label="native_semantic_calls", resources=resources)
                binding = portal.call(resources._bindings.get, session_id)
                assert binding is not None and binding.sandbox_id is not None
                sandbox_ids.add(binding.sandbox_id)
                finished_at = datetime.now(UTC)
                success_receipt = {
                    "schema": _RECEIPT_SCHEMA,
                    "candidate": candidate,
                    "models": models,
                    "timing": {
                        "started_at": started_at_text,
                        "finished_at": finished_at.isoformat(),
                        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
                    },
                    "resources": {
                        "session_id": str(session_id),
                        "run_id": str(run_id),
                        "sandbox_ids": [binding.sandbox_id],
                    },
                    "counts": {
                        "iterations": int(usage["iterations"]),
                        "root_lm_calls_depth_0": int(metrics["root_lm_calls_depth_0"]),
                        "native_sub_lm_calls_depth_0": int(metrics["sub_lm_calls_depth_0"]),
                        "single_lm_calls": 1,
                        "batched_lm_prompts": 3,
                        "recursive_calls": int(usage["recursive_call_count"]),
                        "sse_done": done,
                    },
                    "token_usage_status": metrics["token_usage_status"],
                    "trace_id": trace_id,
                    "termination_mode": termination_mode,
                    "assertions": {
                        "single_semantic_call_succeeded": True,
                        "ordered_batch_results_verified": True,
                        "one_budgeted_sub_lm_call_per_prompt": metrics["sub_lm_calls_depth_0"] == 4,
                        "typed_submit": True,
                        "no_full_child_sandbox": usage["recursive_call_count"] == 0,
                        "cleanup_passed": False,
                    },
                    "failure": None,
                    "passed": False,
                }
                scenario_passed = True
            finally:
                cleanup_failures = portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
                if scenario_passed and not cleanup_failures:
                    assert success_receipt is not None
                    assertions = success_receipt["assertions"]
                    assert isinstance(assertions, dict)
                    assertions["cleanup_passed"] = True
                    success_receipt["passed"] = True
                    _write_receipt_if_requested(success_receipt)
                    receipt_written = True
                elif scenario_passed:
                    # Keep the successful execution evidence even when cleanup
                    # needs operator follow-up. In particular, retain the exact
                    # MLflow trace and provider resource identities so cleanup
                    # can be reconciled against provider state.
                    assert success_receipt is not None
                    assertions = success_receipt["assertions"]
                    assert isinstance(assertions, dict)
                    success_receipt["cleanup_failures"] = list(cleanup_failures)
                    success_receipt["failure"] = {"category": "cleanup_failed", "phase": "cleanup"}
                    success_receipt["passed"] = False
                    _write_receipt_if_requested(success_receipt)
                    receipt_written = True
                else:
                    _write_receipt_if_requested(
                        _failure_receipt(
                            candidate=candidate,
                            started_at=started_at_text,
                            category="cleanup_failed" if cleanup_failures else "proof_failed",
                            phase="cleanup" if cleanup_failures else "native_semantic_calls",
                            diagnostic=_failure_diagnostic(turn_chunks),
                        )
                    )
                    receipt_written = True
                if cleanup_failures:
                    raise AssertionError("live Daytona semantic cleanup did not settle")
    except BaseException:
        if not receipt_written:
            _write_receipt_if_requested(
                _failure_receipt(
                    candidate=candidate,
                    started_at=started_at_text,
                    category="proof_failed",
                    phase=phase,
                    diagnostic=_failure_diagnostic(turn_chunks),
                )
            )
        raise


@pytest.mark.live_daytona
@pytest.mark.timeout(900)
def test_large_context_aggregate_across_seeded_workspace_files(tmp_path: Path) -> None:
    """Prove an exact aggregate from facts split across five persistent files."""
    settings = _live_settings(tmp_path).model_copy(
        update={
            "rlm_recursion_enabled": False,
            "rlm_max_iters": 6,
            "rlm_max_llm_calls": 12,
            "turn_timeout_seconds": 840,
            "mlflow_tracing_enabled": True,
        }
    )
    _assert_tracking_server_reachable(settings.mlflow_tracking_uri)
    ledger = _LargeContextLedger()
    tool = dspy.Tool(
        ledger.verify_aggregate,
        name="verify_corpus_aggregate",
        desc="Verify the exact amount and source filename read from each corpus file.",
        arg_desc={
            "entries": "One entry per file with reference equal to its basename and amount equal to its fact.",
            "total": "The sum of all five amounts.",
        },
    )
    event_views = MappingProxyType(
        {
            "verify_corpus_aggregate": ToolEventView(
                input_projection=lambda values: {
                    "entry_count": len(values.get("entries", ())),
                    "total": values.get("total"),
                },
                output_projection=lambda result: {
                    "ok": bool(result.get("ok")),
                    "source_count": result.get("source_count"),
                },
            )
        }
    )
    app = create_app(settings=settings)
    trace_ids: list[str] = []

    def collect_trace_id(chunks: list[dict[str, Any]]) -> str:
        values = {
            value
            for chunk in chunks
            for value in (
                chunk.get("traceId"),
                *(
                    metadata.get("traceId")
                    for metadata in (chunk.get("metadata"), chunk.get("messageMetadata"))
                    if isinstance(metadata, dict)
                ),
            )
            if isinstance(value, str) and value
        }
        assert len(values) == 1
        return values.pop()

    cleanup_failures: tuple[str, ...] = ()
    with TestClient(app) as client:
        resources, preparation = live_runtime(app)
        assert client.portal is not None
        portal = client.portal
        portal_loop = portal.call(lambda: asyncio.get_running_loop())
        sandbox_ids: set[str] = set()
        object.__setattr__(
            preparation,
            "capabilities",
            _ProofCapabilityPreparer(
                preparation.capabilities,
                (tool,),
                event_views,
                signature=LargeContextProofResult,
            ),
        )
        try:
            created = client.post("/api/sessions", json={"title": "Synthetic distributed corpus proof"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            seeded = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Use exactly one iteration to SUBMIT answer='corpus seeded' and evidence='seed turn'."},
                headers={"Idempotency-Key": f"large-context-seed-{uuid4()}"},
            )
            assert seeded.status_code == 200
            seed_chunks, seed_done = _sse_chunks(seeded)
            assert seed_done == 1
            _assert_sse_stop(seed_chunks, label="large_context_seed")
            trace_ids.append(collect_trace_id(seed_chunks))

            binding = portal.call(resources._bindings.get, session_id)
            assert binding is not None and binding.sandbox_id is not None
            sandbox_ids.add(binding.sandbox_id)
            sandbox = sync_sandbox(portal.call(resources._platform.get, binding.sandbox_id), portal_loop)
            assert sandbox is not None
            workspace = Path(SESSION_WORKSPACE_MOUNT_PATH)
            sandbox.fs.create_folder(str(workspace / "analysis"), mode="755")
            volume_fs = DaytonaSandboxVolumeFs(sandbox)
            for filename, amount in ledger.expected:
                path = str(workspace / "analysis" / filename)
                volume_fs.write_bytes(
                    path,
                    f"source={filename}\namount={amount}\n".encode(),
                )
                assert volume_fs.read_bytes(path) == f"source={filename}\namount={amount}\n".encode()

            analysis = client.post(
                f"/api/sessions/{session_id}/turns",
                json={
                    "text": (
                        "Analyze the supplied synthetic corpus. Call read_workspace_text exactly once for each of "
                        "analysis/ledger-01.txt, analysis/ledger-02.txt, analysis/ledger-03.txt, "
                        "analysis/ledger-04.txt, and analysis/ledger-05.txt. Use only the returned file contents. "
                        "Extract each amount, preserve each basename as its evidence reference, sum the five "
                        "amounts, then call verify_corpus_aggregate exactly once with entries and total. "
                        "Only after ok=true, issue typed SUBMIT with answer='100' and evidence containing all five "
                        "basenames. Do not infer or invent any fact."
                    )
                },
                headers={"Idempotency-Key": f"large-context-analyze-{uuid4()}"},
            )
            assert analysis.status_code == 200
            chunks, done = _sse_chunks(analysis)
            assert done == 1
            _assert_sse_stop(chunks, label="large_context_analysis")
            tool_inputs = [chunk for chunk in chunks if chunk.get("type") in {"tool-input-available", "tool_call"}]
            reads = [chunk for chunk in tool_inputs if chunk.get("toolName") == "read_workspace_text"]
            assert len(reads) == 5
            read_paths = {str(chunk.get("input", {}).get("path", "")) for chunk in reads}
            assert read_paths == {f"analysis/{filename}" for filename, _ in ledger.expected}
            assert sum(chunk.get("toolName") == "verify_corpus_aggregate" for chunk in tool_inputs) == 1
            assert ledger.calls == 1
            result_chunks = [chunk for chunk in chunks if chunk.get("type") == "structured_result"]
            assert len(result_chunks) == 1
            value = result_chunks[0]["value"]
            assert value["answer"].strip() == "100"
            assert all(filename in value["evidence"] for filename, _ in ledger.expected)
            page = client.get(f"/api/sessions/{session_id}/turns")
            assert page.status_code == 200
            assert _structured_part(_assistant_messages(page.json())[-1])["data"]["value"] == value
            trace_ids.append(collect_trace_id(chunks))
        finally:
            cleanup_failures = portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()
    _write_receipt_if_requested(
        {
            "schema": "fleet.live-large-context/v1",
            "candidate": _candidate_metadata(settings),
            "trace_ids": trace_ids,
            "expected_total": 100,
            "source_count": len(ledger.expected),
            "cleanup_confirmed": True,
            "passed": True,
        }
    )


@pytest.mark.live_daytona
@pytest.mark.timeout(600)
def test_constrained_live_final_iteration_submits_typed_result(tmp_path: Path) -> None:
    """Exercise a real model at the final allowed iteration and require bounded submission."""
    settings = _live_settings(tmp_path).model_copy(
        update={
            "rlm_recursion_enabled": False,
            "rlm_max_iters": 1,
            "rlm_max_llm_calls": 4,
            "rlm_finalization_attempts": 2,
            "turn_timeout_seconds": 300,
            "mlflow_tracing_enabled": True,
        }
    )
    _assert_tracking_server_reachable(settings.mlflow_tracking_uri)
    app = create_app(settings=settings)
    trace_id: str | None = None
    cleanup_failures: tuple[str, ...] = ()
    with TestClient(app) as client:
        resources, preparation = live_runtime(app)
        assert client.portal is not None
        sandbox_ids: set[str] = set()
        object.__setattr__(
            preparation,
            "capabilities",
            _ProofCapabilityPreparer(
                preparation.capabilities,
                (),
                MappingProxyType({}),
                signature=NativeSemanticProofResult,
            ),
        )
        try:
            created = client.post("/api/sessions", json={"title": "Constrained final iteration proof"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={
                    "text": (
                        "Complete this one-iteration bounded task: submit answer='bounded-final' "
                        "with evidence='final-iteration'."
                    )
                },
                headers={"Idempotency-Key": f"live-finalization-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            _assert_sse_stop(chunks, label="constrained_finalization")
            usage_chunks = [chunk for chunk in chunks if chunk.get("type") in {"data-usage", "usage"}]
            assert len(usage_chunks) == 1
            usage_chunk = usage_chunks[0]
            usage = (
                usage_chunk["data"].get("usage", usage_chunk["data"])
                if usage_chunk.get("type") == "data-usage"
                else usage_chunk.get("usage", {})
            )
            assert int(usage["iterations"]) == 1
            assert int(usage["iterations"]) <= settings.rlm_max_iters
            assert len(_call_shapes(chunks, "SUBMIT")) == 1
            structured = [chunk for chunk in chunks if chunk.get("type") == "structured_result"]
            assert len(structured) == 1
            value = structured[0]["value"]
            assert value["answer"].strip() == "bounded-final"
            assert value["evidence"] == "final-iteration"
            trace_ids = {
                trace_id
                for chunk in chunks
                for trace_id in (
                    chunk.get("traceId"),
                    *(
                        metadata.get("traceId")
                        for metadata in (chunk.get("metadata"), chunk.get("messageMetadata"))
                        if isinstance(metadata, dict)
                    ),
                )
                if isinstance(trace_id, str) and trace_id
            }
            assert len(trace_ids) == 1
            trace_id = trace_ids.pop()
            binding = client.portal.call(resources._bindings.get, session_id)
            assert binding is not None and binding.sandbox_id is not None
            sandbox_ids.add(binding.sandbox_id)
        finally:
            cleanup_failures = client.portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()
    _write_receipt_if_requested(
        {
            "schema": "fleet.live-finalization/v1",
            "candidate": _candidate_metadata(settings),
            "trace_id": trace_id,
            "iterations": 1,
            "finalization_attempt_limit": settings.rlm_finalization_attempts,
            "cleanup_confirmed": True,
            "passed": True,
        }
    )


def _synthetic_failure_chunks() -> list[dict[str, Any]]:
    """Minimal failed stream: the shape a live run produced when it failed untriageably."""
    return [
        {"type": "start", "messageId": "synthetic-run"},
        {"type": "data-rlm-code", "data": {"code": "SUBMIT(answer=summary, evidence=evidence)"}},
        {
            "type": "tool-input-available",
            "toolCallId": "call-1",
            "toolName": "verify_semantic_work",
            "input": {
                "iteration_token": "iteration-1",
                "single_result": "ROOT",
                "batch_results": ["ALPHA", "BETA", "GAMMA"],
                "accumulator": ["iteration-1", "ROOT", "ALPHA", "BETA", "GAMMA"],
            },
        },
        {"type": "tool-output-error", "toolCallId": "call-1", "errorText": "semantic verification failed"},
        {"type": "error", "errorText": "Turn output is invalid " * 40},
        {"type": "finish", "finishReason": "error"},
    ]


def test_failure_receipt_renders_bounded_stream_diagnostic() -> None:
    """A failed run persists the triage facts that otherwise reach only a pytest message."""
    receipt = _failure_receipt(
        candidate={"sha": "a" * 40, "branch": "work", "tracked_tree_clean": True, "models": {"root": "m"}},
        started_at="2026-09-29T00:00:00+00:00",
        category="proof_failed",
        phase="native_semantic_calls",
        diagnostic=_failure_diagnostic({"native_semantic_calls": _synthetic_failure_chunks()}),
    )
    assert set(receipt) == {"schema", "candidate", "timing", "failure", "diagnostic", "passed"}
    assert receipt["schema"] == _RECEIPT_SCHEMA
    assert receipt["failure"] == {"category": "proof_failed", "phase": "native_semantic_calls"}
    assert receipt["passed"] is False
    diagnostic = receipt["diagnostic"]["native_semantic_calls"]
    assert diagnostic["chunk_types"] == {
        "start": 1,
        "data-rlm-code": 1,
        "tool-input-available": 1,
        "tool-output-error": 1,
        "error": 1,
        "finish": 1,
    }
    assert diagnostic["finish_reasons"] == ["error"]
    assert diagnostic["tool_errors"] == [
        {"toolName": "verify_semantic_work", "errorText": "semantic verification failed"}
    ]
    assert diagnostic["error_texts"][0].startswith("Turn output is invalid")
    assert len(diagnostic["error_texts"][0]) == _DIAGNOSTIC_TEXT_CHARS
    assert diagnostic["semantic_tool"]["classification"] == "semantic_tool_execution_failed"
    submit_shapes = diagnostic["submit_call_shapes"]
    assert submit_shapes[0]["keyword_names"] == ["answer", "evidence"]
    assert submit_shapes[0]["positional_count"] == 0
    assert len(json.dumps(receipt)) <= _DIAGNOSTIC_MAX_CHARS + 1_000
    # No captured stream still writes a well-formed receipt.
    assert (
        _failure_receipt(
            candidate={"sha": "a" * 40, "branch": "work", "tracked_tree_clean": False},
            started_at="2026-09-29T00:00:00+00:00",
            category="proof_failed",
            phase="composition",
        )["diagnostic"]
        == {}
    )


def test_stream_diagnostic_caps_long_strings_and_collections() -> None:
    """Per-string and per-collection caps keep a noisy stream out of the receipt."""
    long_text = "x" * 5_000
    chunks: list[dict[str, Any]] = [
        {"type": "error", "errorText": long_text},
        {"type": "finish", "finishReason": long_text},
        *({"type": "tool-output-error", "toolCallId": f"call-{index}", "errorText": long_text} for index in range(50)),
    ]
    diagnostic = _stream_diagnostic(chunks)
    assert diagnostic["chunk_types"] == {"error": 1, "finish": 1, "tool-output-error": 50}
    assert [len(text) for text in diagnostic["error_texts"]] == [_DIAGNOSTIC_TEXT_CHARS]
    assert [len(text) for text in diagnostic["finish_reasons"]] == [_DIAGNOSTIC_TEXT_CHARS]
    assert len(diagnostic["tool_errors"]) == _DIAGNOSTIC_MAX_ENTRIES
    assert all(len(entry["errorText"]) == _DIAGNOSTIC_TEXT_CHARS for entry in diagnostic["tool_errors"])
    assert len(json.dumps(diagnostic)) <= _DIAGNOSTIC_MAX_CHARS


def test_stream_diagnostic_truncates_when_the_budget_is_exceeded() -> None:
    """An adversarial stream falls back to a bounded marker instead of a stream dump."""
    long_text = "x" * 5_000
    chunks: list[dict[str, Any]] = [
        *({"type": "error", "errorText": long_text} for _ in range(_DIAGNOSTIC_MAX_ENTRIES)),
        *({"type": "finish", "finishReason": long_text} for _ in range(_DIAGNOSTIC_MAX_ENTRIES)),
        *(
            {"type": "tool-output-error", "toolCallId": f"call-{index}", "errorText": long_text}
            for index in range(_DIAGNOSTIC_MAX_ENTRIES)
        ),
        *(
            {
                "type": "tool-input-available",
                "toolCallId": f"call-{index}",
                "toolName": "verify_semantic_work",
                "input": {f"key-{field}": long_text for field in range(_DIAGNOSTIC_MAX_ENTRIES)},
            }
            for index in range(_DIAGNOSTIC_MAX_ENTRIES)
        ),
    ]
    diagnostic = _stream_diagnostic(chunks)
    assert diagnostic["truncated"] is True
    assert diagnostic["encoded_chars"] > _DIAGNOSTIC_MAX_CHARS
    assert diagnostic["sections"] == [
        "chunk_types",
        "error_texts",
        "finish_reasons",
        "semantic_tool",
        "submit_call_shapes",
        "tool_errors",
    ]
    assert len(diagnostic["head"]) == _DIAGNOSTIC_MAX_CHARS
    receipt = _failure_receipt(
        candidate={"sha": "a" * 40, "branch": "work", "tracked_tree_clean": True},
        started_at="2026-09-29T00:00:00+00:00",
        category="proof_failed",
        phase="native_semantic_calls",
        diagnostic={"native_semantic_calls": diagnostic},
    )
    assert len(json.dumps(receipt)) <= _DIAGNOSTIC_MAX_CHARS + 1_000


def test_tracking_precondition_fails_closed_and_names_the_uri() -> None:
    """A lane that certifies spans must not pass while its tracking server is dead."""
    with pytest.raises(pytest.fail.Exception, match=r"http://127\.0\.0\.1:1"):
        _assert_tracking_server_reachable("http://127.0.0.1:1")


def test_tracking_precondition_accepts_non_http_tracking_uri() -> None:
    """Non-HTTP URIs are not socket-probed, matching ``configure_tracing``."""
    _assert_tracking_server_reachable("databricks")
