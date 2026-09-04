"""Stable, content-free runtime benchmark receipt contract.

This module is intentionally independent from HTTP/SSE schemas. Benchmark
receipts are engineering artifacts, not a second public runtime API. They
contain aggregates and selected policy provenance only: never prompts, model
responses, private reasoning, credentials, paths, or provider exceptions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RUNTIME_BENCHMARK_SCHEMA = "fleet.runtime-benchmark/v1"

RuntimeMode = Literal["legacy", "v2"]
TerminalStatus = Literal["succeeded", "failed", "cancelled", "timed_out", "cleanup_failed"]


class LifecycleTiming(BaseModel):
    """Daytona lifecycle timings in milliseconds; unavailable phases are null."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sandbox_lookup_ms: int | None = Field(default=None, ge=0)
    sandbox_creation_ms: int | None = Field(default=None, ge=0)
    sandbox_start_ms: int | None = Field(default=None, ge=0)
    volume_mount_verification_ms: int | None = Field(default=None, ge=0)
    broker_interpreter_startup_ms: int | None = Field(default=None, ge=0)
    code_execution_ms: int | None = Field(default=None, ge=0)
    child_deletion_confirmation_ms: int | None = Field(default=None, ge=0)


class RuntimeBenchmarkResult(BaseModel):
    """One scenario's aggregate measurement under a fully identified policy."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_id: Literal["fleet.runtime-benchmark/v1"] = Field(
        default=RUNTIME_BENCHMARK_SCHEMA,
        alias="schema",
        serialization_alias="schema",
    )
    scenario: str = Field(min_length=1, max_length=96)
    runtime_mode: RuntimeMode
    root_model: str = Field(min_length=1, max_length=256)
    sub_model: str = Field(min_length=1, max_length=256)
    turn_duration_ms: int = Field(ge=0)
    provider_attempts: int | None = Field(default=None, ge=0)
    root_action_calls: int = Field(ge=0)
    sub_lm_calls: int = Field(ge=0)
    child_root_calls: int = Field(ge=0)
    child_sub_lm_calls: int = Field(ge=0)
    parse_repairs: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    recursive_calls: int = Field(ge=0)
    child_sandboxes: int = Field(ge=0)
    delegated_context_chars: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    sandbox_acquire_ms: int | None = Field(default=None, ge=0)
    interpreter_context_ms: int | None = Field(default=None, ge=0)
    sandbox_seconds: float | None = Field(default=None, ge=0)
    terminal_status: TerminalStatus
    score: float | None = Field(default=None, ge=0, le=1)
    lifecycle: LifecycleTiming = Field(default_factory=LifecycleTiming)


class RuntimeBenchmarkReceipt(BaseModel):
    """Validated collection of results with non-secret reproducibility facts."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_id: Literal["fleet.runtime-benchmark/v1"] = Field(
        default=RUNTIME_BENCHMARK_SCHEMA,
        alias="schema",
        serialization_alias="schema",
    )
    fleet_version: str = Field(min_length=1, max_length=64)
    commit: str = Field(min_length=7, max_length=128)
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_name: str = Field(min_length=1, max_length=256)
    root_model: str = Field(min_length=1, max_length=256)
    sub_model: str = Field(min_length=1, max_length=256)
    results: tuple[RuntimeBenchmarkResult, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _results_match_receipt_models(self) -> RuntimeBenchmarkReceipt:
        if any(result.root_model != self.root_model or result.sub_model != self.sub_model for result in self.results):
            raise ValueError("benchmark result model IDs must match receipt provenance")
        return self


def aggregate_results(results: Sequence[RuntimeBenchmarkResult]) -> dict[str, int | float | None]:
    """Return only comparable aggregate counters, preserving unavailable values."""
    if not results:
        raise ValueError("runtime benchmark requires at least one result")
    numeric = (
        "turn_duration_ms",
        "root_action_calls",
        "sub_lm_calls",
        "child_root_calls",
        "child_sub_lm_calls",
        "parse_repairs",
        "tool_calls",
        "recursive_calls",
        "child_sandboxes",
        "delegated_context_chars",
    )
    aggregate: dict[str, int | float | None] = {
        field: sum(getattr(result, field) for result in results) for field in numeric
    }
    for field in ("provider_attempts", "input_tokens", "output_tokens"):
        values = [getattr(result, field) for result in results]
        aggregate[field] = None if any(value is None for value in values) else sum(values)
    scores = [result.score for result in results if result.score is not None]
    aggregate["mean_score"] = None if not scores else round(sum(scores) / len(scores), 6)
    return aggregate


def delegation_measurements(
    snapshot: object,
    *,
    delegated_context_chars: int = 0,
) -> dict[str, int | None]:
    """Project content-free internal delegation metrics into receipt counters.

    The object protocol keeps this engineering schema independent from the RLM
    implementation while making logical calls, provider attempts, parse
    repairs, recursion, and observed token totals explicitly comparable.
    """
    attempts = getattr(snapshot, "provider_attempt_counts", ())
    provider_attempts = sum(int(item[2]) for item in attempts) if attempts else None
    tokens = getattr(snapshot, "lm_token_totals", ())
    return {
        "provider_attempts": provider_attempts,
        "root_action_calls": int(getattr(snapshot, "root_lm_calls_depth_0", 0)),
        "sub_lm_calls": int(getattr(snapshot, "sub_lm_calls_depth_0", 0)),
        "child_root_calls": int(getattr(snapshot, "child_root_lm_calls_depth_1", 0)),
        "child_sub_lm_calls": int(getattr(snapshot, "child_sub_lm_calls_depth_1", 0)),
        "parse_repairs": int(getattr(snapshot, "parse_repairs", 0)),
        "recursive_calls": int(getattr(snapshot, "recursive_child_calls", 0)),
        "delegated_context_chars": max(0, int(delegated_context_chars)),
        "input_tokens": sum(int(item[2]) for item in tokens) if tokens else None,
        "output_tokens": sum(int(item[3]) for item in tokens) if tokens else None,
    }


def receipt_from_mapping(payload: Mapping[str, object]) -> RuntimeBenchmarkReceipt:
    """Strictly parse a persisted receipt before it is used as a comparison baseline."""
    return RuntimeBenchmarkReceipt.model_validate(payload)


__all__ = [
    "RUNTIME_BENCHMARK_SCHEMA",
    "LifecycleTiming",
    "RuntimeBenchmarkReceipt",
    "RuntimeBenchmarkResult",
    "aggregate_results",
    "delegation_measurements",
    "receipt_from_mapping",
]
