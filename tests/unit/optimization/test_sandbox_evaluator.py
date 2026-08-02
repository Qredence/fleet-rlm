"""Unit contracts for fresh restrictive sandbox evaluations."""

from __future__ import annotations

from dataclasses import dataclass

from fleet_rlm.optimization.dataset import validate_records
from fleet_rlm.optimization.sandbox_evaluator import (
    DaytonaSandboxEvaluator,
    RestrictiveSandboxPolicy,
    SandboxEvaluationRequest,
    SandboxEvaluationResponse,
)


@dataclass
class _Sandbox:
    requests: list[SandboxEvaluationRequest]
    closed: bool = False

    def execute(self, request: SandboxEvaluationRequest) -> SandboxEvaluationResponse:
        self.requests.append(request)
        return SandboxEvaluationResponse(
            answer="ok",
            typed_output_valid=True,
            execution_safe=True,
            iterations=1,
            submodel_calls=1,
            elapsed_seconds=0.1,
            termination_mode="typed_submit",
        )

    def close(self) -> None:
        self.closed = True


class _Factory:
    def __init__(self) -> None:
        self.sandboxes: list[_Sandbox] = []

    def create(self, _policy: RestrictiveSandboxPolicy) -> _Sandbox:
        sandbox = _Sandbox([])
        self.sandboxes.append(sandbox)
        return sandbox


def test_evaluator_uses_and_closes_a_fresh_sandbox_per_record() -> None:
    record = validate_records(
        [
            {
                "record_id": f"record-{index}",
                "task": {"query": "question"},
                "output_contract": {"schema": "answer-v1"},
                "expectations": {},
                "provenance": {"redaction_version": "v1"},
            }
            for index in range(25)
        ]
    )[0]
    policy = RestrictiveSandboxPolicy(snapshot="trusted-v1", gateway_domains=("gateway.example.test",))
    factory = _Factory()
    evaluator = DaytonaSandboxEvaluator(policy=policy, factory=factory)

    evaluator.run("candidate", record)
    evaluator.run("candidate", record, attempt=2)

    assert len(factory.sandboxes) == 2
    assert all(sandbox.closed for sandbox in factory.sandboxes)
    request = factory.sandboxes[0].requests[0]
    assert request.record["query"] == "question"
    assert request.gateway_domains == ("gateway.example.test",)
    assert request.max_iterations == 10
