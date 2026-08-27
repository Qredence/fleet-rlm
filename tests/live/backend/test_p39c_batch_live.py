"""P39c live certification: Root-only two-child batch ordering, bounded
concurrency, and all-or-nothing settlement on the real provider.

Serial, credentialed, ``FLEET_LIVE=1``-only proof using deterministic scripted
LMs (protocol-stable per the mission AGENTS.md known pre-existing issue note).

- ``success`` (VAL-REC-035): exactly one ``rlm_query_batched`` with two
  prompts; both children acquire distinct Sandboxes and distinct sibling
  Volume scopes, host-side captured answers preserve input order, the batch
  settles with bounded concurrency, and every child resource is cleaned
  before Turn success (admission at baseline, no lease holder, provider-side
  absence re-confirmed for every observed child Sandbox id).
- ``failure`` (VAL-CROSS-005 all-or-nothing): one child's cleanup is forced
  to fail (fault-injected absence confirmation on call index 2). The batch
  cannot yield a successful parent result: the Turn ends in one sanitized
  terminal failure, there is no history advance, no Artifact identity, and
  no autonomous Memory promotion, while BOTH children still settle with
  provider delete + absence evidence and admission is fully restored.

Per the p39c orchestrator note, the SSE-side ``peak_child_concurrency`` metric
can transiently observe 1 under live provider timing even when both children
overlap; the host-side acquisition/release evidence below is the deterministic
overlap proof. If the SSE metric is 1 the lane records it as evidence-side
overlap proof (never silently weakens the deterministic side).
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

from fleet_rlm.api.local_scope import LocalScope
from fleet_rlm.app import create_app
from fleet_rlm.config import Settings
from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.daytona.sandbox_lease import SandboxLeaseReceipt
from fleet_rlm.daytona.session_manager import get_active_lease_registry
from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.rlm.recursion import ChildRuntimeCleanupError, RecursiveRLMExecutor
from tests.live.backend._database import upgrade_to_head
from tests.live.backend._p35d_evidence import candidate_identity
from tests.live.backend._p39c_evidence import record_observed_sandbox_ids, write_lane_receipt
from tests.live.backend.test_fleet_rlm_daytona_mvp import _live_settings, _sse_chunks, _strict_cleanup
from tests.live.backend.test_p39a_child_cleanup_ownership_live import (
    _receipt_projection,
    _wait_for_admission_baseline,
)

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(1800)]

_RECEIPT_SCHEMA = "fleet.p39c-batch/v1"
_TOKEN_A = "P39C-BATCH-ALPHA"
_TOKEN_B = "P39C-BATCH-BETA"


def _make_child_lm(token: str) -> dspy.utils.DummyLM:
    return dspy.utils.DummyLM(
        [{"reasoning": "bounded batch child", "code": f"SUBMIT(answer='{token}')"}],
        adapter=dspy.JSONAdapter(),
    )


class _BatchRootLM(dspy.utils.DummyLM):
    """Root: exactly one two-child batch, then typed SUBMIT of ordered answers."""

    def __init__(self) -> None:
        super().__init__(
            [
                {
                    "reasoning": "run exactly one two-child batch",
                    "code": (
                        "results = rlm_query_batched(prompts=['P39C-CHILD-A bounded first subproblem',"
                        " 'P39C-CHILD-B bounded second subproblem'])"
                    ),
                },
                {
                    "reasoning": "submit ordered batch answers",
                    "code": "SUBMIT(answer='P39C-BATCH-ROOT ' + '|'.join(results))",
                },
            ],
            adapter=dspy.JSONAdapter(),
        )

    def copy(self, **kwargs: Any) -> dspy.utils.DummyLM:
        del kwargs
        # Dispatch by the per-child prompt token; each child gets its own
        # scripted runtime so both answers are deterministic and ordered.
        return _ChildDispatchLM()


class _ChildDispatchLM(dspy.utils.DummyLM):
    def __init__(self) -> None:
        super().__init__(
            {
                "P39C-CHILD-A": {"reasoning": "child A", "code": f"SUBMIT(answer='{_TOKEN_A}')"},
                "P39C-CHILD-B": {"reasoning": "child B", "code": f"SUBMIT(answer='{_TOKEN_B}')"},
            },
            adapter=dspy.JSONAdapter(),
        )


class _FailureRootLM(dspy.utils.DummyLM):
    """Root for the all-or-nothing lane: one batch (child 2 cleanup fails).

    The script never proposes memory, never stages an artifact, and never
    reaches a typed SUBMIT: the batch tool raises, the Root exhausts its
    iterations, and the extraction fallback cannot produce a valid typed
    result. Any successful Turn here would be a violation.
    """

    def __init__(self) -> None:
        super().__init__(
            [
                {
                    "reasoning": "run the failing two-child batch",
                    "code": (
                        "results = rlm_query_batched(prompts=['P39C-CHILD-A bounded first subproblem',"
                        " 'P39C-CHILD-B bounded second subproblem'])"
                    ),
                },
                {
                    "reasoning": "blind retry that must also fail",
                    "code": "results = rlm_query_batched(prompts=['P39C-CHILD-A retry one', 'P39C-CHILD-B retry two'])",
                },
            ],
            adapter=dspy.JSONAdapter(),
        )

    def copy(self, **kwargs: Any) -> dspy.utils.DummyLM:
        del kwargs
        return _ChildDispatchLM()


@dataclass
class _BatchEvidence:
    sandbox_ids: list[str] = field(default_factory=list)
    volume_subpaths: list[str] = field(default_factory=list)
    call_indexes: list[int] = field(default_factory=list)
    close_attempts: dict[str, int] = field(default_factory=dict)
    receipts: list[dict[str, object]] = field(default_factory=list)
    confirmations: list[dict[str, object]] = field(default_factory=list)
    delete_acceptance_seconds: dict[str, float] = field(default_factory=dict)
    batch_answers: list[str] | None = None
    cleanup_errors: list[str] = field(default_factory=list)
    _active: int = 0
    _peak: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot_overlap(self) -> dict[str, object]:
        with self._lock:
            return {"peak_active_children": self._peak, "active_at_end": self._active}


def _case_settings(tmp_path: Path, *, name: str) -> Settings:
    settings = _live_settings(tmp_path).model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{(tmp_path / f'p39c-batch-{name}.db').resolve()}",
            "volume_name": f"fleet-rlm-p39c-batch-{name}-{uuid4()}",
            "rlm_recursion_enabled": True,
            "rlm_recursion_max_calls": 2,
            "rlm_recursion_max_prompt_chars": 2_000,
            "rlm_recursion_child_max_iters": 1,
            "rlm_recursion_child_max_llm_calls": 1,
            "rlm_recursion_max_parallel_children": 2,
            "rlm_max_iters": 2,
            "rlm_max_llm_calls": 4,
            "rlm_autonomous_memory_categories": ("operator preference",),
            "turn_timeout_seconds": 840,
            "run_stale_after_seconds": 600,
            "mlflow_tracing_enabled": False,
        }
    )
    upgrade_to_head(settings.database_url or "")
    return settings


def _install_batch_evidence(
    monkeypatch: pytest.MonkeyPatch,
    evidence: _BatchEvidence,
    *,
    resources: Any,
    fail_absence_for_call_index: int | None = None,
    barrier_wait_s: float | None = None,
) -> None:
    original_acquire = recursive_child_runtime._acquire_child_runtime
    original_lease = recursive_child_runtime.SandboxLease
    original_cleanup = recursive_child_runtime.cleanup_child_runtime_async
    original_platform_delete = resources.platform.delete

    async def observed_acquire(**kwargs: Any) -> Any:
        lease = await original_acquire(**kwargs)
        call_index = int(kwargs["call_index"])
        with evidence._lock:
            evidence._active += 1
            evidence._peak = max(evidence._peak, evidence._active)
        evidence.sandbox_ids.append(lease.sandbox_id)
        evidence.volume_subpaths.append(lease.volume_subpath)
        evidence.call_indexes.append(call_index)
        if barrier_wait_s is not None:
            # Deterministic bounded-concurrency proof: hold each acquired child
            # until every sibling acquisition is in flight (or the bounded
            # window expires), so the evidence-side overlap peak can never
            # transiently read 1 under live provider timing.
            barrier_deadline = time.monotonic() + barrier_wait_s
            while time.monotonic() < barrier_deadline:
                with evidence._lock:
                    if evidence._active >= 2:
                        break
                await asyncio.sleep(0.05)
        original_close = lease._close

        def observed_close() -> None:
            evidence.close_attempts[lease.sandbox_id] = evidence.close_attempts.get(lease.sandbox_id, 0) + 1
            try:
                original_close()
            finally:
                with evidence._lock:
                    evidence._active = max(0, evidence._active - 1)

        lease._close = observed_close
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

    async def faulted_cleanup(**kwargs: Any) -> None:
        """Force call index N's absence confirmation to fail (fault injection)."""
        sandbox_id = str(kwargs.get("sandbox_id", ""))
        index_for_sandbox = None
        if sandbox_id in evidence.sandbox_ids:
            index_for_sandbox = evidence.call_indexes[evidence.sandbox_ids.index(sandbox_id)]
        if index_for_sandbox == fail_absence_for_call_index:
            from fleet_rlm.daytona.lifecycle import AbsenceProbeError

            async def failing_confirm(**confirm_kwargs: Any) -> Any:
                return AbsenceProbeError(
                    sandbox_id=str(confirm_kwargs.get("sandbox_id")),
                    error="fault-injected absence confirmation failure",
                    observations=("fault_injected",),
                    duration_s=0.0,
                )

            kwargs["confirm"] = failing_confirm
        try:
            await original_cleanup(**kwargs)
        except ChildRuntimeCleanupError as exc:
            evidence.cleanup_errors.append(str(exc)[:240])
            raise

    async def recording_delete(sandbox_id: Any) -> None:
        key = sandbox_id if isinstance(sandbox_id, str) else str(getattr(sandbox_id, "id", sandbox_id))
        requested_at = time.monotonic()
        await original_platform_delete(sandbox_id)
        evidence.delete_acceptance_seconds[key] = round(time.monotonic() - requested_at, 3)

    monkeypatch.setattr(recursive_child_runtime, "_acquire_child_runtime", observed_acquire)
    monkeypatch.setattr(recursive_child_runtime, "SandboxLease", RecordingLease)
    monkeypatch.setattr(recursive_child_runtime, "confirm_absence", recording_confirm)
    monkeypatch.setattr(resources.platform, "delete", recording_delete)
    if fail_absence_for_call_index is not None:
        monkeypatch.setattr(recursive_child_runtime, "cleanup_child_runtime_async", faulted_cleanup)


def _install_batch_answer_capture(monkeypatch: pytest.MonkeyPatch, evidence: _BatchEvidence) -> None:
    """Host-side capture of the batch answers so order cannot be faked by Root."""
    original = RecursiveRLMExecutor._call_batched

    def observed(self: RecursiveRLMExecutor, prompts: list[str]) -> list[str]:
        answers = original(self, prompts)
        evidence.batch_answers = [str(item).strip() for item in answers]
        return answers

    monkeypatch.setattr(RecursiveRLMExecutor, "_call_batched", observed)


def _write_receipt(name: str, payload: dict[str, object]) -> None:
    """Write the canonical receipt; FLEET_LIVE_EVIDENCE_PATH adds a copy."""
    write_lane_receipt(f"p39c-batch-{name}.json", f"-p39c-batch-{name}", payload)


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


def test_live_batch_two_children_ordered_concurrent_leak_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-035: ordered, bounded-concurrency, leak-free two-child batch."""
    settings = _case_settings(tmp_path, name="success")
    evidence = _BatchEvidence()
    cleanup_failures: tuple[str, ...] = ()
    app = create_app(settings=settings)
    session_id: UUID | None = None
    sandbox_ids: set[str] = set()
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        preparation = inventory.run_preparation
        assert resources is not None
        assert preparation is not None
        _install_batch_evidence(monkeypatch, evidence, resources=resources, barrier_wait_s=45.0)
        _install_batch_answer_capture(monkeypatch, evidence)
        preparation._models = RLMModelBundle(
            _BatchRootLM(),
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
        )
        try:
            created = client.post("/api/sessions", json={"title": "P39c batch success canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Run the bounded P39c two-child batch proof."},
                headers={"Idempotency-Key": f"p39c-batch-success-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            assert chunks[-1].get("type") == "finish"
            assert chunks[-1].get("finishReason") == "stop"

            # Host-side captured answers preserve input order.
            assert evidence.batch_answers == [_TOKEN_A, _TOKEN_B]
            # Batch completion projection: two answers and the SSE peak metric.
            batch_output = next(
                chunk["output"]
                for chunk in chunks
                if chunk.get("type") == "tool-output-available"
                and isinstance(chunk.get("output"), dict)
                and "answer_count" in chunk["output"]
            )
            assert batch_output["answer_count"] == 2
            sse_peak = int(batch_output.get("peak_child_concurrency", 0))
            overlap = evidence.snapshot_overlap()
            # Deterministic evidence-side proof of two overlapping children.
            assert overlap["peak_active_children"] == 2
            assert overlap["active_at_end"] == 0
            # The SSE-side metric can transiently report 1 under live provider
            # timing (orchestrator note): it is recorded, and only when it is
            # 2 does the wire-side equality hold. It must never be 0 here.
            assert sse_peak in {1, 2}

            # Two unique Sandboxes and two distinct sibling Volume scopes,
            # call indexes reserved [1, 2].
            assert len(evidence.sandbox_ids) == 2
            assert evidence.sandbox_ids[0] != evidence.sandbox_ids[1]
            assert len(set(evidence.volume_subpaths)) == 2
            assert sorted(evidence.call_indexes) == [1, 2]
            run_id = str(next(chunk["messageId"] for chunk in chunks if chunk.get("type") == "start"))
            expected_scopes = {
                f"recursive/{LocalScope().workspace_id}/{run_id}/1",
                f"recursive/{LocalScope().workspace_id}/{run_id}/2",
            }
            assert set(evidence.volume_subpaths) == expected_scopes

            # Every child closed exactly once with a clean provider receipt.
            assert evidence.close_attempts == {sandbox_id: 1 for sandbox_id in evidence.sandbox_ids}
            assert len(evidence.receipts) == 2
            for receipt in evidence.receipts:
                assert receipt["provider_action"] == "delete"
                assert receipt["provider_requested"] is True
                assert receipt["provider_confirmed_absent"] is True
                assert receipt["admission_released"] is True
                assert receipt["admission_released_after"] == "confirmed_cleanup"
                assert receipt["clean"] is True
                assert receipt["first_error"] is None

            # VAL-REC-038 per-child deletion evidence: acceptance timing and
            # complete absence confirmations tied to exact Sandbox ids.
            assert set(evidence.delete_acceptance_seconds) == set(evidence.sandbox_ids)
            confirmed = {item["sandbox_id"]: item for item in evidence.confirmations}
            assert set(confirmed) == set(evidence.sandbox_ids)
            assert all(item["absent"] for item in evidence.confirmations)

            # Admission restored and no lease holder before Turn success settles.
            _wait_for_admission_baseline(resources, session_id, permits=settings.max_active_daytona_leases)
            assert get_active_lease_registry().holder(session_id) is None
            # Provider-side re-confirmed absence for every child Sandbox id.
            absence = client.portal.call(_all_absent, resources, list(evidence.sandbox_ids))
            assert all(absence.values())
            binding = client.portal.call(resources.bindings.get, session_id)
            assert binding is not None and binding.sandbox_id is not None
            sandbox_ids.add(binding.sandbox_id)
            sandbox_ids.update(evidence.sandbox_ids)
            sandbox_ids.update(resources._sandbox_ids)
        finally:
            assert client.portal is not None

            cleanup_failures = client.portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()
    record_observed_sandbox_ids("batch-success", sandbox_ids, {str(session_id)})
    _write_receipt(
        "success",
        {
            "schema": _RECEIPT_SCHEMA,
            "candidate": candidate_identity(),
            "scenario": "batch-success",
            "evidence": {
                "child_sandbox_ids": evidence.sandbox_ids,
                "volume_subpaths": evidence.volume_subpaths,
                "call_indexes": evidence.call_indexes,
                "batch_answers": evidence.batch_answers,
                "close_attempts": evidence.close_attempts,
                "cleanup_receipts": evidence.receipts,
                "deletion_evidence": {
                    "delete_request_acceptance_seconds": evidence.delete_acceptance_seconds,
                    "confirmations": evidence.confirmations,
                },
                "overlap": evidence.snapshot_overlap(),
                "sse_peak_child_concurrency": sse_peak,
            },
            "assertions": {
                "ordered_host_captured_answers": True,
                "two_unique_sandboxes": True,
                "two_distinct_sibling_scopes": True,
                "call_indexes_reserved": True,
                "evidence_side_peak_concurrency_two": True,
                "exactly_one_close_each": True,
                "all_receipts_clean": True,
                "sandbox_absent": True,
                "admission_restored": True,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        },
    )


def test_live_batch_child_cleanup_failure_is_all_or_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-CROSS-005: one failed child yields one sanitized terminal failure,
    no history advance, no Artifact identity, no autonomous Memory promotion,
    while both children still settle with deletion evidence and admission is
    restored."""
    settings = _case_settings(tmp_path, name="failure")
    evidence = _BatchEvidence()
    cleanup_failures: tuple[str, ...] = ()
    app = create_app(settings=settings)
    session_id: UUID | None = None
    sandbox_ids: set[str] = set()
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        preparation = inventory.run_preparation
        assert resources is not None
        assert preparation is not None
        _install_batch_evidence(
            monkeypatch,
            evidence,
            resources=resources,
            fail_absence_for_call_index=2,
        )
        _install_batch_answer_capture(monkeypatch, evidence)
        preparation._models = RLMModelBundle(
            _FailureRootLM(),
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
        )
        try:
            created = client.post("/api/sessions", json={"title": "P39c batch all-or-nothing canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Run the bounded P39c failing batch proof."},
                headers={"Idempotency-Key": f"p39c-batch-failure-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1

            # One sanitized terminal failure; never a successful stop.
            finish_chunks = [chunk for chunk in chunks if chunk.get("type") == "finish"]
            assert len(finish_chunks) == 1
            assert finish_chunks[0].get("finishReason") == "error"
            assert chunks[-1] is finish_chunks[0]
            assert not [chunk for chunk in chunks if chunk.get("type") == "abort"]
            error_texts = [str(chunk.get("errorText", "")) for chunk in chunks if chunk.get("type") == "error"]
            assert error_texts and all(
                text in {"Turn failed", "Turn output is too large", "Turn output is invalid"} for text in error_texts
            )
            # No Artifact identity and no structured success on the wire.
            assert not [chunk for chunk in chunks if chunk.get("type") == "data-artifact"]
            assert not [chunk for chunk in chunks if chunk.get("type") == "data-structured-result"]
            # No successful batch completion projection was emitted.
            assert not [
                chunk
                for chunk in chunks
                if chunk.get("type") == "tool-output-available"
                and isinstance(chunk.get("output"), dict)
                and "answer_count" in chunk["output"]
            ]
            # The failed batch never returned an answer list to the Root.
            assert evidence.batch_answers is None
            # The typed cleanup-failure classification was recorded.
            assert len(evidence.cleanup_errors) >= 1
            assert any("cleanup failed" in text for text in evidence.cleanup_errors)

            # Both children still settled: exactly one close each, provider
            # delete requested for both, admission restored after both closes,
            # and no lease holder remains.
            assert len(evidence.sandbox_ids) == 2
            assert evidence.sandbox_ids[0] != evidence.sandbox_ids[1]
            assert sorted(evidence.call_indexes) == [1, 2]
            assert evidence.close_attempts == {sandbox_id: 1 for sandbox_id in evidence.sandbox_ids}
            assert len(evidence.receipts) == 2
            receipts_by_id = {receipt["sandbox_id"]: receipt for receipt in evidence.receipts}
            child1_id = evidence.sandbox_ids[evidence.call_indexes.index(1)]
            child2_id = evidence.sandbox_ids[evidence.call_indexes.index(2)]
            assert receipts_by_id[child1_id]["clean"] is True
            assert receipts_by_id[child1_id]["provider_confirmed_absent"] is True
            assert receipts_by_id[child2_id]["clean"] is False
            assert receipts_by_id[child2_id]["provider_requested"] is True
            assert receipts_by_id[child2_id]["provider_confirmed_absent"] is False
            assert receipts_by_id[child2_id]["admission_released"] is True
            for receipt in evidence.receipts:
                assert receipt["provider_action"] == "delete"
                assert receipt["admission_released"] is True

            _wait_for_admission_baseline(resources, session_id, permits=settings.max_active_daytona_leases)
            assert get_active_lease_registry().holder(session_id) is None
            # Provider-side absence for every child despite the faulted
            # confirmation (ownership settles; the delete request did run).
            absence = client.portal.call(_all_absent, resources, list(evidence.sandbox_ids))
            assert all(absence.values())

            # No history advance: the failed Turn committed no assistant parts.
            page = client.get(f"/api/sessions/{session_id}/turns")
            assert page.status_code == 200
            assistant_items = [item for item in page.json()["items"] if item["role"] == "assistant"]
            assert assistant_items == []

            # No autonomous Memory promotion: the shared Volume's Workspace
            # Memory carries no promoted candidate record. Verified through one
            # validator-owned scratch Sandbox on the workspace scope (the Root
            # Sandbox may already be released after the failed Turn).
            from fleet_rlm.runtime.bindings import workspace_volume_subpath

            volume = client.portal.call(lambda: resources.volume_client.get(settings.volume_name, create=True))
            volume_id = str(getattr(volume, "id", "") or "")
            assert volume_id
            mount_path = settings.volume_mount_path or "/home/daytona/fleet"
            scratch = client.portal.call(
                lambda: resources.platform.create(
                    volume_id=volume_id,
                    mount_path=mount_path,
                    volume_subpath=workspace_volume_subpath(LocalScope().workspace_id),
                    ephemeral=True,
                    labels={"fleet.p39c": "batch-failure-memory-check"},
                )
            )
            scratch_id = str(getattr(scratch, "id", ""))
            sandbox_ids.add(scratch_id)

            async def _memory_content() -> str:
                try:
                    data = await scratch.fs.download_file(f"{mount_path}/memory/MEMORIES.md")
                except Exception:
                    return ""
                return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)

            content = client.portal.call(_memory_content)
            if content.strip():
                from fleet_rlm.workspace.models import parse_workspace_memory_lines

                lines = parse_workspace_memory_lines(content, complete_memory_graph=False)
                promoted = [
                    line
                    for line in lines
                    if line.entry is not None and getattr(line.entry, "source", None) == "agent_candidate"
                ]
                assert promoted == []
            binding = client.portal.call(resources.bindings.get, session_id)
            if binding is not None and binding.sandbox_id is not None:
                sandbox_ids.add(binding.sandbox_id)
            sandbox_ids.update(evidence.sandbox_ids)
            sandbox_ids.update(resources._sandbox_ids)
        finally:
            assert client.portal is not None
            cleanup_failures = client.portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()
    record_observed_sandbox_ids("batch-failure", sandbox_ids, {str(session_id)})
    _write_receipt(
        "failure",
        {
            "schema": _RECEIPT_SCHEMA,
            "candidate": candidate_identity(),
            "scenario": "batch-all-or-nothing",
            "evidence": {
                "child_sandbox_ids": evidence.sandbox_ids,
                "call_indexes": evidence.call_indexes,
                "close_attempts": evidence.close_attempts,
                "cleanup_receipts": evidence.receipts,
                "cleanup_errors": evidence.cleanup_errors,
                "delete_request_acceptance_seconds": evidence.delete_acceptance_seconds,
                "confirmations": evidence.confirmations,
                "batch_answers_returned": evidence.batch_answers,
                "overlap": evidence.snapshot_overlap(),
            },
            "assertions": {
                "one_sanitized_terminal_failure": True,
                "no_history_advance": True,
                "no_artifact_identity": True,
                "no_autonomous_memory_promotion": True,
                "no_partial_batch_answers": True,
                "both_children_deleted_with_ownership": True,
                "admission_restored": True,
                "sandbox_absent": True,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        },
    )
