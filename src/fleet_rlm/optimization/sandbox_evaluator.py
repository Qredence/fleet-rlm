"""Strict, fresh-sandbox evaluation boundary for untrusted RLM candidates.

This module deliberately defines a small port.  Production wiring must provide a
Daytona implementation that proves every policy control before a live optimizer
run is allowed.  There is intentionally no in-process fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from fleet_rlm.optimization.types import OptimizationRecord


class SandboxPolicyError(RuntimeError):
    """The configured Daytona path cannot enforce optimizer isolation."""


@dataclass(frozen=True, slots=True)
class RestrictiveSandboxPolicy:
    """Non-negotiable controls for a candidate-record evaluation sandbox."""

    snapshot: str
    gateway_domains: tuple[str, ...]
    execution_timeout_seconds: int = 60
    output_limit_chars: int = 10_000
    max_iterations: int = 10
    max_llm_calls: int = 15

    def __post_init__(self) -> None:
        if not self.snapshot.strip():
            raise SandboxPolicyError("a trusted optimization snapshot is required")
        if not self.gateway_domains:
            raise SandboxPolicyError("gateway-only egress requires an explicit domain allowlist")
        if any(not domain or "://" in domain or "/" in domain for domain in self.gateway_domains):
            raise SandboxPolicyError("gateway domains must be bare host names")
        if self.execution_timeout_seconds < 1 or self.output_limit_chars < 1:
            raise SandboxPolicyError("positive sandbox execution limits are required")

    def request(self, *, candidate: str, record: OptimizationRecord, attempt: int) -> SandboxEvaluationRequest:
        """Create the minimal redacted request supplied to one disposable sandbox."""
        return SandboxEvaluationRequest(
            candidate=candidate,
            record=record.optimizer_example(),
            candidate_sha256=_digest(candidate),
            record_sha256=record.content_sha256,
            attempt=attempt,
            snapshot=self.snapshot,
            gateway_domains=self.gateway_domains,
            execution_timeout_seconds=self.execution_timeout_seconds,
            output_limit_chars=self.output_limit_chars,
            max_iterations=self.max_iterations,
            max_llm_calls=self.max_llm_calls,
        )


@dataclass(frozen=True, slots=True)
class SandboxEvaluationRequest:
    """The sole trusted-host-to-sandbox payload."""

    candidate: str
    record: dict[str, Any]
    candidate_sha256: str
    record_sha256: str
    attempt: int
    snapshot: str
    gateway_domains: tuple[str, ...]
    execution_timeout_seconds: int
    output_limit_chars: int
    max_iterations: int
    max_llm_calls: int


@dataclass(frozen=True, slots=True)
class SandboxEvaluationResponse:
    """Sanitized result returned by a disposable sandbox."""

    answer: str | None
    typed_output_valid: bool
    execution_safe: bool
    iterations: int
    submodel_calls: int
    elapsed_seconds: float
    termination_mode: str
    failure_category: str | None = None


class RestrictiveSandbox(Protocol):
    """One fresh sandbox lifecycle; implementations must never retain state."""

    def execute(self, request: SandboxEvaluationRequest) -> SandboxEvaluationResponse: ...

    def close(self) -> None: ...


class RestrictiveSandboxFactory(Protocol):
    """Create one newly provisioned disposable Daytona sandbox."""

    def create(self, policy: RestrictiveSandboxPolicy) -> RestrictiveSandbox: ...


class DaytonaSandboxEvaluator:
    """Evaluate every candidate-record pair in a fresh restrictive sandbox."""

    def __init__(self, *, policy: RestrictiveSandboxPolicy, factory: RestrictiveSandboxFactory) -> None:
        self._policy = policy
        self._factory = factory

    def run(self, candidate: str, record: OptimizationRecord, *, attempt: int = 1) -> SandboxEvaluationResponse:
        """Execute one candidate and always dispose its sandbox."""
        if not candidate.strip():
            raise SandboxPolicyError("candidate instructions must be non-empty")
        sandbox = self._factory.create(self._policy)
        try:
            return sandbox.execute(self._policy.request(candidate=candidate, record=record, attempt=attempt))
        finally:
            sandbox.close()


def _digest(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


__all__ = [
    "DaytonaSandboxEvaluator",
    "RestrictiveSandbox",
    "RestrictiveSandboxFactory",
    "RestrictiveSandboxPolicy",
    "SandboxEvaluationRequest",
    "SandboxEvaluationResponse",
    "SandboxPolicyError",
]
