"""Live canary: Root ``rlm_query_batched`` with two simultaneous Daytona child RLMs."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field, replace
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
from fleet_rlm.config.loader import active_profile, require_live_execution
from fleet_rlm.config.settings import FleetConfigurationError, Settings
from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.daytona.session_manager import get_active_lease_registry
from fleet_rlm.rlm.events import ToolEventView
from fleet_rlm.rlm.program import has_llm_credentials
from fleet_rlm.rlm.recursion import RecursiveRLMExecutor
from tests.live.backend._database import upgrade_to_head
from tests.live.backend._p35d_evidence import candidate_identity, write_receipt
from tests.live.backend.test_phase1_daytona_stream import _strict_cleanup

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(960)]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LIVE_ROOT_MODEL = os.environ.get("FLEET_LIVE_ROOT_MODEL", "openai/zai-org/GLM-5.3-Flash")
_LIVE_SUB_MODEL = os.environ.get("FLEET_LIVE_SUB_MODEL", "openai/zai-org/GLM-5.3-Flash")
_CONTRACT_ID = "fleet.daytona-recursive-batch"
_TOKEN_A = "BATCH_TOKEN_ALPHA"
_TOKEN_B = "BATCH_TOKEN_BETA"
_LIVE_VALUES = frozenset({"1", "true", "yes"})


class BatchResult(dspy.Signature):
    """Return a multi-field Root result so completion is necessarily typed."""

    request: str = dspy.InputField()
    history: dspy.History = dspy.InputField()
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
        prepared = await self.delegate.prepare(turn, environment, attachments, deadline=deadline)
        prepared.spec = replace(
            prepared.spec,
            signature=BatchResult,
            output_schema_id=_CONTRACT_ID,
            output_schema_version="1",
            tools=(*prepared.spec.tools, *self.tools),
            tool_event_views={**prepared.spec.tool_event_views, **self.event_views},
        )
        return prepared


@dataclass(slots=True)
class _ProofLedger:
    calls: int = 0
    ordered: bool = False

    def verify_batch(self, results: list[str]) -> dict[str, bool]:
        self.calls += 1
        if self.calls != 1:
            raise ValueError("batch verifier must be called exactly once")
        if not isinstance(results, list) or len(results) != 2:
            raise ValueError("batch verifier requires exactly two ordered results")
        normalized = [str(item).strip() for item in results]
        self.ordered = normalized == [_TOKEN_A, _TOKEN_B]
        if not self.ordered:
            raise ValueError("batch results must preserve [prompt_a, prompt_b] order")
        return {"ok": True}


@dataclass(slots=True)
class _ChildEvidence:
    sandbox_ids: list[str] = field(default_factory=list)
    volume_subpaths: list[str] = field(default_factory=list)
    call_indexes: list[int] = field(default_factory=list)
    peak_observed: int = 0
    cleanups: int = 0
    _active: int = 0
    batch_answers: list[str] | None = None


def _live_enabled() -> bool:
    return os.environ.get("FLEET_LIVE", "").strip().lower() in _LIVE_VALUES


def _load_live_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    if not _live_enabled():
        pytest.skip("Set FLEET_LIVE=1 for the two-child rlm_query_batched canary")
    load_dotenv(_REPO_ROOT / ".env", override=False)
    import fleet_rlm.config.loader as configuration

    copied_policy = tmp_path / "batch-fleet.toml"
    target_profile = os.environ.get("FLEET_LIVE_PROFILE", "daytona-recursive")
    policy_source = (
        (_REPO_ROOT / "config" / "fleet.toml")
        .read_text(encoding="utf-8")
        .replace('default_profile = "daytona"', 'default_profile = "daytona-recursive"', 1)
    )
    if target_profile != "daytona-recursive":
        policy_source = policy_source.replace(
            'default_profile = "daytona-recursive"', f'default_profile = "{target_profile}"', 1
        )
    copied_policy.write_text(policy_source, encoding="utf-8")
    monkeypatch.setattr(configuration, "_CONFIG_PATH", copied_policy)
    try:
        policy = require_live_execution()
    except FleetConfigurationError:
        pytest.fail("Recursive batch canary requires runtime.live_enabled=true")
    target_profile = os.environ.get("FLEET_LIVE_PROFILE", "daytona-recursive")
    if active_profile(policy) != target_profile or policy.run_environment != "daytona":
        pytest.fail("Recursive batch canary requires the selected daytona profile")
    if not policy.rlm_recursion_enabled or (policy.root_model, policy.sub_model) != (
        _LIVE_ROOT_MODEL,
        _LIVE_SUB_MODEL,
    ):
        pytest.fail("Recursive batch canary requires the selected GLM recursive policy")
    if policy.rlm_recursion_max_parallel_children < 2:
        pytest.fail("Recursive batch canary requires recursion_max_parallel_children >= 2")
    if policy.daytona_api_key is None or not has_llm_credentials(policy):
        pytest.fail("Recursive batch canary is missing configured provider credentials")
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'recursive-batch.db').resolve()}"
    upgrade_to_head(database_url)
    return policy.model_copy(
        update={
            "database_url": database_url,
            "volume_name": f"fleet-rlm-recursive-batch-{uuid4()}",
            "rlm_max_iters": 8,
            "rlm_max_llm_calls": 14,
            "turn_timeout_seconds": 900,
            # Daytona Root + two child sandboxes can exceed the default 60s claim
            # stale window during preparation; match other live canaries.
            "run_stale_after_seconds": 600,
            "mlflow_tracing_enabled": False,
        }
    )


def _install_child_evidence(monkeypatch: pytest.MonkeyPatch, evidence: _ChildEvidence) -> None:
    original = recursive_child_runtime._acquire_child_runtime
    lock = threading.Lock()

    async def observed(**kwargs: object) -> recursive_child_runtime.ChildRuntimeLease:
        lease = await original(**kwargs)  # type: ignore[arg-type]
        with lock:
            evidence._active += 1
            evidence.peak_observed = max(evidence.peak_observed, evidence._active)
            evidence.sandbox_ids.append(lease.sandbox_id)
            evidence.volume_subpaths.append(lease.volume_subpath)
            evidence.call_indexes.append(int(kwargs["call_index"]))  # type: ignore[arg-type]
        close = lease._close

        def observed_close() -> None:
            try:
                close()
            except BaseException:
                with lock:
                    evidence._active = max(0, evidence._active - 1)
                raise
            else:
                with lock:
                    evidence._active = max(0, evidence._active - 1)
                    evidence.cleanups += 1

        lease._close = observed_close
        return lease

    monkeypatch.setattr(recursive_child_runtime, "_acquire_child_runtime", observed)


def _install_batch_answer_capture(monkeypatch: pytest.MonkeyPatch, evidence: _ChildEvidence) -> None:
    """Record host-side ``rlm_query_batched`` answers so Root cannot fake order via verify_batch alone."""
    original = RecursiveRLMExecutor._call_batched

    def observed(self: RecursiveRLMExecutor, prompts: list[str]) -> list[str]:
        answers = original(self, prompts)
        evidence.batch_answers = [str(item).strip() for item in answers]
        return answers

    monkeypatch.setattr(RecursiveRLMExecutor, "_call_batched", observed)


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


def _batch_completion(chunks: list[dict[str, Any]]) -> dict[str, object] | None:
    for chunk in chunks:
        if chunk.get("type") != "tool-output-available":
            continue
        output = chunk.get("output")
        if (
            isinstance(output, dict)
            and output.get("status") == "completed"
            and "answer_count" in output
            and "peak_child_concurrency" in output
        ):
            return output
    return None


def test_daytona_recursive_batch_two_children_through_fastapi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _load_live_settings(tmp_path, monkeypatch)
    ledger = _ProofLedger()
    child_evidence = _ChildEvidence()
    _install_child_evidence(monkeypatch, child_evidence)
    _install_batch_answer_capture(monkeypatch, child_evidence)
    proof_tool = dspy.Tool(
        ledger.verify_batch,
        name="verify_batch",
        desc="Verify ordered two-child batch answers exactly once.",
    )
    proof_views = MappingProxyType(
        {
            "verify_batch": ToolEventView(
                input_projection=lambda values: {"result_count": len(values.get("results") or ())},
                output_projection=lambda result: {"ok": bool(result.get("ok"))},
            )
        }
    )
    cleanup_failures: tuple[str, ...] = ()
    app = create_app(settings=settings)
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        preparation = inventory.run_preparation
        assert resources is not None
        assert preparation is not None
        preparation._capabilities = _ProofCapabilityPreparer(preparation._capabilities, (proof_tool,), proof_views)
        session_id: UUID | None = None
        try:
            created = client.post("/api/sessions", json={"title": "Daytona recursive batch canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            prompt_a = (
                f'In one iteration call typed SUBMIT(answer="{_TOKEN_A}"). Do not call rlm_query or rlm_query_batched.'
            )
            prompt_b = (
                f'In one iteration call typed SUBMIT(answer="{_TOKEN_B}"). Do not call rlm_query or rlm_query_batched.'
            )
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={
                    "text": (
                        "Execute the narrow native DSPy two-child batch proof. Run exactly one recursive"
                        " Daytona batch. First set prompt_a and prompt_b to the exact strings below, then"
                        " call results = rlm_query_batched(prompts=[prompt_a, prompt_b]) once."
                        f" prompt_a = {prompt_a!r}. prompt_b = {prompt_b!r}."
                        " Do not call rlm_query, do not call llm_query, and do not nest batching."
                        " After the batch returns, in a later iteration call"
                        " verify_batch(results=results) exactly once and require its ok result. Finally"
                        " issue exactly one typed SUBMIT with a brief completion answer and evidence='two-child"
                        " rlm_query_batched'. Do not retry and do not use extraction fallback."
                    ),
                },
                headers={"Idempotency-Key": f"daytona-recursive-batch-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            assert chunks[-1].get("type") == "finish"
            assert chunks[-1].get("finishReason") == "stop"
            batch = _batch_completion(chunks)
            assert batch is not None
            assert batch["answer_count"] == 2
            assert batch["peak_child_concurrency"] == 2
            assert ledger.calls == 1
            assert ledger.ordered
            assert child_evidence.batch_answers == [_TOKEN_A, _TOKEN_B]
            assert child_evidence.sandbox_ids == list(dict.fromkeys(child_evidence.sandbox_ids))
            assert len(child_evidence.sandbox_ids) == 2
            assert child_evidence.sandbox_ids[0] != child_evidence.sandbox_ids[1]
            assert len(child_evidence.volume_subpaths) == 2
            assert child_evidence.volume_subpaths[0] != child_evidence.volume_subpaths[1]
            assert sorted(child_evidence.call_indexes) == [1, 2]
            assert child_evidence.peak_observed == 2
            assert child_evidence.cleanups == 2
            assert child_evidence._active == 0
            runtime = getattr(resources, "runtime", None)
            close = getattr(runtime, "close_root_session", None)
            if callable(close):
                client.portal.call(lambda: close(LocalScope().workspace_id, session_id))
            assert resources.daytona_admission._semaphore._value == settings.max_active_daytona_leases
            assert get_active_lease_registry().holder(session_id) is None
            structured = [chunk for chunk in chunks if chunk.get("type") == "data-structured-result"]
            assert len(structured) == 1
            assert structured[0].get("data", {}).get("schema_id") == _CONTRACT_ID
            generated_code = "\n".join(
                str(chunk.get("data", {}).get("code", "")) for chunk in chunks if chunk.get("type") == "data-rlm-code"
            )
            assert "rlm_query_batched" in generated_code
        finally:
            assert client.portal is not None
            cleanup_failures = client.portal.call(_strict_cleanup, resources, settings.volume_name)
    assert cleanup_failures == ()
    write_receipt(
        {
            "schema": "fleet.p35d-root-batch/v1",
            "candidate": candidate_identity(),
            "assertions": {
                "ordered_root_batch": True,
                "native_child_count": 2,
                "peak_child_concurrency": child_evidence.peak_observed,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        }
    )
