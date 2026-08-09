"""One-Turn live proof for Phase 2 native DSPy recursion on Daytona."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.config import FleetConfigurationError, Settings, active_profile, require_live_execution
from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.rlm.lm_factory import has_llm_credentials
from fleet_rlm.rlm.tool_observer import ToolEventView
from tests.live.backend._database import upgrade_to_head
from tests.live.backend.test_phase1_daytona_stream import _strict_cleanup

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(960)]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RECEIPT_SCHEMA = "fleet.phase2-daytona-recursive/v1"
_EVIDENCE_ENV = "FLEET_PHASE2_RECURSIVE_EVIDENCE_PATH"
_LIVE_ROOT_MODEL = "deepseek-v4-flash"
_LIVE_SUB_MODEL = "deepseek-v4-flash"
_CONTRACT_ID = "fleet.phase2-daytona-recursive"


class Phase2Result(dspy.Signature):
    """Return a multi-field Root result so completion is necessarily typed."""

    request: str = dspy.InputField()
    session_context: dict = dspy.InputField()
    skill_cards: list[dict] = dspy.InputField()
    attachments: list[dict] = dspy.InputField()
    answer: str = dspy.OutputField()
    evidence: str = dspy.OutputField()


_SCENARIO = """
Run exactly one recursive Daytona proof. First set `root_marker = "root-only"`. Then make exactly one
`child_result = rlm_query(prompt=...)` call. The child prompt must tell the fresh child interpreter to determine whether
the Python name `root_marker` exists, return exactly `absent` when it does not, and use typed
`SUBMIT(answer="absent")`; do not call rlm_query inside the child. After return, assert that root_marker is still
`root-only` and child_result is exactly `absent`. Call verify_phase2 exactly once with those values and require
its `ok` result. Finally issue exactly one typed `SUBMIT(answer="phase2 complete", evidence="native recursive child")`.
Do not retry, do not call llm_query, and do not use extraction fallback.
""".strip()


@dataclass(slots=True)
class _ProofCapabilityPreparer:
    delegate: Any
    tools: tuple[dspy.Tool, ...]
    event_views: MappingProxyType[str, ToolEventView]

    async def prepare(self, turn: Any, environment: Any, attachments: Any, *, deadline: float) -> Any:
        """
        Prepare a turn with the Phase 2 recursive execution specification.
        
        Parameters:
            deadline (float): Maximum time allowed for preparation.
        
        Returns:
            Any: The prepared turn with the Phase 2 signature, contract metadata, tools, and event views.
        """
        prepared = await self.delegate.prepare(turn, environment, attachments, deadline=deadline)
        prepared.spec = replace(
            prepared.spec,
            signature=Phase2Result.with_instructions(_SCENARIO),
            output_schema_id=_CONTRACT_ID,
            output_schema_version="1",
            tools=(*prepared.spec.tools, *self.tools),
            tool_event_views={**prepared.spec.tool_event_views, **self.event_views},
        )
        return prepared


@dataclass(slots=True)
class _ProofLedger:
    calls: int = 0
    root_marker_absent_in_child: bool = False
    root_continuity: bool = False

    def verify_phase2(self, child_result: str, root_marker: str) -> dict[str, bool]:
        """
        Verify child isolation and root-state continuity for the Phase 2 recursion proof.
        
        Parameters:
        	child_result (str): Child-submitted value expected to be "absent".
        	root_marker (str): Root marker expected to be "root-only".
        
        Returns:
        	dict[str, bool]: A result containing `{"ok": True}` when both checks pass.
        
        Raises:
        	ValueError: If the verifier is called more than once or either check fails.
        """
        self.calls += 1
        if self.calls != 1:
            raise ValueError("phase2 verifier must be called exactly once")
        self.root_marker_absent_in_child = child_result.strip() == "absent"
        self.root_continuity = root_marker == "root-only"
        if not self.root_marker_absent_in_child or not self.root_continuity:
            raise ValueError("recursive child isolation proof failed")
        return {"ok": True}


@dataclass(slots=True)
class _ChildEvidence:
    created: int = 0
    same_volume_sibling_scope: bool = False
    cleanup_succeeded: bool = False
    child_duration_ms: int = 0
    started_at: float | None = None


def _load_live_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """
    Load and validate live settings for the Phase 2 recursive canary.
    
    Parameters:
    	tmp_path (Path): Temporary directory for the copied policy and SQLite database.
    	monkeypatch (pytest.MonkeyPatch): Pytest fixture used to apply the temporary configuration path.
    
    Returns:
    	Settings: Validated settings configured for the Daytona recursive canary.
    """
    if not os.environ.get(_EVIDENCE_ENV):
        pytest.skip("Run this credentialed canary via scripts/live_phase2_recursive_verify.py")
    load_dotenv(_REPO_ROOT / ".env", override=False)
    import fleet_rlm.config as configuration

    copied_policy = tmp_path / "phase2-fleet.toml"
    copied_policy.write_text(
        (_REPO_ROOT / "config" / "fleet.toml")
        .read_text(encoding="utf-8")
        .replace('default_profile = "daytona"', 'default_profile = "daytona-recursive"', 1),
        encoding="utf-8",
    )
    monkeypatch.setattr(configuration, "_CONFIG_PATH", copied_policy)
    try:
        policy = require_live_execution()
    except FleetConfigurationError:
        pytest.fail("Phase 2 recursive canary requires runtime.live_enabled=true")
    if active_profile(policy) != "daytona-recursive" or policy.run_environment != "daytona":
        pytest.fail("Phase 2 recursive canary requires the daytona-recursive profile")
    if not policy.rlm_recursion_enabled or (policy.root_model, policy.sub_model) != (
        _LIVE_ROOT_MODEL,
        _LIVE_SUB_MODEL,
    ):
        pytest.fail("Phase 2 recursive canary requires the selected DeepSeek recursive policy")
    if policy.daytona_api_key is None or not has_llm_credentials(policy):
        pytest.fail("Phase 2 recursive canary is missing configured provider credentials")
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'phase2-recursive.db').resolve()}"
    upgrade_to_head(database_url)
    return policy.model_copy(
        update={
            "database_url": database_url,
            "volume_name": f"fleet-rlm-phase2-recursive-{uuid4()}",
            "rlm_max_iterations": 7,
            "rlm_max_llm_calls": 10,
            "turn_timeout_seconds": 900,
        }
    )


def _install_child_evidence(monkeypatch: pytest.MonkeyPatch, evidence: _ChildEvidence) -> None:
    """Instrument child-runtime acquisition and cleanup to record evidence for the test."""
    original = recursive_child_runtime._acquire_child_runtime

    async def observed(**kwargs: object) -> recursive_child_runtime.ChildRuntimeLease:
        """
        Wrap child-runtime acquisition to record creation, recursive sibling scope, cleanup success, and duration.
        
        Parameters:
        	**kwargs (object): Child-runtime acquisition arguments, including workspace, run, call, and volume identifiers.
        
        Returns:
        	recursive_child_runtime.ChildRuntimeLease: The acquired child-runtime lease.
        """
        evidence.started_at = time.perf_counter()
        lease = await original(**kwargs)  # type: ignore[arg-type]
        evidence.created += 1
        expected_scope = f"recursive/{kwargs['workspace_id']}/{kwargs['run_id']}/{kwargs['call_index']}"
        evidence.same_volume_sibling_scope = (
            lease.volume_subpath == expected_scope and lease.volume_id == kwargs["volume_id"]
        )
        close = lease._close

        def observed_close() -> None:
            """Close the child runtime and record cleanup success and elapsed duration."""
            try:
                close()
            except Exception:
                raise
            else:
                evidence.cleanup_succeeded = True
            finally:
                if evidence.started_at is not None:
                    evidence.child_duration_ms = int((time.perf_counter() - evidence.started_at) * 1000)

        lease._close = observed_close
        return lease

    monkeypatch.setattr(recursive_child_runtime, "_acquire_child_runtime", observed)


def _sse_chunks(response: Any) -> tuple[list[dict[str, Any]], int]:
    """
    Parse Server-Sent Event data from a response.
    
    Parameters:
    	response (Any): Response whose text contains SSE lines.
    
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


def _recursive_completion(chunks: list[dict[str, Any]]) -> dict[str, object] | None:
    """Finds the completed recursive tool output in a sequence of event chunks.
    
    Parameters:
    	chunks (list[dict[str, Any]]): Event chunks to inspect.
    
    Returns:
    	dict[str, object] | None: The first completed recursive output, or `None` if no matching output is found.
    """
    for chunk in chunks:
        if chunk.get("type") != "tool-output-available":
            continue
        output = chunk.get("output")
        if isinstance(output, dict) and output.get("status") == "completed" and "recursive_depth" in output:
            return output
    return None


def _write_receipt(payload: dict[str, object]) -> None:
    """
    Write the evidence payload as formatted JSON to the configured output path.
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


def test_phase2_daytona_recursive_through_fastapi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Run the live Daytona recursive canary through the FastAPI application.
    
    Parameters:
    	tmp_path (Path): Temporary directory used to create the live test database.
    	monkeypatch (pytest.MonkeyPatch): Pytest fixture for patching runtime behavior and environment settings.
    """
    settings = _load_live_settings(tmp_path, monkeypatch)
    ledger = _ProofLedger()
    child_evidence = _ChildEvidence()
    _install_child_evidence(monkeypatch, child_evidence)
    proof_tool = dspy.Tool(
        ledger.verify_phase2,
        name="verify_phase2",
        desc="Verify child isolation and Root continuity exactly once.",
    )
    proof_views = MappingProxyType(
        {
            "verify_phase2": ToolEventView(
                input_projection=lambda _values: {"verification": "phase2"},
                output_projection=lambda result: {"ok": bool(result.get("ok"))},
            )
        }
    )
    started = time.perf_counter()
    pending_receipt: dict[str, object] | None = None
    cleanup_failures: tuple[str, ...] = ()
    app = create_app(settings=settings)
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        preparation = inventory.run_preparation
        assert resources is not None
        assert preparation is not None
        preparation._capabilities = _ProofCapabilityPreparer(preparation._capabilities, (proof_tool,), proof_views)
        try:
            created = client.post("/api/sessions", json={"title": "Phase 2 Daytona recursive canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Execute the narrow native DSPy Phase 2 recursive proof."},
                headers={"Idempotency-Key": f"phase2-daytona-recursive-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            assert chunks[-1].get("type") == "finish"
            assert chunks[-1].get("finishReason") == "stop"
            completion = _recursive_completion(chunks)
            assert completion is not None
            assert completion["status"] == "completed"
            assert completion["call_index"] == 1
            assert completion["recursive_depth"] == 1
            assert completion["termination_mode"] == "typed_submit"
            assert isinstance(completion["child_iterations"], int) and completion["child_iterations"] >= 1
            structured = [chunk for chunk in chunks if chunk.get("type") == "data-structured-result"]
            assert len(structured) == 1
            assert structured[0].get("data", {}).get("schema_id") == _CONTRACT_ID
            assert ledger.calls == 1
            assert child_evidence.created == 1
            assert child_evidence.same_volume_sibling_scope
            assert child_evidence.cleanup_succeeded
            pending_receipt = {
                "schema": _RECEIPT_SCHEMA,
                "timing": {
                    "turn_duration_ms": int((time.perf_counter() - started) * 1000),
                    "child_duration_ms": child_evidence.child_duration_ms,
                },
                "assertions": {
                    "dedicated_child_sandbox": True,
                    "same_volume_sibling_scope": True,
                    "root_marker_absent_in_child": ledger.root_marker_absent_in_child,
                    "root_continuity": ledger.root_continuity,
                    "child_typed_submit": completion["termination_mode"] == "typed_submit",
                    "root_typed_submit": True,
                    "strict_child_cleanup": child_evidence.cleanup_succeeded,
                    "terminal_ordering": True,
                    "no_grandchild_sandbox": child_evidence.created == 1,
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
