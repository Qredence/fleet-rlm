"""P39c live certification: cancellation and deadline leave no recursive resources.

Serial, credentialed, ``FLEET_LIVE=1``-only proof using deterministic scripted
LMs (protocol-stable per the mission AGENTS.md known pre-existing issue note).
Both scenarios run a Root-only two-child ``rlm_query_batched`` with bounded
parallelism one, so one child is in flight in a real Daytona Sandbox while its
sibling stays queued (never acquired):

- ``cancel`` (VAL-REC-036): a supported cancellation request lands while the
  first child executes. The stream emits only cancelled semantics (one abort,
  no success), the queued sibling never acquires, no late child success is
  admitted, every acquired child settles through strict interpreter/broker
  shutdown + provider delete + confirmed absence, and admission/lease state
  return to baseline.
- ``deadline`` (VAL-REC-036): the Turn deadline fires while the first child
  executes. The stream emits only timeout/error semantics (no abort, no
  success), the queued sibling never acquires, and the same strict cleanup +
  absence + admission restoration evidence holds.
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
from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.sandbox_lease import SandboxLeaseReceipt
from fleet_rlm.daytona.session_manager import get_active_lease_registry
from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.sessions.models import TurnAccess
from tests.live.backend._database import upgrade_to_head
from tests.live.backend._p35d_evidence import candidate_identity
from tests.live.backend._p39c_evidence import record_observed_sandbox_ids, write_lane_receipt
from tests.live.backend.test_fleet_rlm_daytona_mvp import _live_settings, _sse_chunks, _strict_cleanup
from tests.live.backend.test_p39a_child_cleanup_ownership_live import (
    _receipt_projection,
    _wait_for_admission_baseline,
)

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(1200)]

_RECEIPT_SCHEMA = "fleet.p39c-cancel-deadline/v1"
_HOLD_SECONDS = 12.0


def _make_child_lm(token: str) -> dspy.utils.DummyLM:
    return dspy.utils.DummyLM(
        [{"reasoning": "bounded batch child", "code": f"SUBMIT(answer='{token}')"}],
        adapter=dspy.JSONAdapter(),
    )


class _BatchRootLM(dspy.utils.DummyLM):
    """Root: exactly one two-child batch and nothing else."""

    def __init__(self) -> None:
        super().__init__(
            [
                {
                    "reasoning": "run exactly one two-child batch",
                    "code": (
                        "results = rlm_query_batched(prompts=['P39C-CD-A bounded first subproblem',"
                        " 'P39C-CD-B bounded second subproblem'])"
                    ),
                },
            ],
            adapter=dspy.JSONAdapter(),
        )

    def copy(self, **kwargs: Any) -> dspy.utils.DummyLM:
        del kwargs
        return dspy.utils.DummyLM(
            {
                "P39C-CD-A": {"reasoning": "child A", "code": "SUBMIT(answer='P39C-CD-TOKEN-A')"},
                "P39C-CD-B": {"reasoning": "child B", "code": "SUBMIT(answer='P39C-CD-TOKEN-B')"},
            },
            adapter=dspy.JSONAdapter(),
        )


@dataclass
class _ScenarioEvidence:
    sandbox_ids: list[str] = field(default_factory=list)
    call_indexes: list[int] = field(default_factory=list)
    shutdown_order: list[str] = field(default_factory=list)
    receipts: list[dict[str, object]] = field(default_factory=list)
    confirmations: list[dict[str, object]] = field(default_factory=list)
    delete_acceptance_seconds: dict[str, float] = field(default_factory=dict)
    platform_creates: list[dict[str, object]] = field(default_factory=list)
    block_calls: int = 0
    cancel_state: str | None = None
    class_shutdown_calls: list[str] = field(default_factory=list)


def _case_settings(tmp_path: Path, *, name: str, turn_timeout_seconds: int) -> Settings:
    settings = _live_settings(tmp_path).model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{(tmp_path / f'p39c-cd-{name}.db').resolve()}",
            "volume_name": f"fleet-rlm-p39c-cd-{name}-{uuid4()}",
            "rlm_recursion_enabled": True,
            "rlm_recursion_max_calls": 2,
            "rlm_recursion_max_prompt_chars": 2_000,
            "rlm_recursion_child_max_iters": 2,
            "rlm_recursion_child_max_llm_calls": 2,
            "rlm_recursion_max_parallel_children": 1,
            "rlm_max_iters": 2,
            "rlm_max_llm_calls": 4,
            "turn_timeout_seconds": turn_timeout_seconds,
            "run_stale_after_seconds": 600,
            "mlflow_tracing_enabled": False,
        }
    )
    upgrade_to_head(settings.database_url or "")
    return settings


def _install_scenario_evidence(monkeypatch: pytest.MonkeyPatch, evidence: _ScenarioEvidence) -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    original_class_shutdown = DaytonaCodeInterpreter.shutdown

    def class_observed_shutdown(self: DaytonaCodeInterpreter, *args: Any, **kwargs: Any) -> None:
        backend = getattr(self, "_backend", None)
        sandbox = getattr(backend, "sandbox", None) if backend is not None else None
        inner = getattr(sandbox, "_sandbox", sandbox) if sandbox is not None else None
        sandbox_id = str(getattr(inner, "id", "") or "") if inner is not None else ""
        evidence.class_shutdown_calls.append(
            f"interpreter_shutdown:{sandbox_id}:strict_broker_cleanup={bool(kwargs.get('strict_broker_cleanup'))}"
        )
        return original_class_shutdown(self, *args, **kwargs)

    monkeypatch.setattr(DaytonaCodeInterpreter, "shutdown", class_observed_shutdown)

    original_acquire = recursive_child_runtime._acquire_child_runtime
    original_lease = recursive_child_runtime.SandboxLease

    async def observed_acquire(**kwargs: Any) -> Any:
        lease = await original_acquire(**kwargs)
        evidence.sandbox_ids.append(lease.sandbox_id)
        evidence.call_indexes.append(int(kwargs["call_index"]))
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


def _block_first_child_execution(
    monkeypatch: pytest.MonkeyPatch,
    *,
    evidence: _ScenarioEvidence,
    on_blocked: Any,
    hold_seconds: float = _HOLD_SECONDS,
) -> None:
    """Block the first CHILD broker execution; Root broker execution is untouched.

    ``on_blocked`` fires exactly once while the child interpreter is in flight.
    After the hold, the blocked execution raises a TimeoutError so the child
    can never complete successfully (no late child success).
    """
    original = DaytonaHttpToolBroker.execute_code
    lock = threading.Lock()

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
            first_child_block = is_child and evidence.block_calls == 0
            if is_child:
                evidence.block_calls += 1
        if first_child_block:
            on_blocked()
            time.sleep(hold_seconds)
            raise TimeoutError("p39c host-forced child stall")
        return original(self, code, variables, timeout_s=timeout_s, on_stdout=on_stdout)

    monkeypatch.setattr(DaytonaHttpToolBroker, "execute_code", blocking)


def _write_receipt(name: str, payload: dict[str, object]) -> None:
    """Write the canonical receipt; FLEET_LIVE_EVIDENCE_PATH adds a copy."""
    write_lane_receipt(f"p39c-cancel-deadline-{name}.json", f"-p39c-cd-{name}", payload)


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


def _assert_no_recursive_success(chunks: list[dict[str, Any]]) -> None:
    assert not [
        chunk
        for chunk in chunks
        if chunk.get("type") == "tool-output-available"
        and isinstance(chunk.get("output"), dict)
        and chunk["output"].get("status") == "completed"
        and chunk["output"].get("recursive_depth") == 1
    ]
    assert not [chunk for chunk in chunks if chunk.get("type") == "data-artifact"]
    assert not [chunk for chunk in chunks if chunk.get("type") == "data-structured-result"]


def _wait_for_cleanup_evidence(evidence: _ScenarioEvidence, *, receipts: int, timeout: float = 120.0) -> None:
    """Wait until the owned child cleanup settled after the cancel/deadline terminal.

    The cancellation stream terminal is projected while the recursive executor
    still owns the in-flight child; its lease close (interpreter shutdown,
    provider delete, absence confirmation, admission release) settles on the
    executor's cleanup boundary shortly after.
    """
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        shutdown_count = max(len(evidence.shutdown_order), len(evidence.class_shutdown_calls))
        if len(evidence.receipts) >= receipts and shutdown_count >= receipts:
            return
        time.sleep(0.25)


def test_live_cancel_with_in_flight_child_and_queued_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-036 cancellation: only cancelled semantics, zero leaked children."""
    settings = _case_settings(tmp_path, name="cancel", turn_timeout_seconds=600)
    evidence = _ScenarioEvidence()
    _install_scenario_evidence(monkeypatch, evidence)
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
        portal = client.portal
        assert portal is not None
        preparation._models = RLMModelBundle(
            _BatchRootLM(),
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
        )
        try:
            created = client.post("/api/sessions", json={"title": "P39c cancel canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])

            def request_cancel() -> None:
                run_id = get_active_lease_registry().holder(session_id)
                assert run_id is not None, "cancel canary requires an active Run lease"

                async def _mark_cancelled() -> str:
                    scope = LocalScope()
                    lifecycle = inventory.run_lifecycle
                    assert lifecycle is not None
                    return await lifecycle.request_cancel(
                        TurnAccess(scope.user_id, scope.workspace_id),
                        run_id,
                    )

                evidence.cancel_state = portal.call(_mark_cancelled)

            _block_first_child_execution(monkeypatch, evidence=evidence, on_blocked=request_cancel)
            started_at = time.perf_counter()
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Run the bounded P39c cancellation proof."},
                headers={"Idempotency-Key": f"p39c-cd-cancel-{uuid4()}"},
            )
            elapsed = time.perf_counter() - started_at
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            assert elapsed < 240

            # Cancellation landed while the child was in flight.
            assert evidence.cancel_state in {"requested", "already_requested"}
            assert evidence.block_calls >= 1
            # Only cancelled semantics: one abort terminal, never a success.
            abort_chunks = [chunk for chunk in chunks if chunk.get("type") == "abort"]
            assert len(abort_chunks) == 1
            assert chunks[-1] is abort_chunks[0]
            assert not [chunk for chunk in chunks if chunk.get("type") == "finish"]
            _assert_no_recursive_success(chunks)

            # Exactly one in-flight child acquired; the queued sibling never
            # acquired (no post-cancellation provider create).
            assert evidence.sandbox_ids, "child never reached in-flight execution"
            assert len(evidence.sandbox_ids) == 1
            assert evidence.call_indexes == [1]

            # The abort terminal settles the stream while the recursive
            # executor still owns the in-flight child; its strict close
            # settles on the cleanup boundary shortly after.
            _wait_for_cleanup_evidence(evidence, receipts=1)
            # Strict interpreter/broker shutdown ran for the in-flight child.
            # The instance-level wrapper is the preferred record (matches p39a);
            # the class-level trace is a robust fallback for the rare close-path
            # race where the wrapper object is not the one shut down.
            expected_shutdown = f"interpreter_shutdown:{evidence.sandbox_ids[0]}:strict_broker_cleanup=True"
            shutdown_traces = (evidence.shutdown_order, evidence.class_shutdown_calls)
            assert any(expected_shutdown in trace for trace in shutdown_traces), (
                f"strict shutdown not recorded: {shutdown_traces}"
            )
            # One clean provider receipt: delete + confirmed absence +
            # admission restored after confirmed cleanup.
            assert len(evidence.receipts) == 1
            receipt = evidence.receipts[0]
            assert receipt["sandbox_id"] == evidence.sandbox_ids[0]
            assert receipt["provider_action"] == "delete"
            assert receipt["provider_requested"] is True
            assert receipt["provider_confirmed_absent"] is True
            assert receipt["admission_released"] is True
            assert receipt["admission_released_after"] == "confirmed_cleanup"
            assert receipt["clean"] is True

            _wait_for_admission_baseline(
                resources, session_id, permits=settings.max_active_daytona_leases, portal=portal
            )
            assert get_active_lease_registry().holder(session_id) is None
            absence = portal.call(_all_absent, resources, list(evidence.sandbox_ids))
            assert all(absence.values())
            binding = portal.call(resources.bindings.get, session_id)
            if binding is not None and binding.sandbox_id is not None:
                sandbox_ids.add(binding.sandbox_id)
            sandbox_ids.update(evidence.sandbox_ids)
            sandbox_ids.update(resources._sandbox_ids)
        finally:
            cleanup_failures = portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()
    record_observed_sandbox_ids("cancel", sandbox_ids, {str(session_id)})
    _write_receipt(
        "cancel",
        {
            "schema": _RECEIPT_SCHEMA,
            "candidate": candidate_identity(),
            "scenario": "cancel-in-flight-child-queued-sibling",
            "evidence": {
                "child_sandbox_ids": evidence.sandbox_ids,
                "call_indexes": evidence.call_indexes,
                "queued_sibling_acquisitions": len(evidence.sandbox_ids) - 1,
                "cancel_state": evidence.cancel_state,
                "ordered_steps": evidence.shutdown_order,
                "class_shutdown_trace": evidence.class_shutdown_calls,
                "cleanup_receipts": evidence.receipts,
                "deletion_evidence": {
                    "delete_request_acceptance_seconds": evidence.delete_acceptance_seconds,
                    "confirmations": evidence.confirmations,
                },
            },
            "assertions": {
                "only_cancelled_semantics": True,
                "no_late_child_success": True,
                "queued_sibling_never_acquired": True,
                "strict_broker_shutdown": True,
                "provider_delete_confirmed_absent": True,
                "admission_restored": True,
                "lease_released": True,
                "zero_cleanup_failures": True,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        },
    )


def test_live_deadline_with_in_flight_child_and_queued_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-036 deadline: only timeout semantics, zero leaked children."""
    # The Turn timeout must leave enough headroom for the child acquisition to
    # reach interpreter execution on live providers; a deadline that fires
    # before the stall is not the scenario under test (see b8b3ec861 which
    # widened the same race in the p35d canary).
    turn_timeout_seconds = 180
    settings = _case_settings(tmp_path, name="deadline", turn_timeout_seconds=turn_timeout_seconds)
    evidence = _ScenarioEvidence()
    _install_scenario_evidence(monkeypatch, evidence)
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
        portal = client.portal
        assert portal is not None
        preparation._models = RLMModelBundle(
            _BatchRootLM(),
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
        )
        try:
            created = client.post("/api/sessions", json={"title": "P39c deadline canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])

            blocked = threading.Event()

            def note_blocked() -> None:
                blocked.set()

            # The hold must outlast the Turn deadline so the deadline fires
            # while the child is still in flight.
            _block_first_child_execution(
                monkeypatch,
                evidence=evidence,
                on_blocked=note_blocked,
                hold_seconds=turn_timeout_seconds + 25,
            )
            started_at = time.perf_counter()
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Run the bounded P39c deadline proof."},
                headers={"Idempotency-Key": f"p39c-cd-deadline-{uuid4()}"},
            )
            elapsed = time.perf_counter() - started_at
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            assert blocked.is_set(), "interpreter never reached the host-forced stall"
            assert turn_timeout_seconds <= elapsed < turn_timeout_seconds + 180

            # Only timeout/error semantics: finish/error terminal, no abort.
            finish_chunks = [chunk for chunk in chunks if chunk.get("type") == "finish"]
            assert len(finish_chunks) == 1
            assert finish_chunks[0].get("finishReason") == "error"
            assert chunks[-1] is finish_chunks[0]
            error_texts = [str(chunk.get("errorText", "")) for chunk in chunks if chunk.get("type") == "error"]
            assert any("timed out" in text.lower() for text in error_texts), error_texts
            assert not [chunk for chunk in chunks if chunk.get("type") == "abort"]
            _assert_no_recursive_success(chunks)

            # Exactly one in-flight child; the queued sibling never acquired.
            assert len(evidence.sandbox_ids) == 1
            assert evidence.call_indexes == [1]

            # The error terminal settles the stream while the recursive
            # executor still owns the stalled child; its strict close settles
            # on the cleanup boundary shortly after (the interpreter shutdown
            # must unwind the host-forced stall first).
            _wait_for_cleanup_evidence(evidence, receipts=1, timeout=240.0)
            # Strict interpreter/broker shutdown and one complete receipt.
            expected_shutdown = f"interpreter_shutdown:{evidence.sandbox_ids[0]}:strict_broker_cleanup=True"
            shutdown_traces = (evidence.shutdown_order, evidence.class_shutdown_calls)
            assert any(expected_shutdown in trace for trace in shutdown_traces), (
                f"strict shutdown not recorded: {shutdown_traces}"
            )
            assert len(evidence.receipts) == 1
            receipt = evidence.receipts[0]
            assert receipt["sandbox_id"] == evidence.sandbox_ids[0]
            assert receipt["provider_action"] == "delete"
            assert receipt["provider_requested"] is True
            assert receipt["provider_confirmed_absent"] is True
            assert receipt["admission_released"] is True
            assert receipt["clean"] is True

            _wait_for_admission_baseline(
                resources, session_id, permits=settings.max_active_daytona_leases, portal=portal
            )
            assert get_active_lease_registry().holder(session_id) is None
            absence = portal.call(_all_absent, resources, list(evidence.sandbox_ids))
            assert all(absence.values())
            binding = portal.call(resources.bindings.get, session_id)
            if binding is not None and binding.sandbox_id is not None:
                sandbox_ids.add(binding.sandbox_id)
            sandbox_ids.update(evidence.sandbox_ids)
            sandbox_ids.update(resources._sandbox_ids)
        finally:
            cleanup_failures = portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()
    record_observed_sandbox_ids("deadline", sandbox_ids, {str(session_id)})
    _write_receipt(
        "deadline",
        {
            "schema": _RECEIPT_SCHEMA,
            "candidate": candidate_identity(),
            "scenario": "deadline-in-flight-child-queued-sibling",
            "evidence": {
                "child_sandbox_ids": evidence.sandbox_ids,
                "call_indexes": evidence.call_indexes,
                "queued_sibling_acquisitions": len(evidence.sandbox_ids) - 1,
                "ordered_steps": evidence.shutdown_order,
                "class_shutdown_trace": evidence.class_shutdown_calls,
                "cleanup_receipts": evidence.receipts,
                "deletion_evidence": {
                    "delete_request_acceptance_seconds": evidence.delete_acceptance_seconds,
                    "confirmations": evidence.confirmations,
                },
            },
            "assertions": {
                "only_timeout_semantics": True,
                "no_late_child_success": True,
                "queued_sibling_never_acquired": True,
                "strict_broker_shutdown": True,
                "provider_delete_confirmed_absent": True,
                "admission_restored": True,
                "lease_released": True,
                "zero_cleanup_failures": True,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        },
    )
