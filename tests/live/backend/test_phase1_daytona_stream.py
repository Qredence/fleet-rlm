"""One-Turn live proof for Phase 1 native DSPy streaming on Daytona."""

from __future__ import annotations

import ast
import asyncio
import json
import os
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.config.loader import active_profile, require_live_execution
from fleet_rlm.config.settings import FleetConfigurationError, Settings
from fleet_rlm.rlm.events import ToolEventView
from fleet_rlm.rlm.program import has_llm_credentials
from tests.live.backend._database import upgrade_to_head

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(900)]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RECEIPT_SCHEMA = "fleet.phase1-daytona-stream/v1"
_EVIDENCE_ENV = "FLEET_PHASE1_STREAM_EVIDENCE_PATH"
_LIVE_ROOT_MODEL = os.environ.get("FLEET_LIVE_ROOT_MODEL", "databricks-deepseek-v4-flash-0731")
_LIVE_SUB_MODEL = os.environ.get("FLEET_LIVE_SUB_MODEL", "databricks-deepseek-v4-flash-0731")
_APPROVED_MODELS = frozenset(
    name
    for base in {
        _LIVE_ROOT_MODEL,
        _LIVE_ROOT_MODEL.removesuffix("-0731"),
        _LIVE_SUB_MODEL,
        _LIVE_SUB_MODEL.removesuffix("-0731"),
    }
    for name in (base, f"openai/{base}")
)
_CLEANUP_RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0)
_ATTACHMENT_CONTENT = "phase-one capsule witness: CEDAR-17\n"
_CONTRACT_ID = "fleet.phase1-daytona-stream"


class Phase1Result(dspy.Signature):
    """Return a multi-field result so the completion is necessarily typed."""

    request: str = dspy.InputField()
    session_context: dict = dspy.InputField()
    skill_cards: list[dict] = dspy.InputField()
    attachments: list[dict] = dspy.InputField()
    answer: str = dspy.OutputField()
    evidence: str = dspy.OutputField()


@dataclass(slots=True)
class _ProofCapabilityPreparer:
    delegate: Any
    tools: tuple[dspy.Tool, ...]
    event_views: MappingProxyType[str, ToolEventView]

    async def prepare(self, turn: Any, environment: Any, attachments: Any, *, deadline: float) -> Any:
        """
        Prepare a turn with the Phase 1 result signature, output contract, verification tools, and event views.

        Parameters:
                turn (Any): The turn to prepare.
                environment (Any): The execution environment.
                attachments (Any): Attachments available to the turn.
                deadline (float): The preparation deadline.

        Returns:
                Any: The prepared turn configured for Phase 1 execution.
        """
        prepared = await self.delegate.prepare(turn, environment, attachments, deadline=deadline)
        prepared.spec = replace(
            prepared.spec,
            # Instructions are recomposed from Fleet fragments at worker start; steering rides the request text.
            signature=Phase1Result,
            output_schema_id=_CONTRACT_ID,
            output_schema_version="1",
            tools=(*prepared.spec.tools, *self.tools),
            tool_event_views={**prepared.spec.tool_event_views, **self.event_views},
        )
        return prepared


@dataclass(slots=True)
class _ProofLedger:
    calls: int = 0
    semantic_calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    def verify_phase1(self, attachment_text: str, single_result: str, batch_results: list[str]) -> dict[str, bool]:
        """
        Validate the Phase 1 attachment and semantic-query results.

        Parameters:
            attachment_text (str): Materialized attachment content to validate.
            single_result (str): Result from the single semantic query.
            batch_results (list[str]): Ordered results from the batched semantic query.

        Returns:
            dict[str, bool]: A mapping containing `{"ok": True}` when validation succeeds.

        Raises:
            ValueError: If the verifier is called more than once or any expected attachment or semantic-query
                result is invalid.
        """
        self.calls += 1
        if self.calls != 1:
            raise ValueError("phase1 verifier must be called exactly once")
        if attachment_text != _ATTACHMENT_CONTENT:
            raise ValueError("capsule attachment was not locally materialized")
        if "ROOT" not in single_result.upper():
            raise ValueError("single native semantic call did not complete")
        if len(batch_results) != 2 or any(value.startswith("[ERROR]") for value in batch_results):
            raise ValueError("batched native semantic call did not complete")
        if any(token not in value.upper() for token, value in zip(("ALPHA", "BETA"), batch_results, strict=True)):
            raise ValueError("batched native semantic values are not ordered")
        self.semantic_calls.append((single_result, tuple(batch_results)))
        return {"ok": True}


@dataclass(slots=True)
class _FirstStreamDeltaProbe:
    first_delta_at: float | None = None

    def observe_body(self, body: bytes) -> None:
        """
        Record the timestamp of the first reasoning or RLM-code streaming event found in a response body.

        Parameters:
            body (bytes): Response body containing newline-delimited SSE data.
        """
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
        """
        Observe streamed HTTP response bodies while forwarding each message to the underlying sender.
        """
        pending = bytearray()

        async def recording_send(message: dict[str, Any]) -> None:
            """
            Record complete HTTP response-body lines for streaming inspection before forwarding each message.
            """
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


def _load_live_settings(tmp_path: Path) -> Settings:
    """
    Load and validate settings for the live Phase 1 streaming test.

    Parameters:
        tmp_path (Path): Temporary directory in which to create the test database.

    Returns:
        Settings: Validated settings using an upgraded temporary database and bounded execution limits.
    """
    if not os.environ.get(_EVIDENCE_ENV):
        pytest.skip("Run this credentialed canary via scripts/live_phase1_stream_verify.py")
    load_dotenv(_REPO_ROOT / ".env", override=False)
    try:
        policy = require_live_execution()
    except FleetConfigurationError:
        pytest.fail("Phase 1 stream canary requires runtime.live_enabled=true")
    if active_profile(policy) != "daytona" or policy.run_environment != "daytona":
        pytest.fail("Phase 1 stream canary requires the normal daytona profile")
    if policy.root_model not in _APPROVED_MODELS or policy.sub_model not in _APPROVED_MODELS:
        pytest.fail("Phase 1 stream canary requires the committed Root and Sub policy")
    if policy.daytona_api_key is None or not has_llm_credentials(policy):
        pytest.fail("Phase 1 stream canary is missing configured provider credentials")
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'phase1-stream.db').resolve()}"
    upgrade_to_head(database_url)
    return policy.model_copy(
        update={
            "database_url": database_url,
            "volume_name": f"fleet-rlm-phase1-stream-{uuid4()}",
            "rlm_max_iters": 5,
            "rlm_max_llm_calls": 8,
            "turn_timeout_seconds": 840,
        }
    )


def _sse_chunks(response: Any) -> tuple[list[dict[str, Any]], int]:
    """
    Parse server-sent event data from an HTTP response.

    Parameters:
        response (Any): Response containing newline-delimited SSE data.

    Returns:
        tuple[list[dict[str, Any]], int]: Parsed JSON event chunks and the number of `[DONE]` markers.
    """
    chunks: list[dict[str, Any]] = []
    done = 0
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line.removeprefix("data: ")
        if payload == "[DONE]":
            done += 1
        else:
            chunks.append(json.loads(payload))
    return chunks, done


def _streaming_evidence(chunks: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """
    Summarize reasoning and code streaming evidence from parsed SSE chunks.

    Parameters:
        chunks (list[dict[str, Any]]): Parsed streaming chunks to inspect.

    Returns:
        tuple[int, list[str]]: The number of relevant delta events and the sorted
        names of streaming fields observed.
    """
    fields: set[str] = set()
    count = 0
    for chunk in chunks:
        if chunk.get("type") == "reasoning-delta":
            fields.add("reasoning")
            count += 1
        elif chunk.get("type") == "data-rlm-code" and chunk.get("data", {}).get("is_delta") is True:
            fields.add("code")
            count += 1
    return count, sorted(fields)


def _call_shapes(chunks: list[dict[str, Any]], call_name: str) -> list[dict[str, object]]:
    """
    Extracts argument shapes for calls with the specified name from streamed RLM code chunks.

    Parameters:
        chunks (list[dict[str, Any]]): Streamed event chunks containing RLM code.
        call_name (str): Function name to match.

    Returns:
        list[dict[str, object]]: Argument counts and keyword names for each matching call.
    """
    shapes: list[dict[str, object]] = []
    for chunk in chunks:
        if chunk.get("type") != "data-rlm-code":
            continue
        code = str(chunk.get("data", {}).get("code", ""))
        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name == call_name:
                shapes.append({"args": len(node.args), "keywords": sorted(key.arg for key in node.keywords if key.arg)})
    return shapes


async def _retry_cleanup(operation: Any) -> bool:
    """Retry an asynchronous cleanup operation until it succeeds or all configured attempts fail.

    Parameters:
        operation (Any): Asynchronous cleanup operation to execute.

    Returns:
        bool: `True` if the operation succeeds, `False` after all attempts fail.
    """
    for delay in (*_CLEANUP_RETRY_DELAYS, None):
        try:
            await operation()
            return True
        except Exception:
            if delay is None:
                return False
            await asyncio.sleep(delay)
    return False


async def _strict_cleanup(resources: Any, volume_name: str) -> tuple[str, ...]:
    """
    Delete tracked sandboxes and the owned volume, returning labels for cleanup failures.

    Parameters:
        resources (Any): Resource manager containing tracked sandboxes and cleanup clients.
        volume_name (str): Name of the volume to delete.

    Returns:
        tuple[str, ...]: Cleanup failure labels, including "sandbox", "tracking", or "volume".
    """
    failures: list[str] = []
    for sandbox_id in sorted(set(resources._sandbox_ids)):

        async def delete_sandbox(sandbox_id: str = sandbox_id) -> None:
            """Delete the specified Daytona sandbox if it exists.

            Parameters:
                sandbox_id (str): Identifier of the sandbox to delete.
            """
            sandbox = await resources.platform.get(sandbox_id)
            if sandbox is not None:
                await resources.platform.delete(sandbox)

        if not await _retry_cleanup(delete_sandbox):
            failures.append("sandbox")
    try:
        resources._sandbox_ids.clear()
    except Exception:
        failures.append("tracking")

    async def delete_volume() -> None:
        """Delete the configured volume if it exists."""
        volume = await resources.client.volume.get(volume_name, create=False)
        if volume is not None:
            await resources.client.volume.delete(volume)

    if not await _retry_cleanup(delete_volume):
        failures.append("volume")
    return tuple(failures)


def _write_receipt(payload: dict[str, object]) -> None:
    """
    Write a JSON proof receipt to the configured evidence path using an atomic replacement.

    Parameters:
        payload (dict[str, object]): Receipt data to serialize.
    """
    output = Path(os.environ[_EVIDENCE_ENV]).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
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
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def test_phase1_daytona_stream_through_fastapi(tmp_path: Path) -> None:
    settings = _load_live_settings(tmp_path)
    ledger = _ProofLedger()
    probe = _FirstStreamDeltaProbe()
    app = create_app(settings=settings)
    resources: Any | None = None
    cleanup_failures: tuple[str, ...] = ()
    pending_receipt: dict[str, object] | None = None
    started = time.perf_counter()

    proof_tool = dspy.Tool(
        ledger.verify_phase1,
        name="verify_phase1",
        desc="Verify the locally materialized Attachment and native semantic calls exactly once.",
    )
    proof_views = MappingProxyType(
        {
            "verify_phase1": ToolEventView(
                input_projection=lambda values: {
                    "attachment_chars": len(str(values.get("attachment_text", ""))),
                    "single_result_type": type(values.get("single_result")).__name__,
                    "batch_count": len(values.get("batch_results", ())),
                },
                output_projection=lambda result: {"ok": bool(result.get("ok"))},
            )
        }
    )
    app.add_middleware(_FirstStreamDeltaMiddleware, probe=probe)
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        preparation = inventory.run_preparation
        assert resources is not None
        assert preparation is not None
        preparation._capabilities = _ProofCapabilityPreparer(preparation._capabilities, (proof_tool,), proof_views)
        try:
            created = client.post("/api/sessions", json={"title": "Phase 1 Daytona stream canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            uploaded = client.post(
                "/api/attachments",
                files={"attachment": ("phase1.txt", _ATTACHMENT_CONTENT.encode(), "text/plain")},
            )
            assert uploaded.status_code == 201
            attachment_id = uploaded.json()["id"]
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={
                    "text": (
                        "Execute the narrow native DSPy Phase 1 stream proof. Do exactly one bounded"
                        " Daytona proof. In the first iteration read attachment_text ="
                        ' attachments[0]["data"]; do not use a file Tool. Then call'
                        ' single_result = llm_query("Return exactly ROOT") and'
                        ' batch_results = llm_query_batched(["Return exactly ALPHA", "Return exactly BETA"]).'
                        " Reject any result beginning with [ERROR]. Call exactly once:"
                        " verification = verify_phase1(attachment_text=attachment_text,"
                        " single_result=single_result, batch_results=batch_results)."
                        ' Require verification["ok"]. Finally call exactly'
                        ' SUBMIT(answer="phase1 complete", evidence="native Daytona stream")'
                        " with keyword arguments. Do not call rlm_query, do not retry, and do not"
                        " use extraction fallback."
                    ),
                    "attachment_ids": [attachment_id],
                },
                headers={"Idempotency-Key": f"phase1-daytona-stream-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            assert chunks[-1].get("type") == "finish"
            assert chunks[-1].get("finishReason") == "stop"
            assert probe.first_delta_at is not None
            delta_count, fields = _streaming_evidence(chunks)
            assert delta_count >= 1
            assert fields
            assert any(
                chunk.get("type") == "data-attachment"
                and chunk.get("data", {}).get("attachmentId") == attachment_id
                and chunk.get("data", {}).get("phase") == "read"
                for chunk in chunks
            )
            generated_code = "\n".join(
                str(chunk.get("data", {}).get("code", "")) for chunk in chunks if chunk.get("type") == "data-rlm-code"
            )
            assert "llm_query" in generated_code
            assert "llm_query_batched" in generated_code
            assert "rlm_query" not in generated_code
            assert "Extract forced final output" not in generated_code
            assert ledger.calls == 1
            assert len(ledger.semantic_calls) == 1
            submit_shapes = _call_shapes(chunks, "SUBMIT")
            assert submit_shapes == [{"args": 0, "keywords": ["answer", "evidence"]}]
            structured = [chunk for chunk in chunks if chunk.get("type") == "data-structured-result"]
            assert len(structured) == 1
            assert structured[0].get("data", {}).get("schema_id") == _CONTRACT_ID
            pending_receipt = {
                "schema": _RECEIPT_SCHEMA,
                "timing": {
                    "first_delta_ms": int((probe.first_delta_at - started) * 1000),
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                },
                "streaming": {"delta_count": delta_count, "fields": fields},
                "assertions": {
                    "typed_submit": True,
                    "attachment_prepared": True,
                    "attachment_accessed": True,
                    "single_semantic_call": True,
                    "batched_semantic_call": True,
                    "no_recursive_child": True,
                    "terminal_ordering": True,
                    "broker_session_cleanup": True,
                    "turn_resources_cleanup": True,
                },
                "resources": {
                    "sandbox_count": len(resources._sandbox_ids),
                    "broker_session_count": len(resources._sandbox_ids),
                    "owned_volume_only": True,
                },
                "failure": None,
                "passed": True,
            }
        finally:
            assert client.portal is not None
            cleanup_failures = client.portal.call(_strict_cleanup, resources, settings.volume_name)
    assert cleanup_failures == ()
    assert pending_receipt is not None
    _write_receipt(pending_receipt)
