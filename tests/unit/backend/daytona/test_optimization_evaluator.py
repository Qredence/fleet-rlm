"""Unit contracts for disposable strict Daytona evaluator sandboxes."""

from __future__ import annotations

import hashlib
from contextlib import nullcontext
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import dspy
import pytest

from fleet_rlm.daytona import optimization_evaluator as subject
from fleet_rlm.daytona.optimization_evaluator import (
    DisposableOptimizationSandboxFactory,
    OptimizationSandboxPolicy,
    OptimizationSandboxPolicyError,
    StrictDaytonaEvaluationLifecycle,
    StrictEvaluationCapabilityError,
    StrictEvaluationCleanupError,
    StrictEvaluationModels,
    StrictEvaluationProof,
    StrictEvaluationRequest,
)
from fleet_rlm.daytona.provisioning import DaytonaSandboxSpec
from fleet_rlm.optimization.evidence import (
    StrictDaytonaProofError,
    StrictDaytonaProofReceipt,
    ValidatedStrictDaytonaProof,
    validate_strict_daytona_proof,
)
from fleet_rlm.optimization.types import OptimizationRecord
from fleet_rlm.rlm.dspy_contract import RLMOptions


@dataclass
class _Platform:
    creates: list[dict] = field(default_factory=list)
    deleted: list[object] = field(default_factory=list)

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

    async def delete(self, sandbox: object) -> None:
        """Record the sandbox scheduled for deletion."""
        self.deleted.append(sandbox)


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


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"gateway_domains": ()}, "gateway"),
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

    def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
        """Record interpreter shutdown and raise an error when shutdown is configured to fail."""
        assert strict_broker_cleanup is True
        self._events.append("shutdown")
        if self._fail_shutdown:
            raise RuntimeError("shutdown failed")


class _RLM:
    def __init__(self, prediction: object | BaseException) -> None:
        self._prediction = prediction

    async def acall(self, *args: Any, **kwargs: Any) -> object:
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
        assert len(args) == 1
        assert set(kwargs) == {"curated_input_handle"}
        assert set(kwargs["curated_input_handle"]) == {"transaction_id", "sha256", "schema", "byte_size"}
        if isinstance(self._prediction, BaseException):
            raise self._prediction
        return self._prediction


class _LifecycleFactory:
    def __init__(self, events: list[str], *, fail_delete: bool = False) -> None:
        self.events = events
        self.deleted: list[object] = []
        self.fail_delete = fail_delete

    async def create(self, **kwargs: Any) -> object:
        self.events.append("create")
        assert len(kwargs["candidate_sha256"]) == 64
        assert all(character in "0123456789abcdef" for character in kwargs["candidate_sha256"])
        return object()

    async def delete(self, sandbox: object) -> None:
        """Delete a sandbox and record the deletion event.

        Parameters:
            sandbox (object): The sandbox to delete.

        Raises:
            RuntimeError: If sandbox deletion fails.
        """
        self.events.append("delete")
        self.deleted.append(sandbox)
        if self.fail_delete:
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


def _lifecycle(factory: _LifecycleFactory, proof: object) -> StrictDaytonaEvaluationLifecycle:
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
        models=StrictEvaluationModels(root_lm=object(), sub_lm=object()),  # type: ignore[arg-type]
        options=RLMOptions(max_iters=2, max_llm_calls=3, max_output_chars=100),
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
    assert events == ["create", "shutdown", "delete", "create", "shutdown", "delete"]


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

    assert events == ["create", "shutdown", "delete"]


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

    assert events == ["create", "shutdown", "delete"]
