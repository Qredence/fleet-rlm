"""Opt-in end-to-end proof of the Daytona-backed Fleet RLM MVP."""

from __future__ import annotations

import ast
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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from fleet_rlm.api.local_scope import LocalScope
from fleet_rlm.app import create_app
from fleet_rlm.config import (
    Settings,
    active_profile_contract,
    load_profile_environment_contracts,
    load_runtime_settings,
)
from fleet_rlm.daytona.workspace_fs import DaytonaSandboxVolumeFs
from fleet_rlm.files.volume_paths import volume_paths_from_settings
from fleet_rlm.rlm.tool_observer import ToolEventView
from fleet_rlm.runtime.bindings import SandboxBinding
from fleet_rlm.skills.catalog import stable_skill_id
from tests.live.backend._database import upgrade_to_head

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(900)]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTRACT_ID = "fleet.live-daytona-mvp"
_CAPABILITY_ID = "fleet.live-daytona-mvp"
_WORKSPACE_PATH = "notes/findings.md"
_RECEIPT_SCHEMA = "fleet.daytona-mvp-proof/v1"
_EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"
_SECRET_NAMES = tuple(
    name for contract in load_profile_environment_contracts() for name in contract.provider_environment_names
)
_CLEANUP_RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0)
_LIVE_ROOT_MODEL = "deepseek-v4-flash"
_LIVE_SUB_MODEL = "deepseek-v4-flash"


class LiveDaytonaMVPResult(dspy.Signature):
    """Return the bounded typed result for the live Daytona proof."""

    request: str = dspy.InputField()
    session_context: dict = dspy.InputField()
    skill_cards: list[dict] = dspy.InputField()
    attachments: list[dict] = dspy.InputField()
    answer: str = dspy.OutputField()
    findings: list[dict[str, str]] = dspy.OutputField()


_FIRST_SCENARIO = """
3 iterations; do not inspect `scenario`, improvise, or retry. 1) Once:
`iteration_token = issue_iteration_token(); accumulator = [iteration_token]; print("FIRST_ITERATION_READY")`.
2) Reuse it. Run `single_result = llm_query("Return exactly ROOT")` and
`batch_results = llm_query_batched(["Return exactly ALPHA", "Return exactly BETA", "Return exactly GAMMA"])`.
Reject `[ERROR]`; extend with `[single_result, *batch_results]`. Call once, no
casts/copies/positional args:
`verification = verify_semantic_work(iteration_token=iteration_token,
single_result=single_result, batch_results=batch_results,
accumulator=accumulator)`.
Append `notes/findings.md` with results and `verification["checksum"]` via
`append_workspace_text(path="notes/findings.md", content=content)`; require
`workspace_result["ok"]`. Publish the existing Workspace document without
resending its body via `publish_workspace_artifact(path="notes/findings.md",
kind="markdown", title="Findings")`; require `artifact_result["ok"]`; print
`SECOND_ITERATION_READY`. 3) Set
non-empty string-only `summary`/`findings`; call exactly
`SUBMIT(answer=summary, findings=findings)` with keywords. No fallback.
""".strip()

_SECOND_SCENARIO = """
Exactly two RLM iterations; do not create `accumulator`, explore, parse, or
retry. 1) Set `accumulator_present = "accumulator" in globals()`, then call
`workspace_result = read_workspace_text(path="notes/findings.md", max_chars=10000)`.
Require dictionary key `workspace_result["ok"]`; call exactly once
`reload_verification = verify_workspace_reload(
workspace_content=workspace_result["content"],
accumulator_present=accumulator_present)`;
require `reload_verification["ok"]`, set `workspace_checksum`, and print
`RELOAD_ITERATION_READY`. 2) Set non-empty string-only `summary` and `findings`,
then call exactly `SUBMIT(answer=summary, findings=findings)` with keywords.
""".strip()


@dataclass(slots=True)
class _ProofCapabilityPreparer:
    delegate: Any
    tools: tuple[dspy.Tool, ...]
    event_views: MappingProxyType[str, ToolEventView]

    async def prepare(self, turn: Any, environment: Any, attachments: Any, *, deadline: float) -> Any:
        prepared = await self.delegate.prepare(turn, environment, attachments, deadline=deadline)
        scenario = _SECOND_SCENARIO if str(turn.input.text).startswith("SECOND") else _FIRST_SCENARIO
        prepared.spec = replace(
            prepared.spec,
            signature=LiveDaytonaMVPResult.with_instructions(scenario),
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


def _load_repo_env() -> None:
    """Load repo ``.env`` into the process without overriding exported values."""
    load_dotenv(_REPO_ROOT / ".env", override=False)


def _live_settings(tmp_path: Path) -> Settings:
    """
    Load and configure settings required for the live Daytona MVP proof.

    Parameters:
        tmp_path (Path): Temporary directory in which to create the proof database.

    Returns:
        Settings: Runtime settings configured with a temporary database and bounded proof resources.
    """
    _load_repo_env()
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("Set FLEET_LIVE=1 for the complete Daytona MVP proof")
    required_environment = active_profile_contract().provider_environment_names
    missing = [name for name in required_environment if not os.environ.get(name)]
    if missing:
        pytest.fail("Live Daytona MVP proof missing required credentials: " + ", ".join(missing))
    policy = load_runtime_settings()
    if (policy.root_model, policy.sub_model) != (_LIVE_ROOT_MODEL, _LIVE_SUB_MODEL):
        pytest.fail("Live Daytona MVP proof requires the production DeepSeek v4 Flash Root and Sub policy")
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'live-mvp.db').resolve()}"
    upgrade_to_head(database_url)
    return policy.model_copy(
        update={
            "database_url": database_url,
            "volume_name": f"fleet-rlm-live-mvp-{uuid4()}",
            "rlm_max_iterations": 8,
            "rlm_max_llm_calls": 12,
            "turn_timeout_seconds": 840,
        }
    )


def _sse_chunks(response: Any) -> tuple[list[dict[str, Any]], int]:
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


def _call_shapes(chunks: list[dict[str, Any]], call_name: str) -> list[dict[str, object]]:
    shapes: list[dict[str, object]] = []
    for chunk in chunks:
        if chunk.get("type") != "data-rlm-code":
            continue
        code = str(chunk.get("data", {}).get("code", ""))
        if call_name not in code:
            continue
        try:
            tree = ast.parse(code)
        except SyntaxError:
            shapes.append({"parse": "invalid"})
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name != call_name:
                continue
            shapes.append(
                {
                    "positional_count": len(node.args),
                    "positional_kinds": [type(argument).__name__ for argument in node.args],
                    "keyword_names": [keyword.arg or "**" for keyword in node.keywords],
                    "keyword_kinds": {keyword.arg or "**": type(keyword.value).__name__ for keyword in node.keywords},
                }
            )
    return shapes


def _semantic_tool_diagnostic(chunks: list[dict[str, Any]]) -> dict[str, object]:
    inputs = [
        chunk.get("input") if isinstance(chunk.get("input"), dict) else {}
        for chunk in chunks
        if chunk.get("type") == "tool-input-available" and chunk.get("toolName") == "verify_semantic_work"
    ]
    call_ids = {
        str(chunk.get("toolCallId", ""))
        for chunk in chunks
        if chunk.get("type") == "tool-input-available" and chunk.get("toolName") == "verify_semantic_work"
    }
    failures = [
        str(chunk.get("errorText", ""))
        for chunk in chunks
        if chunk.get("type") == "tool-output-error" and str(chunk.get("toolCallId", "")) in call_ids
    ]
    if not inputs:
        classification = "semantic_tool_not_observed"
    elif not failures:
        classification = "semantic_tool_not_failed"
    elif any(not values for values in inputs):
        classification = "signature_bind_failed"
    elif any(error == "Tool arguments are invalid" for error in failures):
        expected_types = {
            "iteration_token_type": "str",
            "single_result_type": "str",
            "batch_results_type": "list",
            "batch_result_item_types": ["str"],
            "accumulator_type": "list",
            "accumulator_item_types": ["str"],
        }
        classification = (
            "validator_or_transport_mismatch"
            if all(all(values.get(key) == value for key, value in expected_types.items()) for values in inputs)
            else "dspy_type_validation_failed"
        )
    else:
        classification = "semantic_tool_execution_failed"
    return {
        "call_shapes": _call_shapes(chunks, "verify_semantic_work"),
        "bound_shapes": inputs,
        "classification": classification,
    }


def _sse_finish_diagnostic(chunks: list[dict[str, Any]]) -> str:
    """Bounded summary for failed stop assertions (no code dumps or secrets)."""
    type_counts: dict[str, int] = {}
    tool_names_by_call: dict[str, str] = {}
    for chunk in chunks:
        kind = str(chunk.get("type", "?"))
        type_counts[kind] = type_counts.get(kind, 0) + 1
        if kind == "tool-input-available":
            call_id = str(chunk.get("toolCallId", ""))
            name = str(chunk.get("toolName", ""))
            if call_id and name:
                tool_names_by_call[call_id] = name
    error_texts = [str(chunk.get("errorText", ""))[:200] for chunk in chunks if chunk.get("type") == "error"]
    finish_reasons = [str(chunk.get("finishReason", "")) for chunk in chunks if chunk.get("type") == "finish"]
    tool_errors = [
        {
            "toolName": tool_names_by_call.get(str(chunk.get("toolCallId", "")), "unknown"),
            "errorText": str(chunk.get("errorText", ""))[:200],
        }
        for chunk in chunks
        if chunk.get("type") == "tool-output-error"
    ]
    return (
        f"chunk_types={dict(sorted(type_counts.items()))} "
        f"finish_reasons={finish_reasons} "
        f"error_texts={error_texts} "
        f"tool_errors={tool_errors} "
        f"semantic_tool={_semantic_tool_diagnostic(chunks)} "
        f"submit_call_shapes={_call_shapes(chunks, 'SUBMIT')}"
    )


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


def _assert_sse_stop(chunks: list[dict[str, Any]], *, label: str) -> None:
    if not chunks:
        pytest.fail(f"{label}: no SSE chunks")
    last = chunks[-1]
    if last.get("type") != "finish" or last.get("finishReason") != "stop":
        pytest.fail(
            f"{label}: expected finish/stop, got type={last.get('type')!r} "
            f"finishReason={last.get('finishReason')!r}; {_sse_finish_diagnostic(chunks)}"
        )


def _assistant_messages(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in page["items"] if item["role"] == "assistant"]


def _structured_part(message: dict[str, Any]) -> dict[str, Any]:
    return next(part for part in message["parts"] if part["type"] == "data-structured-result")


def _assert_secret_free(secret_values: tuple[str, ...], *values: object) -> None:
    payload = json.dumps(values, ensure_ascii=False, default=str)
    if any(secret and secret in payload for secret in secret_values):
        pytest.fail("provider credential leaked into a client-visible or durable proof surface")


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


def _failure_receipt(
    *,
    candidate: dict[str, object],
    started_at: str,
    category: str,
    phase: str,
) -> dict[str, object]:
    return {
        "schema": _RECEIPT_SCHEMA,
        "candidate": {key: candidate[key] for key in ("sha", "branch", "tracked_tree_clean")},
        "timing": {
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
        },
        "failure": {"category": category, "phase": phase},
        "passed": False,
    }


def _session_volume_files(sandbox: Any, session_dir: str) -> list[bytes]:
    files: list[bytes] = []
    for info in sandbox.fs.list_files(session_dir, depth=8):
        if info.is_dir or not info.path:
            continue
        files.append(sandbox.fs.download_file(info.path))
    return files


def _sandbox_environment_names(sandbox: Any) -> set[str]:
    result = sandbox.process.exec(
        "python -c 'import json,os; print(json.dumps(sorted(os.environ)))'",
        timeout=30,
    )
    if result.exit_code != 0:
        raise AssertionError("could not inspect Sandbox environment names")
    return set(json.loads(result.result.strip()))


def _retry_cleanup(operation: Any) -> bool:
    for attempt, delay in enumerate((0.0, *_CLEANUP_RETRY_DELAYS)):
        try:
            operation()
            return True
        except Exception:
            if attempt == len(_CLEANUP_RETRY_DELAYS):
                return False
            if delay:
                time.sleep(delay)
    return False


def _strict_cleanup(resources: Any, sandbox_ids: set[str], volume_name: str) -> tuple[str, ...]:
    failures: list[str] = []
    tracked_ids = sandbox_ids | set(resources._sandbox_ids)
    for sandbox_id in sorted(tracked_ids):

        def delete_sandbox(sandbox_id: str = sandbox_id) -> None:
            sandbox = resources.platform.get(sandbox_id)
            if sandbox is not None:
                resources.platform.delete(sandbox)

        if not _retry_cleanup(delete_sandbox):
            failures.append("sandbox")
    try:
        resources.forget_sandboxes()
    except Exception:
        failures.append("tracking")

    def delete_volume() -> None:
        volume = resources.client.volume.get(volume_name, create=False)
        resources.client.volume.delete(volume)

    if not _retry_cleanup(delete_volume):
        failures.append("volume")
    return tuple(failures)


async def _replace_binding(resources: Any, binding: SandboxBinding) -> SandboxBinding:
    return await resources.session_manager.replace(
        binding,
        workspace_id=binding.workspace_id,
        user_id=LocalScope().user_id,
    )


def test_direct_pi_digit_uses_deterministic_repl_without_optional_capabilities(tmp_path: Path) -> None:
    settings = _live_settings(tmp_path).model_copy(
        update={
            "rlm_max_iterations": 3,
            "turn_timeout_seconds": 840,
        }
    )
    app = create_app(settings=settings)
    sandbox_ids: set[str] = set()
    cleanup_failures: tuple[str, ...] = ()

    with TestClient(app) as client:
        resources = app.state.runtime_inventory.run_environment_resources
        assert resources is not None
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

            code_chunks = [chunk for chunk in chunks if chunk.get("type") == "data-rlm-code"]
            output_chunks = [chunk for chunk in chunks if chunk.get("type") == "data-rlm-output"]
            usage_chunks = [chunk for chunk in chunks if chunk.get("type") == "data-usage"]
            structured = [chunk for chunk in chunks if chunk.get("type") == "data-structured-result"]
            assert len(usage_chunks) == 1
            # The default one-output Signature is projected as text; multi-output
            # Signatures use data-structured-result.
            assert structured == []
            text = "".join(str(chunk.get("delta", "")) for chunk in chunks if chunk.get("type") == "text-delta")
            assert text == "1"

            usage = usage_chunks[0]["data"].get("usage", usage_chunks[0]["data"])
            assert 2 <= int(usage["iterations"]) <= 3
            assert 2 <= len(code_chunks) <= 3

            tool_names = [
                str(chunk.get("toolName", "")) for chunk in chunks if chunk.get("type") == "tool-input-available"
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

            outputs = [str(chunk.get("data", {}).get("output", "")) for chunk in output_chunks]
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

            binding = portal.call(resources.bindings.get, session_id)
            assert binding is not None
            assert binding.sandbox_id is not None
            sandbox_ids.add(binding.sandbox_id)
            assert resources.platform.get(binding.sandbox_id) is not None
        finally:
            cleanup_failures = _strict_cleanup(resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()


def test_complete_daytona_mvp_through_fastapi(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _live_settings(tmp_path)
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
                input_projection=lambda values: {
                    "iteration_token_type": type(values.get("iteration_token")).__name__,
                    "single_result_type": type(values.get("single_result")).__name__,
                    "batch_results_type": type(values.get("batch_results")).__name__,
                    "batch_result_item_types": sorted(
                        {type(value).__name__ for value in values.get("batch_results", ())}
                    )
                    if isinstance(values.get("batch_results"), (list, tuple))
                    else [],
                    "batch_count": len(values["batch_results"])
                    if isinstance(values.get("batch_results"), (list, tuple))
                    else 0,
                    "accumulator_type": type(values.get("accumulator")).__name__,
                    "accumulator_item_types": sorted({type(value).__name__ for value in values.get("accumulator", ())})
                    if isinstance(values.get("accumulator"), (list, tuple))
                    else [],
                    "accumulator_count": len(values["accumulator"])
                    if isinstance(values.get("accumulator"), (list, tuple))
                    else 0,
                },
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
            inventory = app.state.runtime_inventory
            resources = inventory.run_environment_resources
            preparation = inventory.run_preparation
            assert resources is not None
            assert preparation is not None
            preparation._capabilities = _ProofCapabilityPreparer(
                preparation._capabilities,
                proof_tools,
                proof_views,
            )
            portal = client.portal
            assert portal is not None
            try:
                phase = "first_turn"
                created = client.post("/api/sessions", json={"title": "Live Daytona MVP proof"})
                assert created.status_code == 201
                session_id = UUID(created.json()["id"])

                first_started = time.perf_counter()
                first = client.post(
                    f"/api/sessions/{session_id}/turns",
                    json={
                        "text": "FIRST: execute the complete recursive Daytona MVP proof.",
                        "skill_selections": [{"id": str(skill.card.id), "expected_version": skill.card.version}],
                    },
                    headers={"Idempotency-Key": f"live-mvp-first-{uuid4()}"},
                )
                assert first.status_code == 200
                first_chunks, first_done = _sse_chunks(first)
                first_run_id = UUID(str(next(chunk["messageId"] for chunk in first_chunks if chunk["type"] == "start")))
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
                assert first_structured["data"]["schemaId"] == _CONTRACT_ID
                assert first_structured["data"]["value"] == structured_chunks[0]["data"]["value"]

                phase = "first_durability"
                binding = portal.call(resources.bindings.get, session_id)
                assert binding is not None
                assert binding.sandbox_id is not None
                assert binding.volume_id is not None
                sandbox_ids.add(binding.sandbox_id)
                first_sandbox = resources.platform.get(binding.sandbox_id)
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
                        "text": "SECOND: verify fresh interpreter state and durable workspace reload.",
                        "skill_selections": [{"id": str(skill.card.id), "expected_version": skill.card.version}],
                    },
                    headers={"Idempotency-Key": f"live-mvp-second-{uuid4()}"},
                )
                assert second.status_code == 200
                second_chunks, second_done = _sse_chunks(second)
                second_run_id = UUID(
                    str(next(chunk["messageId"] for chunk in second_chunks if chunk["type"] == "start"))
                )
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
                replacement_sandbox = resources.platform.get(replacement.sandbox_id)
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
                cleanup_failures = _strict_cleanup(resources, sandbox_ids, settings.volume_name)
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
                )
            )
        raise
