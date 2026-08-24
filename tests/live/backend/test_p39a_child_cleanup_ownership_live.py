"""Live VAL-RLM-063 evidence: recursive child cleanup ownership is complete and fail-closed.

Serial, credentialed, FLEET_LIVE=1-only proof against the real Daytona
provider using deterministic scripted LMs (protocol-stable; the live-model
exploratory scenario is intentionally avoided per the mission AGENTS.md known
pre-existing issue note). Two scenarios run on one SHA:

- ``success``: one scripted recursive child turn completes; evidence records
  the ordered child close steps (strict interpreter/broker shutdown -> scope
  purge -> Sandbox delete -> confirmed absence -> admission restore), exactly
  one close per lease (no double-close), zero leaked child Sandboxes, and
  admission restored to baseline.
- ``fault_injected``: the child's absence confirmation is forced to fail;
  evidence records that the close still fails closed with an explicit typed
  cleanup-failure classification, that every remaining cleanup step was still
  attempted in order (delete request + admission restore), that no clean
  success receipt is emitted (terminal is failed, never completed), and that
  ownership still settles without leaking the Sandbox or its permit.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.config import Settings
from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.daytona.sandbox_lease import SandboxLeaseReceipt
from fleet_rlm.daytona.session_manager import get_active_lease_registry
from fleet_rlm.rlm.child_runtime import ChildRuntimeCleanupError
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from tests.live.backend._database import upgrade_to_head
from tests.live.backend._p35d_evidence import candidate_identity
from tests.live.backend.test_fleet_rlm_daytona_mvp import _live_settings
from tests.live.backend.test_phase1_daytona_stream import _strict_cleanup

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(900)]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RECEIPT_SCHEMA = "fleet.p39a-child-cleanup-ownership-live/v1"
_EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"


class _ChildScriptedLM(dspy.utils.DummyLM):
    def __init__(self) -> None:
        super().__init__(
            [{"reasoning": "complete bounded child", "code": "SUBMIT(answer='child-cleanup-ok')"}],
            adapter=dspy.JSONAdapter(),
        )


class _RootScriptedLM(dspy.utils.DummyLM):
    def __init__(self) -> None:
        super().__init__(
            [
                {"reasoning": "delegate exactly once", "code": "child = rlm_query(prompt='bounded child task')"},
                {"reasoning": "complete bounded root", "code": "SUBMIT(answer='root-cleanup-ok')"},
            ],
            adapter=dspy.JSONAdapter(),
        )

    def copy(self, **kwargs: Any) -> _ChildScriptedLM:
        del kwargs
        return _ChildScriptedLM()


def _case_settings(tmp_path: Path, *, name: str) -> Settings:
    settings = _live_settings(tmp_path).model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{(tmp_path / f'{name}.db').resolve()}",
            "volume_name": f"fleet-rlm-p39a-child-ownership-{name}-{uuid4()}",
            "rlm_recursion_enabled": True,
            "rlm_recursion_max_calls": 1,
            "rlm_recursion_max_prompt_chars": 2_000,
            "rlm_recursion_child_max_iters": 2,
            "rlm_recursion_child_max_llm_calls": 2,
            "rlm_max_iters": 3,
            "rlm_max_llm_calls": 4,
            "turn_timeout_seconds": 840,
            "run_stale_after_seconds": 600,
            "mlflow_tracing_enabled": False,
        }
    )
    upgrade_to_head(settings.database_url or "")
    return settings


class _ChildEvidence:
    """Ordered child-close evidence for one scenario."""

    def __init__(self) -> None:
        self.child_sandbox_ids: list[str] = []
        self.close_attempts: list[str] = []
        self.shutdown_order: list[str] = []
        self.receipts: list[dict[str, object]] = []
        self.cleanup_errors: list[str] = []


def _receipt_projection(receipt: SandboxLeaseReceipt) -> dict[str, object]:
    return {
        "sandbox_id": receipt.sandbox_id,
        "provider_action": receipt.provider.action,
        "provider_requested": receipt.provider.requested,
        "provider_confirmed_absent": receipt.provider.confirmed_absent,
        "provider_observations": list(receipt.provider.plateau),
        "admission_held": receipt.admission.held,
        "admission_released": receipt.admission.released,
        "admission_released_after": receipt.admission.released_after,
        "quarantined": receipt.quarantine.quarantined,
        "clean": receipt.clean,
        "first_error": receipt.first_error,
    }


def _install_child_evidence(monkeypatch: pytest.MonkeyPatch, evidence: _ChildEvidence) -> None:
    """Observe child acquisition, lease close, interpreter shutdown, and cleanup receipts."""
    original_acquire = recursive_child_runtime._acquire_child_runtime
    original_lease = recursive_child_runtime.SandboxLease

    async def observed_acquire(**kwargs: Any) -> Any:
        lease = await original_acquire(**kwargs)
        evidence.child_sandbox_ids.append(lease.sandbox_id)
        original_close = lease._close

        def observed_close() -> None:
            evidence.close_attempts.append(lease.sandbox_id)
            original_close()

        lease._close = observed_close

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

    monkeypatch.setattr(recursive_child_runtime, "_acquire_child_runtime", observed_acquire)
    monkeypatch.setattr(recursive_child_runtime, "SandboxLease", RecordingLease)


def _install_failing_absence_confirmation(monkeypatch: pytest.MonkeyPatch, evidence: _ChildEvidence) -> None:
    """Force the child's provider absence confirmation to fail (fault injection).

    The delete request and admission restore must still run, and the close
    must fail closed with an explicit typed classification instead of a clean
    success receipt.
    """
    original_cleanup = recursive_child_runtime.cleanup_child_runtime_async

    async def failing_cleanup(**kwargs: Any) -> None:
        async def failing_confirm(**confirm_kwargs: Any) -> Any:
            from fleet_rlm.daytona.lifecycle import AbsenceProbeError

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

    monkeypatch.setattr(recursive_child_runtime, "cleanup_child_runtime_async", failing_cleanup)


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


def _wait_for_admission_baseline(resources: Any, session_id: UUID, permits: int) -> None:
    deadline = time.perf_counter() + 60
    while time.perf_counter() < deadline:
        if (
            resources.daytona_admission._semaphore._value == permits
            and get_active_lease_registry().holder(session_id) is None
        ):
            return
        time.sleep(0.25)
    assert resources.daytona_admission._semaphore._value == permits
    assert get_active_lease_registry().holder(session_id) is None


def _child_sandbox_absent(client: TestClient, resources: Any, sandbox_id: str) -> bool:
    """Poll the provider until the child Sandbox is confirmed absent (bounded)."""

    async def poll() -> bool:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            sandbox = await resources.platform.get(sandbox_id)
            if sandbox is None:
                return True
            state = str(getattr(getattr(sandbox, "state", None), "value", getattr(sandbox, "state", None)) or "")
            if state.strip().lower() in {"destroyed", "deleted"}:
                return True
            await asyncio.sleep(1.0)
        return await resources.platform.get(sandbox_id) is None

    assert client.portal is not None
    return client.portal.call(poll)


def _write_receipt(scenario: str, payload: dict[str, object]) -> None:
    configured = os.environ.get(_EVIDENCE_ENV)
    if configured:
        base = Path(configured).expanduser().resolve()
        path = base.with_name(f"{base.stem}-p39a-child-{scenario}{base.suffix or '.json'}")
    else:
        path = _REPO_ROOT / ".fleet-evidence" / "receipts" / f"p39a-child-cleanup-ownership-live-{scenario}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_live_child_cleanup_success_records_ordered_complete_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-RLM-063 success scenario: ordered, complete, exactly-once child cleanup."""
    settings = _case_settings(tmp_path, name="success")
    evidence = _ChildEvidence()
    _install_child_evidence(monkeypatch, evidence)
    cleanup_failures: tuple[str, ...] = ()
    app = create_app(settings=settings)
    session_id: UUID | None = None
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        preparation = inventory.run_preparation
        assert resources is not None
        assert preparation is not None
        preparation._models = RLMModelBundle(_RootScriptedLM(), dspy.utils.DummyLM([{"answer": "unused"}]))
        try:
            created = client.post("/api/sessions", json={"title": "P39a child cleanup success canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Run exactly one recursive child and return the bounded root answer."},
                headers={"Idempotency-Key": f"p39a-child-ownership-success-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            assert chunks[-1].get("type") == "finish"
            assert chunks[-1].get("finishReason") == "stop"
            completion = next(
                (
                    chunk.get("output")
                    for chunk in chunks
                    if chunk.get("type") == "tool-output-available"
                    and isinstance(chunk.get("output"), dict)
                    and chunk["output"].get("status") == "completed"
                    and chunk["output"].get("recursive_depth") == 1
                ),
                None,
            )
            assert completion is not None
            assert completion["termination_mode"] == "typed_submit"

            # Child cleanup evidence: one child, exactly one close attempt,
            # ordered strict shutdown, one complete receipt with confirmed
            # absence and admission restored after confirmed cleanup.
            assert len(evidence.child_sandbox_ids) == 1
            assert evidence.close_attempts == evidence.child_sandbox_ids
            assert evidence.shutdown_order == [
                f"interpreter_shutdown:{evidence.child_sandbox_ids[0]}:strict_broker_cleanup=True"
            ]
            assert len(evidence.receipts) == 1
            receipt = evidence.receipts[0]
            assert receipt["sandbox_id"] == evidence.child_sandbox_ids[0]
            assert receipt["provider_action"] == "delete"
            assert receipt["provider_requested"] is True
            assert receipt["provider_confirmed_absent"] is True
            assert receipt["admission_released"] is True
            assert receipt["admission_released_after"] == "confirmed_cleanup"
            assert receipt["clean"] is True
            assert receipt["first_error"] is None

            # Admission restored to baseline; no leaked lease.
            _wait_for_admission_baseline(resources, session_id, permits=settings.max_active_daytona_leases)
            # Provider-side confirmed absence of the child Sandbox (re-probed).
            assert _child_sandbox_absent(client, resources, evidence.child_sandbox_ids[0])
        finally:
            assert client.portal is not None
            cleanup_failures = client.portal.call(_strict_cleanup, resources, settings.volume_name)
    assert cleanup_failures == ()
    _write_receipt(
        "success",
        {
            "schema": _RECEIPT_SCHEMA,
            "candidate": candidate_identity(),
            "scenario": "success",
            "evidence": {
                "child_sandbox_ids": evidence.child_sandbox_ids,
                "close_attempts": len(evidence.close_attempts),
                "ordered_steps": evidence.shutdown_order,
                "cleanup_receipt": evidence.receipts,
            },
            "assertions": {
                "ordered_cleanup_steps": True,
                "zero_double_close": True,
                "no_detached_child": True,
                "sandbox_absent": True,
                "admission_restored": True,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        },
    )


def test_live_child_cleanup_failure_fails_closed_with_explicit_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-RLM-063 fault scenario: forced absence-confirmation failure fails the
    Turn closed while every remaining cleanup step still runs and nothing leaks."""
    settings = _case_settings(tmp_path, name="fault")
    evidence = _ChildEvidence()
    _install_child_evidence(monkeypatch, evidence)
    _install_failing_absence_confirmation(monkeypatch, evidence)
    cleanup_failures: tuple[str, ...] = ()
    app = create_app(settings=settings)
    session_id: UUID | None = None
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        preparation = inventory.run_preparation
        assert resources is not None
        assert preparation is not None
        preparation._models = RLMModelBundle(_RootScriptedLM(), dspy.utils.DummyLM([{"answer": "unused"}]))
        try:
            created = client.post("/api/sessions", json={"title": "P39a child cleanup fault canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Run exactly one recursive child and return the bounded root answer."},
                headers={"Idempotency-Key": f"p39a-child-ownership-fault-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            # No clean success receipt: the terminal chunk is not a stop, no
            # typed structured result was committed, and the recursive child
            # never completed.
            last = chunks[-1]
            assert not (last.get("type") == "finish" and last.get("finishReason") == "stop")
            assert not any(chunk.get("type") == "data-structured-result" for chunk in chunks)
            assert not any(
                chunk.get("type") == "tool-output-available"
                and isinstance(chunk.get("output"), dict)
                and chunk["output"].get("status") == "completed"
                and chunk["output"].get("recursive_depth") == 1
                for chunk in chunks
            )

            # Fault evidence: strict interpreter/broker shutdown ran first,
            # the delete request and absence confirmation were still attempted,
            # admission was restored through the explicit quarantine-failure
            # lane, and the close failed with a typed cleanup classification.
            assert len(evidence.child_sandbox_ids) == 1
            assert evidence.close_attempts == evidence.child_sandbox_ids
            assert evidence.shutdown_order == [
                f"interpreter_shutdown:{evidence.child_sandbox_ids[0]}:strict_broker_cleanup=True"
            ]
            assert len(evidence.cleanup_errors) == 1
            assert "cleanup failed" in evidence.cleanup_errors[0]
            assert len(evidence.receipts) == 1
            receipt = evidence.receipts[0]
            assert receipt["sandbox_id"] == evidence.child_sandbox_ids[0]
            assert receipt["provider_action"] == "delete"
            assert receipt["provider_requested"] is True
            assert receipt["provider_confirmed_absent"] is False
            assert receipt["admission_released"] is True
            assert receipt["admission_released_after"] == "quarantine_failure"
            assert receipt["clean"] is False
            assert receipt["first_error"] is not None

            # Admission still restored; the failed cleanup never leaked the permit.
            _wait_for_admission_baseline(resources, session_id, permits=settings.max_active_daytona_leases)
            # The delete request did run provider-side: the child Sandbox is
            # absent despite the faulted confirmation (ownership settles).
            assert _child_sandbox_absent(client, resources, evidence.child_sandbox_ids[0])
        finally:
            assert client.portal is not None
            cleanup_failures = client.portal.call(_strict_cleanup, resources, settings.volume_name)
    assert cleanup_failures == ()
    _write_receipt(
        "fault",
        {
            "schema": _RECEIPT_SCHEMA,
            "candidate": candidate_identity(),
            "scenario": "fault-injected-absence-confirmation",
            "evidence": {
                "child_sandbox_ids": evidence.child_sandbox_ids,
                "close_attempts": len(evidence.close_attempts),
                "ordered_steps": evidence.shutdown_order,
                "cleanup_receipt": evidence.receipts,
                "cleanup_error_classification": evidence.cleanup_errors,
            },
            "assertions": {
                "ordered_cleanup_steps_despite_failure": True,
                "zero_double_close": True,
                "no_detached_child": True,
                "sandbox_absent_after_forced_confirmation_failure": True,
                "admission_restored_after_forced_confirmation_failure": True,
                "explicit_failed_cleanup_classification": True,
                "no_clean_success_receipt": True,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        },
    )
