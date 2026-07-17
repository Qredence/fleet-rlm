"""Opt-in end-to-end proof of the Daytona-backed Fleet RLM MVP."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
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
from fleet_rlm.config import Settings
from fleet_rlm.daytona.bindings import SandboxBinding
from fleet_rlm.daytona.paths import volume_paths_from_settings
from fleet_rlm.daytona.volume_fs import DaytonaSandboxVolumeFs
from fleet_rlm.rlm.tool_observer import ToolEventView
from fleet_rlm.skills.capabilities import (
    CapabilityRLMRequirements,
    DSPySkillSelector,
    SkillSelection,
    TaskContract,
)
from tests.live.backend._database import upgrade_to_head

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(900)]

_CONTRACT_ID = "fleet.live-daytona-mvp"
_CAPABILITY_ID = "fleet.live-daytona-mvp"
_WORKSPACE_PATH = "notes/findings.md"
_RECEIPT_SCHEMA = "fleet.daytona-mvp-proof/v1"
_EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"
_SECRET_NAMES = ("FLEET_DAYTONA_API_KEY", "FLEET_LLM_API_KEY")


class LiveDaytonaMVPResult(dspy.Signature):
    """Return the bounded typed result for the live Daytona proof."""

    request: str = dspy.InputField()
    scenario: str = dspy.InputField()
    summary: str = dspy.OutputField()
    findings: list[dict[str, str]] = dspy.OutputField()


_FIRST_SCENARIO = """
Execute this proof in exactly three separate RLM iterations.

Iteration 1: call issue_iteration_token(), assign the returned string to
`iteration_token`, assign `accumulator = [iteration_token]`, print
`FIRST_ITERATION_READY`, and stop this iteration. Do not call SUBMIT.

Iteration 2: reuse the existing `accumulator` without recreating it. Call
llm_query("Return exactly ROOT") once. Then call llm_query_batched with these
prompts in this exact order: ["Return exactly ALPHA", "Return exactly BETA",
"Return exactly GAMMA"]. Reject any result starting with "[ERROR]". Extend the
accumulator with the single result followed by the ordered batch results. Call
verify_semantic_work(iteration_token, single_result, batch_results,
accumulator). Create a concise Markdown document containing the semantic
results and the returned proof checksum. Call write_workspace_text with path
"notes/findings.md", that Markdown content, and overwrite=False. Require its
`ok` field to be true, print `SECOND_ITERATION_READY`, and stop this iteration.
Do not call SUBMIT.

Iteration 3: call SUBMIT with a non-empty summary and a findings list containing
at least one object whose string fields describe the completed recursive,
batched, host-tool, and workspace work. Do not use extraction fallback.
""".strip()

_SECOND_SCENARIO = """
Execute this proof in exactly two separate RLM iterations.

Iteration 1: evaluate whether "accumulator" is present in globals() without
creating it. Call read_workspace_text("notes/findings.md", 10000), require its
`ok` field to be true, and pass its `content` plus the globals() boolean to
verify_workspace_reload. Require the returned `ok` field to be true, assign the
returned checksum to `workspace_checksum`, print `RELOAD_ITERATION_READY`, and
stop this iteration. Do not call SUBMIT.

Iteration 2: call SUBMIT with a non-empty summary and a findings list containing
at least one object whose string fields state that the interpreter was fresh
and the durable workspace was read successfully. Do not use extraction
fallback.
""".strip()


def _task_inputs(context: Any) -> dict[str, str]:
    request = str(context.request)
    scenario = _SECOND_SCENARIO if request.startswith("SECOND") else _FIRST_SCENARIO
    return {"request": request, "scenario": scenario}


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


def _live_settings(tmp_path: Path) -> Settings:
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("Set FLEET_LIVE=1 for the complete Daytona MVP proof")
    missing = [name for name in ("FLEET_DAYTONA_API_KEY", "FLEET_LLM_API_KEY") if not os.environ.get(name)]
    if missing:
        pytest.fail("Live Daytona MVP proof missing required credentials: " + ", ".join(missing))
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'live-mvp.db').resolve()}"
    upgrade_to_head(database_url)
    return Settings(
        run_environment="daytona",
        database_url=database_url,
        volume_name=f"fleet-rlm-live-mvp-{uuid4()}",
        rlm_max_iterations=8,
        rlm_max_llm_calls=12,
        turn_timeout_seconds=840,
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


def _strict_cleanup(resources: Any, sandbox_ids: set[str], volume_name: str) -> tuple[str, ...]:
    failures: list[str] = []
    tracked_ids = sandbox_ids | set(resources._sandbox_ids)  # noqa: SLF001 - strict test cleanup
    for sandbox_id in sorted(tracked_ids):
        try:
            sandbox = resources.platform.get(sandbox_id)
            if sandbox is not None:
                resources.platform.delete(sandbox)
        except Exception:  # noqa: BLE001 - retain only a bounded cleanup phase
            failures.append("sandbox")
    try:
        resources.forget_sandboxes()
    except Exception:  # noqa: BLE001 - retain only a bounded cleanup phase
        failures.append("tracking")
    try:
        volume = resources.client.volume.get(volume_name, create=False)
        resources.client.volume.delete(volume)
    except Exception:  # noqa: BLE001 - retain only a bounded cleanup phase
        failures.append("volume")
    return tuple(failures)


async def _replace_binding(resources: Any, binding: SandboxBinding) -> SandboxBinding:
    return await resources.session_manager.replace(
        binding,
        workspace_id=binding.workspace_id,
        user_id=LocalScope().user_id,
    )


def test_complete_daytona_mvp_through_fastapi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    token_tool = dspy.Tool(
        ledger.issue_iteration_token,
        name="issue_iteration_token",
        desc="Issue the opaque token used to prove state across RLM iterations.",
    )
    semantic_tool = dspy.Tool(
        ledger.verify_semantic_work,
        name="verify_semantic_work",
        desc="Verify ordered recursive semantic work and the persisted Python accumulator.",
    )
    reload_tool = dspy.Tool(
        ledger.verify_workspace_reload,
        name="verify_workspace_reload",
        desc="Verify a fresh interpreter read the durable Session Workspace content.",
    )
    task_contract = TaskContract(
        id=_CONTRACT_ID,
        schema_version="1",
        signature=LiveDaytonaMVPResult,
        input_mapper=_task_inputs,
        text_field="summary",
    )
    app.state.capability_registry.register(
        _CAPABILITY_ID,
        tools=(token_tool, semantic_tool, reload_tool),
        tool_event_views=MappingProxyType(
            {
                "issue_iteration_token": ToolEventView(
                    output_projection=lambda _result: {"issued": True},
                ),
                "verify_semantic_work": ToolEventView(
                    input_projection=lambda values: {
                        "batch_count": len(values.get("batch_results", ())),
                        "accumulator_count": len(values.get("accumulator", ())),
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
        ),
        task_contract=task_contract,
        rlm_requirements=CapabilityRLMRequirements(min_iterations=5, min_llm_calls=4),
    )
    skill = app.state.skill_registry.register(
        name="live-daytona-mvp-proof",
        description="Host-owned capability for the opt-in Daytona MVP proof.",
        instructions="Follow the host-provided live proof scenario exactly.",
        capability_refs=(_CAPABILITY_ID,),
        task_contract_ref=_CAPABILITY_ID,
    )

    async def select_live_skill(self: DSPySkillSelector, **kwargs: Any) -> SkillSelection:
        del self, kwargs
        return SkillSelection(selected_skill_ids=(skill.id,), primary_skill_id=skill.id)

    monkeypatch.setattr(DSPySkillSelector, "select", select_live_skill)
    secret_values = tuple(
        value
        for secret in (settings.daytona_api_key, settings.llm_api_key)
        if secret is not None
        for value in (secret.get_secret_value(),)
        if value
    )

    try:
        with TestClient(app) as client:
            resources = app.state.run_environment_resources
            portal = client.portal
            assert portal is not None
            try:
                phase = "first_turn"
                created = client.post("/api/sessions", json={"title": "Live Daytona MVP proof"})
                assert created.status_code == 201
                session_id = UUID(created.json()["id"])

                first = client.post(
                    f"/api/sessions/{session_id}/turns",
                    json={"text": "FIRST: execute the complete recursive Daytona MVP proof."},
                    headers={"Idempotency-Key": f"live-mvp-first-{uuid4()}"},
                )
                assert first.status_code == 200
                first_run_id = UUID(first.headers["x-fleet-run-id"])
                first_chunks, first_done = _sse_chunks(first)
                assert first_done == 1
                assert sum(chunk["type"] == "start" for chunk in first_chunks) == 1
                assert sum(chunk["type"] == "finish" for chunk in first_chunks) == 1
                assert first_chunks[-1]["type"] == "finish"
                assert first_chunks[-1]["finishReason"] == "stop"
                code_chunks = [chunk for chunk in first_chunks if chunk["type"] == "data-rlm-code"]
                assert len(code_chunks) >= 3
                generated_code = [str(chunk["data"]["code"]) for chunk in code_chunks]
                assert "issue_iteration_token" in generated_code[0]
                assert re.search(r"\baccumulator\s*=", generated_code[0])
                semantic_step = next(code for code in generated_code[1:] if "llm_query_batched" in code)
                assert re.search(r"\bllm_query\s*\(", semantic_step)
                assert "verify_semantic_work" in semantic_step
                assert not re.search(r"\baccumulator\s*=", semantic_step)
                assert re.search(r"\bSUBMIT\s*\(", generated_code[-1])
                tool_names = [chunk["toolName"] for chunk in first_chunks if chunk["type"] == "tool-input-available"]
                assert tool_names.count("issue_iteration_token") == 1
                assert tool_names.count("verify_semantic_work") == 1
                assert "write_workspace_text" in tool_names
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
                    json={"text": "SECOND: verify fresh interpreter state and durable workspace reload."},
                    headers={"Idempotency-Key": f"live-mvp-second-{uuid4()}"},
                )
                assert second.status_code == 200
                second_run_id = UUID(second.headers["x-fleet-run-id"])
                second_chunks, second_done = _sse_chunks(second)
                assert second_done == 1
                assert sum(chunk["type"] == "start" for chunk in second_chunks) == 1
                assert sum(chunk["type"] == "finish" for chunk in second_chunks) == 1
                assert second_chunks[-1]["type"] == "finish"
                assert second_chunks[-1]["finishReason"] == "stop"
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
