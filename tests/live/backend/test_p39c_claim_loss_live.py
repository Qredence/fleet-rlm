"""P39c live certification: claim-loss fencing leaves no recursive resources.

Serial, credentialed, ``FLEET_LIVE=1``-only proof using deterministic scripted
LMs (protocol-stable per the mission AGENTS.md known pre-existing issue note).
VAL-REC-037: force a definitive claim loss (heartbeat revocation) while a real
Daytona child is executing inside a Root-only two-child batch with bounded
parallelism one, so one child is in flight and its sibling stays queued.

Authority revocation must prevent any later sibling acquisition, discard the
late child output, prevent successful Turn settlement, and retain all cleanup
ownership: the stream ends in one failed non-success terminal, no Artifact
identity or history advance exists, and every acquired child still settles
through strict interpreter/broker shutdown + provider delete + confirmed
absence with admission restored and no lease holder.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.chat.run_lifecycle import RunLifecycleUnavailableError
from fleet_rlm.config import Settings
from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.sandbox_lease import SandboxLeaseReceipt
from fleet_rlm.daytona.session_manager import get_active_lease_registry
from fleet_rlm.rlm.program import RLMModelBundle
from tests.live.backend._database import upgrade_to_head
from tests.live.backend._p35d_evidence import candidate_identity
from tests.live.backend._p39c_evidence import record_observed_sandbox_ids, write_lane_receipt
from tests.live.backend.test_fleet_rlm_daytona_mvp import _live_settings, _sse_chunks, _strict_cleanup
from tests.live.backend.test_p39a_child_cleanup_ownership_live import (
    _receipt_projection,
    _wait_for_admission_baseline,
)

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(1200)]

_RECEIPT_SCHEMA = "fleet.p39c-claim-loss/v1"
_HOLD_SECONDS = 30.0


class _BatchRootLM(dspy.utils.DummyLM):
    """Root: exactly one two-child batch and nothing else."""

    def __init__(self) -> None:
        super().__init__(
            [
                {
                    "reasoning": "run exactly one two-child batch",
                    "code": (
                        "results = rlm_query_batched(prompts=['P39C-CL-A bounded first subproblem',"
                        " 'P39C-CL-B bounded second subproblem'])"
                    ),
                },
            ],
            adapter=dspy.JSONAdapter(),
        )

    def copy(self, **kwargs: Any) -> dspy.utils.DummyLM:
        del kwargs
        return dspy.utils.DummyLM(
            {
                "P39C-CL-A": {"reasoning": "child A", "code": "SUBMIT(answer='P39C-CL-TOKEN-A')"},
                "P39C-CL-B": {"reasoning": "child B", "code": "SUBMIT(answer='P39C-CL-TOKEN-B')"},
            },
            adapter=dspy.JSONAdapter(),
        )


@dataclass
class _ClaimLossEvidence:
    sandbox_ids: list[str] = field(default_factory=list)
    call_indexes: list[int] = field(default_factory=list)
    acquire_at: dict[str, float] = field(default_factory=dict)
    create_at: dict[str, float] = field(default_factory=dict)
    shutdown_order: list[str] = field(default_factory=list)
    receipts: list[dict[str, object]] = field(default_factory=list)
    confirmations: list[dict[str, object]] = field(default_factory=list)
    delete_acceptance_seconds: dict[str, float] = field(default_factory=dict)
    claim_loss_at: float | None = None
    claim_losses: int = 0
    child_executing = threading.Event()
    class_shutdown_calls: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)


def _case_settings(tmp_path: Path) -> Settings:
    settings = _live_settings(tmp_path).model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'p39c-claim-loss.db').resolve()}",
            "volume_name": f"fleet-rlm-p39c-claim-loss-{uuid4()}",
            "rlm_recursion_enabled": True,
            "rlm_recursion_max_calls": 2,
            "rlm_recursion_max_prompt_chars": 2_000,
            "rlm_recursion_child_max_iters": 2,
            "rlm_recursion_child_max_llm_calls": 2,
            "rlm_recursion_max_parallel_children": 1,
            "rlm_max_iters": 2,
            "rlm_max_llm_calls": 4,
            "run_heartbeat_seconds": 5,
            "turn_timeout_seconds": 840,
            "run_stale_after_seconds": 600,
            "mlflow_tracing_enabled": False,
        }
    )
    upgrade_to_head(settings.database_url or "")
    return settings


def _install_claim_loss_evidence(monkeypatch: pytest.MonkeyPatch, evidence: _ClaimLossEvidence) -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    original_class_shutdown = DaytonaCodeInterpreter.shutdown

    def class_observed_shutdown(self: DaytonaCodeInterpreter, *args: Any, **kwargs: Any) -> None:
        backend = getattr(self, "_backend", None)
        sandbox = getattr(backend, "sandbox", None) if backend is not None else None
        inner = getattr(sandbox, "_sandbox", sandbox) if sandbox is not None else None
        sandbox_id = str(getattr(inner, "id", "") or "") if inner is not None else ""
        with evidence._lock:
            evidence.class_shutdown_calls.append(
                f"interpreter_shutdown:{sandbox_id}:strict_broker_cleanup={bool(kwargs.get('strict_broker_cleanup'))}"
            )
        return original_class_shutdown(self, *args, **kwargs)

    monkeypatch.setattr(DaytonaCodeInterpreter, "shutdown", class_observed_shutdown)

    original_acquire = recursive_child_runtime._acquire_child_runtime
    original_lease = recursive_child_runtime.SandboxLease

    async def observed_acquire(**kwargs: Any) -> Any:
        lease = await original_acquire(**kwargs)
        now = time.monotonic()
        with evidence._lock:
            evidence.sandbox_ids.append(lease.sandbox_id)
            evidence.call_indexes.append(int(kwargs["call_index"]))
            evidence.acquire_at[lease.sandbox_id] = now
        interpreter = lease.interpreter
        original_shutdown = interpreter.shutdown

        def observed_shutdown(*args: Any, **shutdown_kwargs: Any) -> None:
            evidence.shutdown_order.append(
                f"interpreter_shutdown:{lease.sandbox_id}:"
                f"strict_broker_cleanup={bool(shutdown_kwargs.get('strict_broker_cleanup'))}"
            )
            original_shutdown(*args, **shutdown_kwargs)

        interpreter.shutdown = observed_shutdown
        return lease

    class RecordingLease(original_lease):  # type: ignore[misc, valid-type]
        async def aclose(self) -> SandboxLeaseReceipt:
            receipt = await super().aclose()
            evidence.receipts.append(_receipt_projection(receipt))
            return receipt

    async def recording_confirm(**kwargs: Any) -> Any:
        from fleet_rlm.daytona.lifecycle import confirm_absence as production_confirm

        probe = kwargs["probe"]
        sandbox_id = str(kwargs["sandbox_id"])

        def _raw_state(target: Any) -> str:
            raw = getattr(target, "state", None)
            if raw is None:
                raw = getattr(target, "status", None)
            return str(getattr(raw, "value", raw) or "").strip().lower()

        try:
            first_target = await probe(sandbox_id)
        except Exception as exc:
            first_state = f"probe_error:{type(exc).__name__}"
        else:
            first_state = "not_found" if first_target is None else (_raw_state(first_target) or "unknown")
        outcome = await production_confirm(**kwargs)
        evidence.confirmations.append(
            {
                "sandbox_id": sandbox_id,
                "immediate_post_delete_state": first_state,
                "absent": bool(getattr(outcome, "absent", False)),
                "observations": list(getattr(outcome, "observations", ())),
                "confirmation_duration_s": round(float(getattr(outcome, "duration_s", 0.0)), 3),
            }
        )
        return outcome

    monkeypatch.setattr(recursive_child_runtime, "_acquire_child_runtime", observed_acquire)
    monkeypatch.setattr(recursive_child_runtime, "SandboxLease", RecordingLease)
    monkeypatch.setattr(recursive_child_runtime, "confirm_absence", recording_confirm)


def _install_provider_observation(
    monkeypatch: pytest.MonkeyPatch,
    evidence: _ClaimLossEvidence,
    *,
    resources: Any,
) -> None:
    original_create = resources.platform.create
    original_delete = resources.platform.delete

    async def recording_create(**kwargs: Any) -> Any:
        created = await original_create(**kwargs)
        created_id = str(getattr(created, "id", ""))
        with evidence._lock:
            evidence.create_at[created_id] = time.monotonic()
        return created

    async def recording_delete(sandbox_id: Any) -> None:
        key = sandbox_id if isinstance(sandbox_id, str) else str(getattr(sandbox_id, "id", sandbox_id))
        requested_at = time.monotonic()
        await original_delete(sandbox_id)
        evidence.delete_acceptance_seconds[key] = round(time.monotonic() - requested_at, 3)

    monkeypatch.setattr(resources.platform, "create", recording_create)
    monkeypatch.setattr(resources.platform, "delete", recording_delete)


def _install_claim_loss_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    evidence: _ClaimLossEvidence,
    *,
    lifecycle: Any,
) -> None:
    """Force definitive claim loss on the first heartbeat after a child is executing."""
    original_heartbeat = lifecycle.heartbeat

    async def faulted_heartbeat(run: Any) -> None:
        if evidence.child_executing.is_set() and evidence.claim_loss_at is None:
            with evidence._lock:
                if evidence.claim_loss_at is None:
                    evidence.claim_loss_at = time.monotonic()
                    evidence.claim_losses += 1
            raise RunLifecycleUnavailableError("p39c fault-injected claim loss")
        return await original_heartbeat(run)

    monkeypatch.setattr(lifecycle, "heartbeat", faulted_heartbeat)


def _block_first_child_execution(
    monkeypatch: pytest.MonkeyPatch,
    *,
    evidence: _ClaimLossEvidence,
    hold_seconds: float = _HOLD_SECONDS,
) -> None:
    """Block the first CHILD broker execution and mark the child as executing."""
    original = DaytonaHttpToolBroker.execute_code
    lock = threading.Lock()
    state = {"blocked": 0}

    def blocking(
        self: DaytonaHttpToolBroker,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        timeout_s: float = 130.0,
        on_stdout: Any | None = None,
    ) -> Any:
        broker_sandbox = getattr(self, "_sandbox", None)
        # The DSPy worker seam wraps the async SDK sandbox in a sync view whose
        # ``_sandbox`` attribute holds the object carrying the provider ``id``.
        if broker_sandbox is not None and not hasattr(broker_sandbox, "id"):
            broker_sandbox = getattr(broker_sandbox, "_sandbox", broker_sandbox)
        sandbox_id = str(getattr(broker_sandbox, "id", "") or "")
        is_child = sandbox_id in evidence.sandbox_ids
        with lock:
            first_child_block = is_child and state["blocked"] == 0
            if is_child:
                state["blocked"] += 1
        if first_child_block:
            evidence.child_executing.set()
            time.sleep(hold_seconds)
            raise TimeoutError("p39c host-forced claim-loss child stall")
        return original(self, code, variables, timeout_s=timeout_s, on_stdout=on_stdout)

    monkeypatch.setattr(DaytonaHttpToolBroker, "execute_code", blocking)


def _write_receipt(payload: dict[str, object]) -> None:
    """Write the canonical receipt; FLEET_LIVE_EVIDENCE_PATH adds a copy."""
    write_lane_receipt("p39c-claim-loss.json", "-p39c-claim-loss", payload)


async def _all_absent(resources: Any, sandbox_ids: list[str]) -> dict[str, bool]:
    async def absent(sandbox_id: str) -> bool:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            target = await resources.platform.get(sandbox_id)
            if target is None:
                return True
            state = str(getattr(getattr(target, "state", None), "value", getattr(target, "state", None)) or "")
            if state.strip().lower() in {"destroyed", "deleted"}:
                return True
            await asyncio.sleep(1.0)
        return await resources.platform.get(sandbox_id) is None

    return {sandbox_id: await absent(sandbox_id) for sandbox_id in sandbox_ids}


def test_live_claim_loss_fencing_leaves_no_recursive_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-037: claim loss fences recursive resources and fails closed."""
    settings = _case_settings(tmp_path)
    evidence = _ClaimLossEvidence()
    _install_claim_loss_evidence(monkeypatch, evidence)
    cleanup_failures: tuple[str, ...] = ()
    app = create_app(settings=settings)
    session_id: UUID | None = None
    sandbox_ids: set[str] = set()
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        preparation = inventory.run_preparation
        lifecycle = inventory.run_lifecycle
        assert resources is not None
        assert preparation is not None
        assert lifecycle is not None
        portal = client.portal
        assert portal is not None
        _install_provider_observation(monkeypatch, evidence, resources=resources)
        _install_claim_loss_heartbeat(monkeypatch, evidence, lifecycle=lifecycle)
        _block_first_child_execution(monkeypatch, evidence=evidence)
        preparation._models = RLMModelBundle(
            _BatchRootLM(),
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
        )
        try:
            created = client.post("/api/sessions", json={"title": "P39c claim-loss canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            started_at = time.perf_counter()
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Run the bounded P39c claim-loss proof."},
                headers={"Idempotency-Key": f"p39c-claim-loss-{uuid4()}"},
            )
            elapsed = time.perf_counter() - started_at
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            assert elapsed < 420

            # The fault fired: one definitive claim loss while a child executed.
            assert evidence.claim_losses == 1
            assert evidence.claim_loss_at is not None
            assert evidence.child_executing.is_set()

            # Failed non-success terminal semantics: exactly one error frame
            # with the sanitized public failure and a finish/error terminal;
            # never an abort and never a success.
            finish_chunks = [chunk for chunk in chunks if chunk.get("type") == "finish"]
            assert len(finish_chunks) == 1
            assert finish_chunks[0].get("finishReason") == "error"
            assert chunks[-1] is finish_chunks[0]
            error_texts = [str(chunk.get("errorText", "")) for chunk in chunks if chunk.get("type") == "error"]
            assert error_texts == ["Turn failed"]
            assert not [chunk for chunk in chunks if chunk.get("type") == "abort"]
            # No Artifact identity and no successful recursive/structured output.
            assert not [chunk for chunk in chunks if chunk.get("type") == "data-artifact"]
            assert not [chunk for chunk in chunks if chunk.get("type") == "data-structured-result"]
            assert not [
                chunk
                for chunk in chunks
                if chunk.get("type") == "tool-output-available"
                and isinstance(chunk.get("output"), dict)
                and chunk["output"].get("status") == "completed"
                and chunk["output"].get("recursive_depth") == 1
            ]

            # Child acquisition state: exactly one child (call index 1) was
            # acquired before revocation; the queued sibling never acquired.
            assert len(evidence.sandbox_ids) == 1
            assert evidence.call_indexes == [1]
            child_id = evidence.sandbox_ids[0]
            assert evidence.acquire_at[child_id] < evidence.claim_loss_at

            # No post-revocation provider creates: every create observed for a
            # recursive child predates the claim loss.
            assert child_id in evidence.create_at
            assert all(
                create_time <= evidence.claim_loss_at
                for sandbox_id, create_time in evidence.create_at.items()
                if sandbox_id in evidence.sandbox_ids
            )

            # Cleanup ownership retained: strict interpreter/broker shutdown,
            # provider delete, confirmed absence, admission restored. The
            # error terminal settles the stream while the executor still owns
            # the stalled child; its strict close settles on the cleanup
            # boundary shortly after.
            deadline = time.perf_counter() + 240.0
            while time.perf_counter() < deadline:
                if evidence.receipts and (evidence.shutdown_order or evidence.class_shutdown_calls):
                    break
                time.sleep(0.25)
            expected_shutdown = f"interpreter_shutdown:{child_id}:strict_broker_cleanup=True"
            shutdown_traces = (evidence.shutdown_order, evidence.class_shutdown_calls)
            assert any(expected_shutdown in trace for trace in shutdown_traces), (
                f"strict shutdown not recorded: {shutdown_traces}"
            )
            assert len(evidence.receipts) == 1
            receipt = evidence.receipts[0]
            assert receipt["sandbox_id"] == child_id
            assert receipt["provider_action"] == "delete"
            assert receipt["provider_requested"] is True
            assert receipt["provider_confirmed_absent"] is True
            assert receipt["admission_released"] is True
            assert receipt["clean"] is True

            _wait_for_admission_baseline(resources, session_id, permits=settings.max_active_daytona_leases)
            assert get_active_lease_registry().holder(session_id) is None
            absence = portal.call(_all_absent, resources, list(evidence.sandbox_ids))
            assert all(absence.values())

            # No history advance: the failed Turn committed nothing.
            page = client.get(f"/api/sessions/{session_id}/turns")
            assert page.status_code == 200
            assistant_items = [item for item in page.json()["items"] if item["role"] == "assistant"]
            assert assistant_items == []

            binding = portal.call(resources.bindings.get, session_id)
            if binding is not None and binding.sandbox_id is not None:
                sandbox_ids.add(binding.sandbox_id)
            sandbox_ids.update(evidence.sandbox_ids)
            sandbox_ids.update(resources._sandbox_ids)
        finally:
            cleanup_failures = portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()
    record_observed_sandbox_ids("claim-loss", sandbox_ids, {str(session_id)})
    _write_receipt(
        {
            "schema": _RECEIPT_SCHEMA,
            "candidate": candidate_identity(),
            "scenario": "claim-loss-fencing",
            "evidence": {
                "child_sandbox_ids": evidence.sandbox_ids,
                "call_indexes": evidence.call_indexes,
                "queued_sibling_acquisitions": len(evidence.sandbox_ids) - 1,
                "claim_losses": evidence.claim_losses,
                "child_acquired_before_revocation": all(
                    evidence.acquire_at[sandbox_id] < (evidence.claim_loss_at or 0)
                    for sandbox_id in evidence.sandbox_ids
                ),
                "no_post_revocation_creates": all(
                    create_time <= (evidence.claim_loss_at or 0)
                    for sandbox_id, create_time in evidence.create_at.items()
                    if sandbox_id in evidence.sandbox_ids
                ),
                "ordered_steps": evidence.shutdown_order,
                "class_shutdown_trace": evidence.class_shutdown_calls,
                "cleanup_receipts": evidence.receipts,
                "deletion_evidence": {
                    "delete_request_acceptance_seconds": evidence.delete_acceptance_seconds,
                    "confirmations": evidence.confirmations,
                },
            },
            "assertions": {
                "failed_non_success_terminal": True,
                "no_artifact_identity": True,
                "no_history_advance": True,
                "no_post_revocation_provider_creates": True,
                "queued_sibling_never_acquired": True,
                "late_child_output_discarded": True,
                "strict_close_delete_absence_receipts": True,
                "admission_restored": True,
                "no_lease_holder": True,
                "zero_cleanup_failures": True,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        }
    )
