"""Unit contracts for disposable strict Daytona evaluator sandboxes."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import dspy
import pytest

from fleet_rlm.daytona.runtime import DaytonaSandboxSpec, LiveDaytonaPlatform
from fleet_rlm.optimization import daytona as subject
from fleet_rlm.optimization.daytona import (
    DisposableOptimizationSandboxFactory,
    OptimizationSandboxPolicy,
    OptimizationSandboxPolicyError,
    StrictDaytonaEvaluationLifecycle,
    StrictEvaluationCapabilityError,
    StrictEvaluationCleanupError,
    StrictEvaluationError,
    StrictEvaluationModels,
    StrictEvaluationProof,
    StrictEvaluationRequest,
)
from fleet_rlm.optimization.evidence import (
    StrictDaytonaProofError,
    StrictDaytonaProofReceipt,
    ValidatedStrictDaytonaProof,
    validate_strict_daytona_proof,
)
from fleet_rlm.optimization.types import OptimizationRecord
from fleet_rlm.rlm.program import RLMOptions


@dataclass
class _Platform:
    creates: list[dict] = field(default_factory=list)
    deleted: list[object] = field(default_factory=list)
    delete_options: list[dict[str, object]] = field(default_factory=list)

    async def create(self, **kwargs: Any) -> object:
        """
        Record sandbox creation arguments and return a generated sandbox identifier.

        Parameters:
            **kwargs (Any): Sandbox creation arguments to record.

        Returns:
            object: A sandbox record containing the generated identifier.
        """
        self.creates.append(kwargs)
        return {"id": f"sandbox-{len(self.creates)}"}

    async def delete(self, sandbox: object, **kwargs: object) -> None:
        """Record the sandbox scheduled for deletion."""
        self.deleted.append(sandbox)
        self.delete_options.append(kwargs)


@pytest.mark.asyncio
async def test_factory_creates_no_volume_ephemeral_gateway_only_sandbox() -> None:
    platform = _Platform()
    policy = OptimizationSandboxPolicy(
        snapshot="fleet-test-v1",
        gateway_domains=("Gateway.Example.Test",),
        auto_stop_interval_seconds=60,
        auto_delete_interval_seconds=0,
    )
    factory = DisposableOptimizationSandboxFactory(
        platform=platform,
        sandbox_spec=DaytonaSandboxSpec("fleet-test-v1"),
    )

    sandbox = await factory.create(
        policy=policy,
        run_id="run-1",
        candidate_sha256="a" * 64,
        record_id="record-1",
    )
    await factory.delete(sandbox)

    assert platform.deleted == [sandbox]
    assert platform.delete_options == [{"wait": True, "timeout": 30}]
    assert platform.creates == [
        {
            "labels": {
                "fleet-purpose": "optimization-evaluator",
                "fleet-policy": policy.policy_id[:24],
                "fleet-run": "run-1",
                "fleet-candidate": "a" * 64,
                "fleet-record": "record-1",
            },
            "with_volume": False,
            "ephemeral": True,
            "domain_allow_list": "gateway.example.test",
            "auto_stop_interval": 60,
        }
    ]


@pytest.mark.asyncio
async def test_live_platform_forwards_confirmed_delete_and_treats_absence_as_success() -> None:
    class _Client:
        def __init__(self) -> None:
            self.deleted: list[tuple[object, dict[str, object]]] = []
            self.present = True

        async def get(self, sandbox_id: str) -> object:
            if not self.present:
                error = RuntimeError("not found")
                error.status_code = 404
                raise error
            return SimpleNamespace(id=sandbox_id)

        async def delete(self, sandbox: object, **kwargs: object) -> None:
            self.deleted.append((sandbox, kwargs))

    client = _Client()
    platform = LiveDaytonaPlatform(client, DaytonaSandboxSpec("fleet-test-v1"))
    await platform.delete("sandbox-1", wait=True, timeout=17)
    client.present = False
    await platform.delete("sandbox-1", wait=True, timeout=17)

    assert client.deleted == [(SimpleNamespace(id="sandbox-1"), {"wait": True, "timeout": 17})]


@pytest.mark.asyncio
async def test_factory_creates_no_volume_ephemeral_block_all_sandbox() -> None:
    platform = _Platform()
    policy = OptimizationSandboxPolicy(
        snapshot="fleet-test-v1",
        gateway_domains=(),
        network_block_all=True,
        auto_stop_interval_seconds=60,
    )
    factory = DisposableOptimizationSandboxFactory(
        platform=platform,
        sandbox_spec=DaytonaSandboxSpec("fleet-test-v1"),
    )

    await factory.create(
        policy=policy,
        run_id="run-1",
        candidate_sha256="a" * 64,
        record_id="record-1",
    )

    assert platform.creates == [
        {
            "labels": {
                "fleet-purpose": "optimization-evaluator",
                "fleet-policy": policy.policy_id[:24],
                "fleet-run": "run-1",
                "fleet-candidate": "a" * 64,
                "fleet-record": "record-1",
            },
            "with_volume": False,
            "ephemeral": True,
            "network_block_all": True,
            "auto_stop_interval": 60,
        }
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"gateway_domains": ()}, "gateway"),
        (
            {"gateway_domains": ("gateway.example.test",), "network_block_all": True},
            "block-all",
        ),
        ({"gateway_domains": ("https://gateway.example.test",)}, "bare DNS"),
        ({"gateway_domains": ("gateway.example.test",), "gateway_cidrs": ("10.0.0.0/8",)}, "domain allow-list"),
        (
            {
                "gateway_domains": ("gateway.example.test",),
                "auto_stop_interval_seconds": 9,
                "auto_delete_interval_seconds": 1,
            },
            "auto-delete",
        ),
    ],
)
def test_policy_rejects_invalid_isolation_configuration(kwargs: dict, message: str) -> None:
    with pytest.raises(OptimizationSandboxPolicyError, match=message):
        OptimizationSandboxPolicy(snapshot="fleet-test-v1", **kwargs)


class _Interpreter:
    def __init__(self, events: list[str], *, fail_shutdown: bool = False) -> None:
        """
        Configure the interpreter test double and its shutdown behavior.

        Parameters:
            events (list[str]): Collection used to record lifecycle events.
            fail_shutdown (bool): Whether shutdown should raise an error.
        """
        self._events = events
        self._fail_shutdown = fail_shutdown
        self._shutdown = False

    def new_invocation(self) -> SimpleNamespace:
        def shutdown() -> None:
            assert not self._shutdown
            self._events.append("invocation_shutdown")

        return SimpleNamespace(shutdown=shutdown)

    def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
        """Record interpreter shutdown and raise an error when shutdown is configured to fail."""
        assert strict_broker_cleanup is True
        if self._shutdown:
            return
        self._events.append("shutdown")
        if self._fail_shutdown:
            raise RuntimeError("shutdown failed")
        self._shutdown = True


class _RLM:
    def __init__(
        self,
        prediction: object | BaseException,
        *,
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self._prediction = prediction
        self._entered = entered
        self._release = release

    def __call__(self, **kwargs: Any) -> object:
        """
        Evaluate a curated input request and provide the configured prediction.

        Parameters:
            curated_input_handle (dict): Input handle containing the transaction ID,
                SHA-256 hash, schema, and byte size.

        Returns:
            object: The configured prediction.

        Raises:
            BaseException: The configured prediction exception, when evaluation is
                configured to fail.
        """
        assert set(kwargs) == {"curated_input_handle", "interpreter_factory"}
        assert set(kwargs["curated_input_handle"]) == {"transaction_id", "sha256", "schema", "byte_size"}
        interpreter_factory = kwargs["interpreter_factory"]
        assert callable(interpreter_factory)
        interpreter = interpreter_factory()
        try:
            if self._entered is not None:
                self._entered.set()
            if self._release is not None:
                assert self._release.wait(timeout=5)
            if isinstance(self._prediction, BaseException):
                raise self._prediction
            return self._prediction
        finally:
            interpreter.shutdown()


class _LifecycleFactory:
    def __init__(
        self,
        events: list[str],
        *,
        fail_delete: bool = False,
        on_delete: Any | None = None,
        delete_failures: int = 0,
        delete_gate: asyncio.Event | None = None,
        delete_entered: asyncio.Event | None = None,
    ) -> None:
        self.events = events
        self.deleted: list[object] = []
        self.fail_delete = fail_delete
        self.on_delete = on_delete
        self.delete_failures = delete_failures
        self.delete_gate = delete_gate
        self.delete_entered = delete_entered

    async def create(self, **kwargs: Any) -> object:
        self.events.append("create")
        assert len(kwargs["candidate_sha256"]) == 64
        assert all(character in "0123456789abcdef" for character in kwargs["candidate_sha256"])
        return SimpleNamespace(id=f"sandbox-{self.events.count('create')}")

    async def delete(self, sandbox: object) -> None:
        """Delete a sandbox and record the deletion event.

        Parameters:
            sandbox (object): The sandbox to delete.

        Raises:
            RuntimeError: If sandbox deletion fails.
        """
        self.events.append("delete")
        self.deleted.append(sandbox)
        if self.delete_entered is not None:
            self.delete_entered.set()
        if self.delete_gate is not None:
            await self.delete_gate.wait()
        if self.on_delete is not None:
            self.on_delete()
        if self.fail_delete or self.delete_failures:
            if self.delete_failures:
                self.delete_failures -= 1
            raise RuntimeError("delete failed")


class _Signature(dspy.Signature):
    request: str = dspy.InputField()
    answer: str = dspy.OutputField()


def _record() -> OptimizationRecord:
    return OptimizationRecord(
        record_id="record-1",
        query="safe input",
        output_contract={},
        expectations={},
        execution_requirements={},
        provenance={"redaction_version": "v1"},
        content_sha256="b" * 64,
    )


def _proof() -> ValidatedStrictDaytonaProof:
    """
    Create a validated proof for the standard test sandbox policy.

    Returns:
        ValidatedStrictDaytonaProof: A proof confirming the configured snapshot,
        gateway domain, isolation controls, and required security outcomes.
    """
    policy = OptimizationSandboxPolicy("fleet-test-v1", ("gateway.example.test",))
    return validate_strict_daytona_proof(
        StrictDaytonaProofReceipt(
            policy_id=policy.policy_id,
            snapshot=policy.snapshot,
            gateway_domains=policy.gateway_domains,
            controls={
                "no_volume_requested": True,
                "ephemeral_requested": True,
                "domain_allow_list_requested": True,
                "auto_stop_seconds": 300,
                "auto_delete_seconds": 0,
            },
            outcomes={
                "broker_started": "passed",
                "broker_round_trip": "passed",
                "valid_capability_read": "passed",
                "invalid_transaction_denied": "passed",
                "invalid_digest_denied": "passed",
                "direct_egress_denied": "passed",
                "denied_egress_unobserved": "passed",
                "effective_policy_verified": "passed",
                "host_credentials_absent": "passed",
                "interpreter_cleanup": "passed",
                "broker_cleanup": "passed",
                "sandbox_deleted": "passed",
                "approved_gateway_egress": "passed",
            },
        )
    )


def _lifecycle(
    factory: _LifecycleFactory,
    proof: object,
    *,
    models: StrictEvaluationModels | None = None,
    execution_timeout_seconds: int = 60,
) -> StrictDaytonaEvaluationLifecycle:
    """
    Create a strict Daytona evaluation lifecycle for the fleet test policy.

    Parameters:
        factory (_LifecycleFactory): Factory used to create evaluation sandboxes.
        proof (object): Sandbox proof supplied to the lifecycle.

    Returns:
        StrictDaytonaEvaluationLifecycle: Configured evaluation lifecycle.
    """
    return StrictDaytonaEvaluationLifecycle(
        factory=factory,  # type: ignore[arg-type]
        policy=OptimizationSandboxPolicy("fleet-test-v1", ("gateway.example.test",)),
        proof=proof,
        models=models or StrictEvaluationModels(root_lm=object(), sub_lm=object()),  # type: ignore[arg-type]
        options=RLMOptions(max_iters=2, max_llm_calls=3, max_output_chars=100),
        execution_timeout_seconds=execution_timeout_seconds,
    )


@pytest.mark.asyncio
async def test_lifecycle_rejects_manual_proofs_before_sandbox_creation() -> None:
    events: list[str] = []
    factory = _LifecycleFactory(events)
    manual_proof = StrictEvaluationProof(
        readonly_input_boundary_verified=True,
        gateway_broker_verified=True,
        proof_id="proof-v1",
    )

    with pytest.raises(StrictEvaluationCapabilityError, match="validated Daytona proof"):
        _lifecycle(factory, manual_proof)

    assert events == []


def test_lifecycle_rejects_proof_for_a_different_domain_policy() -> None:
    events: list[str] = []
    factory = _LifecycleFactory(events)

    with pytest.raises(StrictDaytonaProofError, match="does not match"):
        StrictDaytonaEvaluationLifecycle(
            factory=factory,  # type: ignore[arg-type]
            policy=OptimizationSandboxPolicy("fleet-test-v1", ("other-gateway.example.test",)),
            proof=_proof(),
            models=StrictEvaluationModels(root_lm=object(), sub_lm=object()),  # type: ignore[arg-type]
            options=RLMOptions(max_iters=2, max_llm_calls=3, max_output_chars=100),
        )

    assert events == []


@pytest.mark.asyncio
async def test_lifecycle_builds_fresh_interpreter_and_rlm_then_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    factory = _LifecycleFactory(events)
    interpreters: list[_Interpreter] = []
    rlms: list[_RLM] = []

    def build_interpreter(**kwargs: Any) -> _Interpreter:
        """
        Create and record an interpreter configured with the curated-input reader tool.

        Parameters:
            **kwargs (Any): Interpreter configuration, including the required `read_curated_input` tool.

        Returns:
            _Interpreter: The newly created interpreter.
        """
        assert set(kwargs["tools"]) == {"read_curated_input"}
        interpreter = _Interpreter(events)
        interpreters.append(interpreter)
        return interpreter

    def build_rlm(**kwargs: Any) -> _RLM:
        assert [tool.name for tool in kwargs["tools"]] == ["read_curated_input"]
        assert kwargs["sub_lm"] is not None
        rlm = _RLM(SimpleNamespace(answer="typed answer", trajectory=[], get_lm_usage=lambda: {}))
        rlms.append(rlm)
        return rlm

    monkeypatch.setattr(subject, "sandbox_backend", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(subject, "DaytonaCodeInterpreter", build_interpreter)
    monkeypatch.setattr(subject, "build_native_rlm", build_rlm)

    def strict_inputs(handle: dict[str, object]) -> dict[str, object]:
        """
        Wrap a validated curated-input handle for strict evaluation.

        Parameters:
            handle (dict[str, object]): Input handle containing exactly `transaction_id`,
                `sha256`, `schema`, and `byte_size`.

        Returns:
            dict[str, object]: A mapping containing the curated input handle.
        """
        assert set(handle) == {"transaction_id", "sha256", "schema", "byte_size"}
        assert handle["sha256"] != hashlib.sha256(("a" * 64).encode()).hexdigest()
        return {"curated_input_handle": handle}

    monkeypatch.setattr(subject, "_strict_named_inputs", strict_inputs)
    monkeypatch.setattr(subject.dspy, "context", lambda **_kwargs: nullcontext())

    lifecycle = _lifecycle(factory, _proof())
    first = await lifecycle.evaluate(StrictEvaluationRequest("a" * 64, _record(), "run-1"))
    await lifecycle.evaluate(StrictEvaluationRequest("a" * 64, _record(), "run-2"))

    assert first.prediction.display_text == "typed answer"
    assert first.candidate_sha256 == hashlib.sha256(("a" * 64).encode()).hexdigest()
    assert first.record_sha256 == "b" * 64
    assert first.proof_id == _proof().proof_id
    assert len(interpreters) == len(rlms) == 2
    assert interpreters[0] is not interpreters[1]
    assert rlms[0] is not rlms[1]
    assert events == ["create", "invocation_shutdown", "delete", "shutdown"] * 2


@pytest.mark.asyncio
async def test_lifecycle_runs_pinned_native_rlm_in_owned_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.daytona.interpreter import InProcessInterpreterBackend

    events: list[str] = []
    factory = _LifecycleFactory(events)
    adapter = dspy.JSONAdapter()
    root_lm = dspy.utils.DummyLM(
        [{"reasoning": "submit the typed result", "code": "SUBMIT(answer='native answer')"}],
        adapter=adapter,
    )
    models = StrictEvaluationModels(root_lm=root_lm, sub_lm=root_lm)
    monkeypatch.setattr(subject, "sandbox_backend", lambda *_args, **_kwargs: InProcessInterpreterBackend())

    result = await _lifecycle(factory, _proof(), models=models).evaluate(
        StrictEvaluationRequest("a" * 64, _record(), "run-native")
    )

    assert result.prediction.display_text == "native answer"
    assert events == ["create", "delete"]
    assert len(root_lm.history) == 1


@pytest.mark.asyncio
async def test_lifecycle_keeps_event_loop_responsive_and_revokes_timed_out_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    events: list[str] = []
    factory = _LifecycleFactory(events, on_delete=release.set)
    interpreter = _Interpreter(events)
    monkeypatch.setattr(subject, "sandbox_backend", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(subject, "DaytonaCodeInterpreter", lambda **_kwargs: interpreter)
    monkeypatch.setattr(
        subject,
        "build_native_rlm",
        lambda **_kwargs: _RLM(RuntimeError("sandbox revoked"), entered=entered, release=release),
    )
    monkeypatch.setattr(subject, "_strict_named_inputs", lambda handle: {"curated_input_handle": handle})
    monkeypatch.setattr(subject.dspy, "context", lambda **_kwargs: nullcontext())
    lifecycle = _lifecycle(factory, _proof(), execution_timeout_seconds=1)

    heartbeat = asyncio.Event()

    async def mark_heartbeat() -> None:
        await asyncio.sleep(0.05)
        heartbeat.set()

    heartbeat_task = asyncio.create_task(mark_heartbeat())
    started = time.monotonic()
    evaluation = asyncio.create_task(lifecycle.evaluate(StrictEvaluationRequest("a" * 64, _record(), "run-timeout")))
    assert await asyncio.to_thread(entered.wait, 1)
    # The evaluator is blocked in its RLM worker, but this coroutine still runs.
    await asyncio.wait_for(heartbeat.wait(), timeout=0.5)
    assert time.monotonic() - started < 0.5
    await heartbeat_task

    with pytest.raises(StrictEvaluationError, match="timed out"):
        await evaluation

    assert release.is_set()
    assert events == ["create", "delete", "invocation_shutdown", "shutdown"]


@pytest.mark.asyncio
async def test_lifecycle_retains_ownership_for_late_worker_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = threading.Event()
    release = threading.Event()
    events: list[str] = []
    factory = _LifecycleFactory(events)
    interpreter = _Interpreter(events)
    monkeypatch.setattr(subject, "sandbox_backend", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(subject, "DaytonaCodeInterpreter", lambda **_kwargs: interpreter)
    monkeypatch.setattr(
        subject,
        "build_native_rlm",
        lambda **_kwargs: _RLM(RuntimeError("sandbox revoked"), entered=entered, release=release),
    )
    monkeypatch.setattr(subject, "_strict_named_inputs", lambda handle: {"curated_input_handle": handle})
    monkeypatch.setattr(subject.dspy, "context", lambda **_kwargs: nullcontext())
    lifecycle = _lifecycle(factory, _proof(), execution_timeout_seconds=1)

    evaluation = asyncio.create_task(
        lifecycle.evaluate(StrictEvaluationRequest("a" * 64, _record(), "run-late-worker"))
    )
    assert await asyncio.to_thread(entered.wait, 1)
    with pytest.raises(StrictEvaluationCleanupError, match="sandbox-1"):
        await evaluation

    assert lifecycle._cleanup_supervisor.active_jobs == 1
    assert events == ["create", "delete"]
    release.set()
    await lifecycle.aclose(drain_seconds=2)
    assert events == ["create", "delete", "invocation_shutdown", "shutdown"]


@pytest.mark.asyncio
async def test_lifecycle_cancellation_deletes_sandbox_and_observes_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = threading.Event()
    release = threading.Event()
    events: list[str] = []
    factory = _LifecycleFactory(events, on_delete=release.set)
    interpreter = _Interpreter(events)
    monkeypatch.setattr(subject, "sandbox_backend", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(subject, "DaytonaCodeInterpreter", lambda **_kwargs: interpreter)
    monkeypatch.setattr(
        subject,
        "build_native_rlm",
        lambda **_kwargs: _RLM(RuntimeError("sandbox revoked"), entered=entered, release=release),
    )
    monkeypatch.setattr(subject, "_strict_named_inputs", lambda handle: {"curated_input_handle": handle})
    monkeypatch.setattr(subject.dspy, "context", lambda **_kwargs: nullcontext())
    evaluation = asyncio.create_task(
        _lifecycle(factory, _proof()).evaluate(StrictEvaluationRequest("a" * 64, _record(), "run-cancelled"))
    )
    assert await asyncio.to_thread(entered.wait, 1)
    evaluation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await evaluation

    assert release.is_set()
    assert events == ["create", "delete", "invocation_shutdown", "shutdown"]


@pytest.mark.asyncio
async def test_lifecycle_deletes_sandbox_when_interpreter_setup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    factory = _LifecycleFactory(events)

    def fail_backend(*_args, **_kwargs):
        raise RuntimeError("interpreter setup failed")

    monkeypatch.setattr(subject, "sandbox_backend", fail_backend)
    with pytest.raises(RuntimeError, match="interpreter setup failed"):
        await _lifecycle(factory, _proof()).evaluate(StrictEvaluationRequest("a" * 64, _record(), "run-setup-failed"))

    assert events == ["create", "delete"]


@pytest.mark.asyncio
async def test_lifecycle_preserves_primary_failure_when_cleanup_also_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    factory = _LifecycleFactory(events, fail_delete=True)
    monkeypatch.setattr(subject, "sandbox_backend", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(subject, "DaytonaCodeInterpreter", lambda **_kwargs: _Interpreter(events, fail_shutdown=True))
    monkeypatch.setattr(subject, "build_native_rlm", lambda **_kwargs: _RLM(RuntimeError("rlm failed")))
    monkeypatch.setattr(subject, "_strict_named_inputs", lambda handle: {"curated_input_handle": handle})
    monkeypatch.setattr(subject.dspy, "context", lambda **_kwargs: nullcontext())

    with pytest.raises(RuntimeError, match="rlm failed"):
        await _lifecycle(factory, _proof()).evaluate(StrictEvaluationRequest("candidate", _record(), "run-1"))

    assert events == ["create", "invocation_shutdown", "delete", "shutdown", "delete"]


@pytest.mark.asyncio
async def test_lifecycle_raises_cleanup_error_without_primary_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    factory = _LifecycleFactory(events, fail_delete=True)
    monkeypatch.setattr(subject, "sandbox_backend", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(subject, "DaytonaCodeInterpreter", lambda **_kwargs: _Interpreter(events))
    monkeypatch.setattr(
        subject,
        "build_native_rlm",
        lambda **_kwargs: _RLM(SimpleNamespace(answer="typed answer", trajectory=[], get_lm_usage=lambda: {})),
    )
    monkeypatch.setattr(subject, "_strict_named_inputs", lambda handle: {"curated_input_handle": handle})
    monkeypatch.setattr(subject.dspy, "context", lambda **_kwargs: nullcontext())

    with pytest.raises(StrictEvaluationCleanupError, match="cleanup"):
        await _lifecycle(factory, _proof()).evaluate(StrictEvaluationRequest("candidate", _record(), "run-1"))

    assert events == ["create", "invocation_shutdown", "delete", "shutdown", "delete"]


@pytest.mark.asyncio
async def test_lifecycle_retries_one_failed_delete_after_worker_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    factory = _LifecycleFactory(events, delete_failures=1)
    monkeypatch.setattr(subject, "sandbox_backend", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(subject, "DaytonaCodeInterpreter", lambda **_kwargs: _Interpreter(events))
    monkeypatch.setattr(
        subject,
        "build_native_rlm",
        lambda **_kwargs: _RLM(SimpleNamespace(answer="typed answer", trajectory=[], get_lm_usage=lambda: {})),
    )
    monkeypatch.setattr(subject, "_strict_named_inputs", lambda handle: {"curated_input_handle": handle})
    monkeypatch.setattr(subject.dspy, "context", lambda **_kwargs: nullcontext())

    result = await _lifecycle(factory, _proof()).evaluate(
        StrictEvaluationRequest("candidate", _record(), "run-retry-delete")
    )

    assert result.prediction.display_text == "typed answer"
    assert events == ["create", "invocation_shutdown", "delete", "shutdown", "delete"]


@pytest.mark.asyncio
async def test_lifecycle_aclose_reports_sandbox_after_repeated_delete_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    factory = _LifecycleFactory(events, fail_delete=True)
    monkeypatch.setattr(subject, "sandbox_backend", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(subject, "DaytonaCodeInterpreter", lambda **_kwargs: _Interpreter(events))
    monkeypatch.setattr(
        subject,
        "build_native_rlm",
        lambda **_kwargs: _RLM(SimpleNamespace(answer="typed answer", trajectory=[], get_lm_usage=lambda: {})),
    )
    monkeypatch.setattr(subject, "_strict_named_inputs", lambda handle: {"curated_input_handle": handle})
    monkeypatch.setattr(subject.dspy, "context", lambda **_kwargs: nullcontext())
    lifecycle = _lifecycle(factory, _proof())

    with pytest.raises(StrictEvaluationCleanupError, match="sandbox-1"):
        await lifecycle.evaluate(StrictEvaluationRequest("candidate", _record(), "run-delete-fails"))
    with pytest.raises(StrictEvaluationCleanupError, match="sandbox-1"):
        await lifecycle.aclose(drain_seconds=1)

    assert len(factory.deleted) >= 3


@pytest.mark.asyncio
async def test_lifecycle_aclose_drains_delete_that_was_only_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    delete_gate = asyncio.Event()
    delete_entered = asyncio.Event()
    factory = _LifecycleFactory(events, delete_gate=delete_gate, delete_entered=delete_entered)
    monkeypatch.setattr(subject, "sandbox_backend", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(subject, "DaytonaCodeInterpreter", lambda **_kwargs: _Interpreter(events))
    monkeypatch.setattr(
        subject,
        "build_native_rlm",
        lambda **_kwargs: _RLM(SimpleNamespace(answer="typed answer", trajectory=[], get_lm_usage=lambda: {})),
    )
    monkeypatch.setattr(subject, "_strict_named_inputs", lambda handle: {"curated_input_handle": handle})
    monkeypatch.setattr(subject.dspy, "context", lambda **_kwargs: nullcontext())
    lifecycle = _lifecycle(factory, _proof(), execution_timeout_seconds=1)

    evaluation = asyncio.create_task(
        lifecycle.evaluate(StrictEvaluationRequest("candidate", _record(), "run-delete-pending"))
    )
    await asyncio.wait_for(delete_entered.wait(), timeout=1)
    with pytest.raises(StrictEvaluationCleanupError, match="sandbox-1"):
        await evaluation

    delete_gate.set()
    await lifecycle.aclose(drain_seconds=2)
    assert events.count("delete") == 1


@pytest.mark.asyncio
async def test_cancellation_during_delete_wait_propagates_and_retains_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    delete_gate = asyncio.Event()
    delete_entered = asyncio.Event()
    factory = _LifecycleFactory(events, delete_gate=delete_gate, delete_entered=delete_entered)
    monkeypatch.setattr(subject, "sandbox_backend", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(subject, "DaytonaCodeInterpreter", lambda **_kwargs: _Interpreter(events))
    monkeypatch.setattr(
        subject,
        "build_native_rlm",
        lambda **_kwargs: _RLM(SimpleNamespace(answer="typed answer", trajectory=[], get_lm_usage=lambda: {})),
    )
    monkeypatch.setattr(subject, "_strict_named_inputs", lambda handle: {"curated_input_handle": handle})
    monkeypatch.setattr(subject.dspy, "context", lambda **_kwargs: nullcontext())
    lifecycle = _lifecycle(factory, _proof(), execution_timeout_seconds=2)

    evaluation = asyncio.create_task(
        lifecycle.evaluate(StrictEvaluationRequest("candidate", _record(), "run-cancel-delete"))
    )
    await asyncio.wait_for(delete_entered.wait(), timeout=1)
    evaluation.cancel()
    delete_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await evaluation
    await lifecycle.aclose(drain_seconds=2)


@pytest.mark.asyncio
async def test_cancellation_during_worker_drain_propagates_and_retains_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    events: list[str] = []
    delete_entered = asyncio.Event()
    factory = _LifecycleFactory(events, delete_entered=delete_entered)
    monkeypatch.setattr(subject, "sandbox_backend", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(subject, "DaytonaCodeInterpreter", lambda **_kwargs: _Interpreter(events))
    monkeypatch.setattr(
        subject,
        "build_native_rlm",
        lambda **_kwargs: _RLM(RuntimeError("sandbox revoked"), entered=entered, release=release),
    )
    monkeypatch.setattr(subject, "_strict_named_inputs", lambda handle: {"curated_input_handle": handle})
    monkeypatch.setattr(subject.dspy, "context", lambda **_kwargs: nullcontext())
    lifecycle = _lifecycle(factory, _proof(), execution_timeout_seconds=3)

    evaluation = asyncio.create_task(
        lifecycle.evaluate(StrictEvaluationRequest("candidate", _record(), "run-cancel-worker"))
    )
    assert await asyncio.to_thread(entered.wait, 1)
    await asyncio.wait_for(delete_entered.wait(), timeout=5)
    await asyncio.sleep(0.05)
    evaluation.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await evaluation
    await lifecycle.aclose(drain_seconds=2)


@pytest.mark.asyncio
async def test_lifecycle_does_not_accept_success_when_shutdown_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    factory = _LifecycleFactory(events)
    monkeypatch.setattr(subject, "sandbox_backend", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(subject, "DaytonaCodeInterpreter", lambda **_kwargs: _Interpreter(events, fail_shutdown=True))
    monkeypatch.setattr(
        subject,
        "build_native_rlm",
        lambda **_kwargs: _RLM(SimpleNamespace(answer="typed answer", trajectory=[], get_lm_usage=lambda: {})),
    )
    monkeypatch.setattr(subject, "_strict_named_inputs", lambda handle: {"curated_input_handle": handle})
    monkeypatch.setattr(subject.dspy, "context", lambda **_kwargs: nullcontext())

    lifecycle = _lifecycle(factory, _proof())
    with pytest.raises(StrictEvaluationCleanupError, match="sandbox-1"):
        await lifecycle.evaluate(StrictEvaluationRequest("candidate", _record(), "run-shutdown-failed"))
    with pytest.raises(StrictEvaluationCleanupError, match="sandbox-1"):
        await lifecycle.aclose(drain_seconds=1)

    assert events == ["create", "invocation_shutdown", "delete", "shutdown", "shutdown"]
