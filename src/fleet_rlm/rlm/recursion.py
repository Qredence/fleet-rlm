"""Bounded native DSPy recursion for the Root REPL harness.

Owns the provider-neutral child-runtime protocol, thread-safe delegation
metrics, application-loop-owned batch settlement, and the native child-RLM
executor. Root depth stays at 0, native children run at depth 1, and every
child lease is cleaned up under strict ownership.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_EXCEPTION, Future, wait
from concurrent.futures import CancelledError as FutureCancelledError
from contextvars import Context, ContextVar, copy_context
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import PurePosixPath
from threading import Event, Lock, RLock
from typing import Any, Literal, Protocol, TypeAlias

import dspy
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fleet_rlm.config.settings import Settings
from fleet_rlm.json_types import JsonValue
from fleet_rlm.observability.diagnostics import trace_failure_category
from fleet_rlm.observability.tracing import dspy_turn_callbacks, start_turn_span
from fleet_rlm.rlm.budget import BudgetDimension
from fleet_rlm.rlm.compat_3_3_1 import CodeInterpreter, _RLMTraceCallback, is_native_rlm
from fleet_rlm.rlm.events import ChildProgress, Status, ToolEventView, ToolObserver, observe_tool
from fleet_rlm.rlm.output_contract import bind_output_contract
from fleet_rlm.rlm.program import (
    FleetJSONAdapter,
    RLMModelBundle,
    RLMOptions,
    build_native_rlm,
)
from fleet_rlm.rlm.result import RLMConfigError, prediction_result, rlm_termination_mode, sanitize_public_text
from fleet_rlm.skills.models import SkillDefinition

# ---------------------------------------------------------------------------
# Provider-neutral child-runtime protocol
# ---------------------------------------------------------------------------


class ChildRuntimeCleanupError(RuntimeError):
    """A child runtime could not be proved clean before Root commit."""


class ChildRuntimeAuthorizationError(RuntimeError):
    """A child runtime operation was attempted after Run authority was revoked."""


class ChildRuntimeNotStartedError(RuntimeError):
    """The runtime refused child admission before allocating a child resource."""


class ChildRuntimeLease(Protocol):
    """A dedicated child interpreter and its strictly owned cleanup operation."""

    @property
    def interpreter(self) -> CodeInterpreter: ...

    sandbox_id: str
    volume_id: str
    volume_subpath: str

    @property
    def data_path(self) -> str: ...

    def stage_files(self, files: Mapping[str, bytes]) -> None: ...

    def read_result_files(self, paths: Sequence[str]) -> Mapping[str, bytes]: ...

    def close(self) -> None: ...


ChildRuntimeFactory = Callable[..., ChildRuntimeLease]


# ---------------------------------------------------------------------------
# Thread-safe internal delegation metrics
# ---------------------------------------------------------------------------

TokenUsageStatus: TypeAlias = Literal["observed", "unavailable"]

_TOKEN_USAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "input_tokens": ("input_tokens", "prompt_tokens"),
    "output_tokens": ("output_tokens", "completion_tokens"),
    "total_tokens": ("total_tokens",),
    "cache_read_tokens": (
        "cache_read_tokens",
        "cache_read_input_tokens",
        "prompt_cache_hit_tokens",
    ),
    "cache_creation_tokens": ("cache_creation_tokens", "cache_creation_input_tokens"),
}


@dataclass(frozen=True, slots=True)
class DelegationMetricsSnapshot:
    """Bounded, content-free delegation measurements."""

    root_lm_calls_depth_0: int = 0
    sub_lm_calls_depth_0: int = 0
    child_root_lm_calls_depth_1: int = 0
    child_sub_lm_calls_depth_1: int = 0
    recursive_child_calls: int = 0
    recursive_batch_calls: int = 0
    recursive_children_started: int = 0
    recursive_children_completed: int = 0
    peak_child_concurrency: int = 0
    delegated_input_bytes: int = 0
    lm_call_counts: tuple[tuple[str, int, int], ...] = ()
    lm_latency_ms: tuple[tuple[str, int, float], ...] = ()
    lm_token_totals: tuple[tuple[str, int, int, int, int], ...] = ()
    token_usage_status: TokenUsageStatus = "unavailable"

    def as_dict(self) -> dict[str, object]:
        return {
            "root_lm_calls_depth_0": self.root_lm_calls_depth_0,
            "sub_lm_calls_depth_0": self.sub_lm_calls_depth_0,
            "child_root_lm_calls_depth_1": self.child_root_lm_calls_depth_1,
            "child_sub_lm_calls_depth_1": self.child_sub_lm_calls_depth_1,
            "recursive_child_calls": self.recursive_child_calls,
            "recursive_batch_calls": self.recursive_batch_calls,
            "recursive_children_started": self.recursive_children_started,
            "recursive_children_completed": self.recursive_children_completed,
            "peak_child_concurrency": self.peak_child_concurrency,
            "delegated_input_bytes": self.delegated_input_bytes,
            "lm_call_counts": [
                {"role": role, "recursive_depth": depth, "count": count} for role, depth, count in self.lm_call_counts
            ],
            "lm_latency_ms": [
                {"role": role, "recursive_depth": depth, "total_ms": round(total, 3)}
                for role, depth, total in self.lm_latency_ms
            ],
            "lm_token_totals": [
                {
                    "role": role,
                    "recursive_depth": depth,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "tokens": tokens,
                }
                for role, depth, input_tokens, output_tokens, tokens in self.lm_token_totals
            ],
            "token_usage_status": self.token_usage_status,
        }


class DelegationMetrics:
    """Accumulate role/depth and bounded recursive fan-out metrics safely."""

    def __init__(self, *, parent: DelegationMetrics | None = None) -> None:
        self._parent = parent
        self._lock = Lock()
        self._lm_calls: dict[tuple[str, int], int] = {}
        self._lm_latency_ms: dict[tuple[str, int], float] = {}
        self._lm_input_tokens: dict[tuple[str, int], int] = {}
        self._lm_output_tokens: dict[tuple[str, int], int] = {}
        self._lm_tokens: dict[tuple[str, int], int] = {}
        self._lm_usage_observed: set[tuple[str, int]] = set()
        self._complete_token_fields = {"input_tokens", "output_tokens", "total_tokens"}
        self._observed_token_fields: set[str] = set()
        self._recursive_child_calls = 0
        self._recursive_batch_calls = 0
        self._recursive_children_started = 0
        self._recursive_children_completed = 0
        self._active_children = 0
        self._peak_child_concurrency = 0
        self._delegated_input_bytes = 0

    def record_lm_call(
        self,
        role: str,
        recursive_depth: int,
        *,
        duration_ms: float = 0.0,
        usage: Mapping[str, Any] | None = None,
    ) -> None:
        if self._parent is not None:
            self._parent.record_lm_call(role, recursive_depth, duration_ms=duration_ms, usage=usage)
        normalized_role = role if role in {"root", "sub"} else "unknown"
        key = (normalized_role, max(0, int(recursive_depth)))
        normalized_usage = normalize_lm_token_usage(usage)
        usage_observed = bool(normalized_usage)
        input_tokens = normalized_usage.get("input_tokens", 0)
        output_tokens = normalized_usage.get("output_tokens", 0)
        tokens = normalized_usage.get("total_tokens", 0)
        with self._lock:
            self._complete_token_fields.intersection_update(normalized_usage)
            self._observed_token_fields.update(normalized_usage)
            self._lm_calls[key] = self._lm_calls.get(key, 0) + 1
            self._lm_latency_ms[key] = self._lm_latency_ms.get(key, 0.0) + max(0.0, float(duration_ms))
            if usage_observed:
                self._lm_usage_observed.add(key)
                self._lm_input_tokens[key] = self._lm_input_tokens.get(key, 0) + input_tokens
                self._lm_output_tokens[key] = self._lm_output_tokens.get(key, 0) + output_tokens
                self._lm_tokens[key] = self._lm_tokens.get(key, 0) + tokens

    def record_recursive_call(self) -> None:
        with self._lock:
            self._recursive_child_calls += 1

    def record_recursive_batch(self) -> None:
        with self._lock:
            self._recursive_batch_calls += 1

    def record_delegated_input_bytes(self, value: int) -> None:
        if type(value) is not int or value < 0:
            raise ValueError("delegated input bytes must be a nonnegative integer")
        if self._parent is not None:
            self._parent.record_delegated_input_bytes(value)
        with self._lock:
            self._delegated_input_bytes += value

    def child_started(self) -> None:
        with self._lock:
            self._recursive_children_started += 1
            self._active_children += 1
            self._peak_child_concurrency = max(self._peak_child_concurrency, self._active_children)

    def child_completed(self) -> None:
        with self._lock:
            self._recursive_children_completed += 1
            self._active_children = max(0, self._active_children - 1)

    def snapshot(self) -> DelegationMetricsSnapshot:
        with self._lock:
            calls = tuple(sorted((role, depth, count) for (role, depth), count in self._lm_calls.items()))
            latency = tuple(sorted((role, depth, total) for (role, depth), total in self._lm_latency_ms.items()))
            token_keys = self._lm_input_tokens.keys() | self._lm_output_tokens.keys() | self._lm_tokens.keys()
            tokens = tuple(
                sorted(
                    (
                        role,
                        depth,
                        self._lm_input_tokens.get((role, depth), 0),
                        self._lm_output_tokens.get((role, depth), 0),
                        self._lm_tokens.get((role, depth), 0),
                    )
                    for (role, depth) in token_keys
                )
            )
            return DelegationMetricsSnapshot(
                root_lm_calls_depth_0=self._lm_calls.get(("root", 0), 0),
                sub_lm_calls_depth_0=self._lm_calls.get(("sub", 0), 0),
                child_root_lm_calls_depth_1=self._lm_calls.get(("root", 1), 0),
                child_sub_lm_calls_depth_1=self._lm_calls.get(("sub", 1), 0),
                recursive_child_calls=self._recursive_child_calls,
                recursive_batch_calls=self._recursive_batch_calls,
                recursive_children_started=self._recursive_children_started,
                recursive_children_completed=self._recursive_children_completed,
                peak_child_concurrency=self._peak_child_concurrency,
                delegated_input_bytes=self._delegated_input_bytes,
                lm_call_counts=calls,
                lm_latency_ms=latency,
                lm_token_totals=tokens,
                token_usage_status="observed" if self._lm_usage_observed else "unavailable",
            )


def normalize_lm_token_usage(usage: Mapping[str, Any] | None) -> dict[str, int]:
    if not isinstance(usage, Mapping):
        return {}
    normalized: dict[str, int] = {}
    for target, aliases in _TOKEN_USAGE_ALIASES.items():
        value = next(
            (
                candidate
                for alias in aliases
                if isinstance((candidate := usage.get(alias)), (int, float)) and not isinstance(candidate, bool)
            ),
            None,
        )
        if value is not None:
            normalized[target] = max(0, int(value))
    if "total_tokens" not in normalized and ("input_tokens" in normalized or "output_tokens" in normalized):
        normalized["total_tokens"] = normalized.get("input_tokens", 0) + normalized.get("output_tokens", 0)
    return normalized


# ---------------------------------------------------------------------------
# Bounded ThreadPool scheduling for reserved recursive child batches
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecursiveCallReservation:
    """One already-reserved recursive child slot with its prompt."""

    prompt: str
    call_index: int
    child_depth: int


class RecursiveBatchError(RuntimeError):
    """Bounded all-or-nothing failure for one recursive batch."""

    def __init__(self, message: str = "recursive child batch failed") -> None:
        super().__init__(message)


def run_reserved_batch(
    reservations: Sequence[RecursiveCallReservation],
    *,
    execute: Callable[[RecursiveCallReservation, Event], Any],
    deadline_monotonic: float,
    max_parallel: int,
    on_retain_running: Callable[[set[Future[Any]]], None],
    scheduler: ChildAsyncScheduler,
) -> list[Any]:
    if not reservations:
        raise ValueError("reserved batch must not be empty")
    del max_parallel
    answers: list[Any] = []
    batch_cancelled = Event()
    futures: list[Future[Any]] = []
    try:
        try:
            for reservation in reservations:
                ctx = copy_context()

                def _run(
                    reserved: RecursiveCallReservation = reservation,
                    context: Context = ctx,
                ) -> Any:
                    return context.run(execute, reserved, batch_cancelled)

                futures.append(scheduler.submit_blocking(_run))
        except BaseException:
            batch_cancelled.set()
            pending = {future for future in futures if not future.done()}
            for future in pending:
                scheduler.cancel(future)
            on_retain_running(pending)
            raise
        remaining = max(0.0, deadline_monotonic - time.monotonic())
        done, not_done = wait(futures, timeout=remaining, return_when=FIRST_EXCEPTION)
        failures = _future_failures(done)
        if failures or not_done:
            batch_cancelled.set()
            for future in not_done:
                scheduler.cancel(future)
        if not_done:
            on_retain_running(not_done)
            if failures:
                raise RecursiveBatchError() from failures[0]
            raise TimeoutError("recursive child batch deadline exceeded")
        if failures:
            raise RecursiveBatchError() from failures[0]
        answers = [future.result(timeout=0) for future in futures]
    finally:
        if batch_cancelled.is_set():
            for future in futures:
                if not future.done():
                    scheduler.cancel(future)
    return answers


def _future_failures(futures: set[Future[Any]]) -> list[BaseException]:
    failures: list[BaseException] = []
    for future in futures:
        if future.cancelled():
            continue
        try:
            failure = future.exception(timeout=0)
        except BaseException as exc:
            failures.append(exc)
        else:
            if failure is not None:
                failures.append(failure)
    return failures


# ---------------------------------------------------------------------------
# Bounded native DSPy child-RLM calls
# ---------------------------------------------------------------------------

RLM_NATIVE_CHILD_DEPTH = 1
_MAX_CHILD_RESULT_BYTES = 50_000
_MAX_CHILD_TASK_CHARS = 2_000
_MAX_CHILD_CONTEXT_CHARS = 2_000
_MAX_CHILD_PROGRESS_OUTCOME_CHARS = 240
_MAX_CHILD_INPUTS = 16
_MAX_CHILD_MANIFEST_BYTES = 64 * 1024


class ChildRequest(BaseModel):
    """Bounded model-authored investigation; source bytes are host resolved."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task: str = Field(min_length=1, max_length=_MAX_CHILD_TASK_CHARS)
    inputs: tuple[str, ...] = Field(default=(), max_length=_MAX_CHILD_INPUTS)
    context: str = Field(default="", max_length=_MAX_CHILD_CONTEXT_CHARS)

    @field_validator("inputs", mode="before")
    @classmethod
    def _normalize_inputs(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)):
            raise ValueError("child inputs must be an array of relative paths")
        return tuple(value)

    @model_validator(mode="after")
    def _validate_request(self) -> ChildRequest:
        if not self.task.strip():
            raise ValueError("child task must contain text")
        if len(self.inputs) != len(set(self.inputs)):
            raise ValueError("child inputs must be unique")
        for reference in self.inputs:
            if (
                not isinstance(reference, str)
                or not reference
                or len(reference) > 512
                or reference != reference.strip()
                or not reference.isprintable()
            ):
                raise ValueError("child input path is invalid")
            _validate_child_path(reference)
        return self

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ChildRequest:
        if not isinstance(value, Mapping):
            raise ValueError("child request must be a JSON object")
        return cls.model_validate(value)

    def render(self) -> str:
        return json.dumps(
            {"task": self.task.strip(), "inputs": list(self.inputs), "context": self.context.strip()},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def serialized_bytes(self) -> int:
        return len(self.render().encode("utf-8"))


def _validate_child_path(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or "\x00" in value
        or "\\" in value
        or ":" in value
        or "%" in value
        or "//" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("child input path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("child input path escapes its authorized scope")


def _child_progress_outcome(answer: str | None, failure_category: str | None) -> str:
    if isinstance(answer, str) and answer.strip():
        excerpt = sanitize_public_text(" ".join(answer.split()), max_len=_MAX_CHILD_PROGRESS_OUTCOME_CHARS)
        if excerpt.strip():
            return excerpt
    return failure_category or "Child answer unavailable"


ChildStatus: TypeAlias = Literal["completed", "failed", "timed_out", "cancelled", "not_started"]


class ChildUsage(BaseModel):
    """Invocation-local measurements; absent provider tokens are never zero."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    child_calls: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    token_usage_status: TokenUsageStatus = "unavailable"
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


_child_metrics: ContextVar[DelegationMetrics | None] = ContextVar("fleet_child_metrics", default=None)


def _child_usage(metrics: DelegationMetrics, *, child_calls: int = 1) -> ChildUsage:
    snapshot = metrics.snapshot()
    with metrics._lock:
        complete = metrics._complete_token_fields & metrics._observed_token_fields
    return ChildUsage(
        child_calls=child_calls,
        llm_calls=sum(count for _, _, count in snapshot.lm_call_counts),
        token_usage_status=snapshot.token_usage_status,
        input_tokens=sum(row[2] for row in snapshot.lm_token_totals) if "input_tokens" in complete else None,
        output_tokens=sum(row[3] for row in snapshot.lm_token_totals) if "output_tokens" in complete else None,
        total_tokens=sum(row[4] for row in snapshot.lm_token_totals) if "total_tokens" in complete else None,
    )


class ChildOutcome(BaseModel):
    """Bounded per-child evidence; private trajectories never cross this DTO."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: ChildStatus
    child_id: str | None = Field(default=None, max_length=200)
    termination: str | None = Field(default=None, max_length=64)
    answer: str = Field(default="", max_length=_MAX_CHILD_RESULT_BYTES)
    evidence: tuple[str, ...] = Field(default=(), max_length=32)
    gaps: tuple[str, ...] = Field(default=(), max_length=32)
    result_files: tuple[str, ...] = Field(default=(), max_length=16)
    source_manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    usage: ChildUsage = Field(default_factory=ChildUsage)
    error_category: str | None = Field(default=None, max_length=64)
    result_bytes: int = Field(default=0, ge=0)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "answer": self.answer,
            "child_id": self.child_id,
            "termination": self.termination,
            "evidence": list(self.evidence),
            "gaps": list(self.gaps),
            "result_files": list(self.result_files),
            "source_manifest_sha256": self.source_manifest_sha256,
            "usage": self.usage.model_dump(mode="json"),
            "error_category": self.error_category,
            "result_bytes": self.result_bytes,
        }


_PENDING_BATCH_WAIT_TIMEOUT_S = 60.0


class ChildAsyncScheduler:
    """Turn-owned tasks on the application loop, with bounded blocking work."""

    def __init__(self, max_workers: int = 1, *, loop: asyncio.AbstractEventLoop | None = None) -> None:
        if type(max_workers) is not int or max_workers <= 0:
            raise ValueError("child scheduler worker count must be a positive integer")
        self._loop = loop if loop is not None else asyncio.get_running_loop()
        self._lock = Lock()
        self._closed = False
        self._semaphore = asyncio.Semaphore(max_workers)
        self._tasks: dict[Future[Any], asyncio.Task[Any]] = {}
        self._cancel_requested: set[Future[Any]] = set()

    def submit(self, awaitable: Any) -> Future[Any]:
        with self._lock:
            if self._closed or not self._loop.is_running():
                if inspect.iscoroutine(awaitable):
                    awaitable.close()
                raise RuntimeError("child scheduler is unavailable")
        try:
            caller_loop = asyncio.get_running_loop()
        except RuntimeError:
            caller_loop = None
        if caller_loop is self._loop:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise RuntimeError("recursive synchronous callback cannot block its owning event loop")
        box: list[Future[Any]] = []

        async def owned() -> Any:
            task = asyncio.current_task()
            assert task is not None
            while not box:
                await asyncio.sleep(0)
            outer = box[0]
            with self._lock:
                self._tasks[outer] = task
                cancelled = outer in self._cancel_requested
            try:
                if cancelled:
                    if inspect.iscoroutine(awaitable):
                        awaitable.close()
                    raise asyncio.CancelledError
                return await awaitable
            except asyncio.CancelledError:
                raise FutureCancelledError() from None
            finally:
                with self._lock:
                    self._tasks.pop(outer, None)
                    self._cancel_requested.discard(outer)

        try:
            future = asyncio.run_coroutine_threadsafe(owned(), self._loop)
        except BaseException:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise
        box.append(future)
        return future

    def submit_blocking(self, call: Callable[[], Any]) -> Future[Any]:
        async def run() -> Any:
            async with self._semaphore:
                worker = asyncio.create_task(asyncio.to_thread(call))
                try:
                    return await asyncio.shield(worker)
                except asyncio.CancelledError:
                    while not worker.done():
                        try:
                            await asyncio.shield(worker)
                        except asyncio.CancelledError:
                            continue
                    with contextlib.suppress(BaseException):
                        worker.result()
                    raise

        return self.submit(run())

    def cancel(self, future: Future[Any]) -> bool:
        with self._lock:
            if future.done():
                return False
            task = self._tasks.get(future)
            if task is None:
                self._cancel_requested.add(future)
                return True
        self._loop.call_soon_threadsafe(task.cancel)
        return True

    def shutdown(self, *, timeout: float = 5.0) -> None:
        del timeout
        with self._lock:
            self._closed = True


@dataclass(frozen=True, slots=True)
class RecursiveRLMOptions:
    """Invocation limits for the custom recursive RLM Tool."""

    enabled: bool = False
    max_calls: int = 4
    max_prompt_chars: int = 50_000
    child_max_iters: int = 8
    child_max_llm_calls: int = 12
    child_max_output_chars: int = 4_000
    max_parallel_children: int = 1

    def __post_init__(self) -> None:
        for name, value in (
            ("max_calls", self.max_calls),
            ("max_prompt_chars", self.max_prompt_chars),
            ("child_max_iters", self.child_max_iters),
            ("child_max_llm_calls", self.child_max_llm_calls),
            ("child_max_output_chars", self.child_max_output_chars),
            ("max_parallel_children", self.max_parallel_children),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise RLMConfigError(f"{name} must be a positive integer, got {value!r}")
        if self.max_parallel_children > 8:
            raise RLMConfigError("max_parallel_children must not exceed 8")
        if self.max_parallel_children > self.max_calls:
            raise RLMConfigError("max_parallel_children must not exceed max_calls")


def recursive_rlm_options(settings: Settings) -> RecursiveRLMOptions:
    return RecursiveRLMOptions(
        enabled=settings.rlm_recursion_enabled,
        max_calls=settings.rlm_recursion_max_calls,
        max_prompt_chars=settings.rlm_recursion_max_prompt_chars,
        child_max_iters=settings.rlm_recursion_child_max_iters,
        child_max_llm_calls=settings.rlm_recursion_child_max_llm_calls,
        child_max_output_chars=settings.rlm_recursion_child_max_output_chars,
        max_parallel_children=settings.rlm_recursion_max_parallel_children,
    )


@dataclass(frozen=True, slots=True)
class RecursiveCallSummary:
    """Bounded aggregate evidence for one Root invocation."""

    call_count: int
    delegated_prompt_chars: int
    maximum_prompt_chars: int
    child_iterations: int
    termination_modes: tuple[str, ...]
    recursive_batch_calls: int = 0
    recursive_children_started: int = 0
    recursive_children_completed: int = 0
    peak_child_concurrency: int = 0
    delegation_metrics: DelegationMetricsSnapshot = field(default_factory=DelegationMetricsSnapshot)

    @classmethod
    def from_snapshot(
        cls,
        snapshot: DelegationMetricsSnapshot,
        *,
        call_count: int = 0,
        delegated_prompt_chars: int = 0,
        maximum_prompt_chars: int = 0,
        child_iterations: int = 0,
        termination_modes: tuple[str, ...] = (),
    ) -> RecursiveCallSummary:
        return cls(
            call_count,
            delegated_prompt_chars,
            maximum_prompt_chars,
            child_iterations,
            termination_modes,
            recursive_batch_calls=snapshot.recursive_batch_calls,
            recursive_children_started=snapshot.recursive_children_started,
            recursive_children_completed=snapshot.recursive_children_completed,
            peak_child_concurrency=snapshot.peak_child_concurrency,
            delegation_metrics=snapshot,
        )


@dataclass(slots=True)
class _RecursiveState:
    lock: RLock = field(default_factory=RLock, repr=False)
    reserved_call_count: int = 0
    delegated_prompt_chars: int = 0
    maximum_prompt_chars: int = 0
    child_iterations: int = 0
    termination_modes: list[str] = field(default_factory=list)
    fatal_cleanup_error: BaseException | None = None
    pending_batch_futures: list[Future[Any]] = field(default_factory=list, repr=False)
    metrics: DelegationMetrics = field(default_factory=DelegationMetrics, repr=False)


class RecursiveSubtaskSignature(dspy.Signature):
    """Investigate staged local inputs and submit bounded findings.

    The host copies each requested relative input path under
    ``FLEET_RUN_SCRATCH`` and supplies a source manifest with the SHA-256 of
    each exact copy. Read inputs with ``open(os.path.join(FLEET_RUN_SCRATCH,
    path), encoding="utf-8")`` and cite useful locations with their manifest
    revision. Write declared result files beneath the same scratch directory.
    Do not assume the root's workspace or sandbox paths exist in this child.
    """

    prompt: str = dspy.InputField(
        desc=(
            "One bounded subproblem containing task, optional context, and a manifest of staged paths and hashes. "
            "Use hashes as source revisions; keep intermediate Python small and check evidence before submitting."
        )
    )
    answer: str = dspy.OutputField(desc="A concise finding; Root verifies it against evidence")
    evidence: list[str] = dspy.OutputField(desc="Source locations or revisions supporting the finding")
    gaps: list[str] = dspy.OutputField(desc="Missing inputs or unresolved questions")
    result_files: list[str] = dspy.OutputField(desc="Optional relative paths of useful child-local output files")


_MAX_PROGRESS_INTEGER = 1_000_000
_MAX_PROGRESS_DURATION_MS = 86_400_000


def _bounded_progress_integer(value: int) -> int:
    return max(0, min(int(value), _MAX_PROGRESS_INTEGER))


def _elapsed_ms(started_at: float) -> int:
    return min(_MAX_PROGRESS_DURATION_MS, max(0, int((time.monotonic() - started_at) * 1000)))


def _recursive_failure_category(exc: BaseException) -> str:
    if isinstance(exc, ChildRuntimeNotStartedError):
        return "capacity"
    if isinstance(exc, ChildRuntimeAuthorizationError):
        return "unauthorized"
    if isinstance(exc, ChildRuntimeCleanupError):
        return "cleanup_failed"
    category = trace_failure_category(exc)
    return category if category in {"timeout", "unauthorized", "cleanup_failed", "wrap_up_rejected"} else "child_failed"


def _as_cleanup_error(exc: BaseException) -> ChildRuntimeCleanupError:
    if isinstance(exc, ChildRuntimeCleanupError):
        return exc
    error = ChildRuntimeCleanupError("recursive child cleanup failed")
    error.__cause__ = exc
    return error


def _validate_recursive_prompt(prompt: object, *, max_chars: int) -> str:
    if not isinstance(prompt, str):
        raise ValueError("rlm_query prompt must be text")
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("rlm_query prompt must not be empty")
    if len(prompt) > max_chars:
        raise ValueError("rlm_query prompt exceeds the recursive prompt bound")
    return prompt


def _child_source_manifest(files: Mapping[str, bytes]) -> tuple[list[dict[str, str]], str]:
    manifest = [{"path": path, "sha256": sha256(content).hexdigest()} for path, content in sorted(files.items())]
    encoded = json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_CHILD_MANIFEST_BYTES:
        raise ValueError("selected child source manifest exceeds its metadata bound")
    return manifest, sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class _RecursiveCall:
    call_index: int
    child_depth: int
    started_at: float
    span: Any
    task_label: str


class RecursiveRLMExecutor:
    """Execute bounded recursive child RLMs from a synchronous DSPy worker."""

    def __init__(
        self,
        *,
        models: RLMModelBundle,
        options: RecursiveRLMOptions,
        child_runtime_factory: ChildRuntimeFactory | None,
        deadline: float,
        metrics: DelegationMetrics | None = None,
        observer: ToolObserver | None = None,
        is_authorized: Callable[[], bool] | None = None,
        scheduler: ChildAsyncScheduler | None = None,
        input_materializer: Callable[[ChildRequest], Mapping[str, bytes]] | None = None,
        result_writer: Callable[[int, str, bytes], str] | None = None,
        parent_run_id: str | None = None,
        loaded_skills: Callable[[], tuple[SkillDefinition, ...]] | None = None,
    ) -> None:
        self._models = models
        self._options = options
        self._child_runtime_factory = child_runtime_factory
        self._deadline = deadline
        self._state = _RecursiveState()
        if metrics is not None:
            self._state.metrics = metrics
        self._metrics = self._state.metrics
        self._observer = observer
        self._is_authorized = is_authorized
        self._input_materializer = input_materializer
        self._result_writer = result_writer
        self._parent_run_id = parent_run_id
        self._loaded_skills = loaded_skills or (lambda: ())
        self._owns_scheduler = scheduler is None
        self._last_completion: Mapping[str, JsonValue] | None = None
        self._last_child_outcomes: tuple[ChildOutcome, ...] = ()
        raw_tool = dspy.Tool(
            self._call_child,
            name="rlm_query",
            desc=(
                "Investigate one bounded task using staged relative input paths. "
                "Pass task, inputs, and optional context."
            ),
        )
        if observer is not None or is_authorized is not None:
            self._tool = observe_tool(
                raw_tool,
                observer or (lambda _detail: None),
                ToolEventView(input_projection=self._child_input, output_projection=self._recursive_output),
                is_authorized=is_authorized,
            )
        else:
            self._tool = raw_tool
        raw_batch_tool = dspy.Tool(
            self._call_children_batched,
            name="rlm_query_batched",
            desc=(
                "Solve multiple independent bounded subproblems with isolated child RLMs. "
                "Pass tasks containing task, inputs, and optional context; results stay in input order. "
                "Use only when every item needs iterative exploration. Root only."
            ),
        )
        if observer is not None or is_authorized is not None:
            self._batched_tool = observe_tool(
                raw_batch_tool,
                observer or (lambda _detail: None),
                ToolEventView(
                    input_projection=self._child_batch_input,
                    output_projection=self._recursive_batch_output,
                ),
                is_authorized=is_authorized,
            )
        else:
            self._batched_tool = raw_batch_tool
        self._scheduler = scheduler or ChildAsyncScheduler(max_workers=options.max_parallel_children)

    @property
    def tool(self) -> dspy.Tool:
        return self._tool

    @property
    def batched_tool(self) -> dspy.Tool:
        return self._batched_tool

    @property
    def last_child_outcomes(self) -> tuple[ChildOutcome, ...]:
        return self._last_child_outcomes

    @property
    def metrics(self) -> DelegationMetrics:
        return self._metrics

    def summary(self) -> RecursiveCallSummary:
        with self._state.lock:
            return RecursiveCallSummary.from_snapshot(
                self._metrics.snapshot(),
                call_count=self._state.reserved_call_count,
                delegated_prompt_chars=self._state.delegated_prompt_chars,
                maximum_prompt_chars=self._state.maximum_prompt_chars,
                child_iterations=self._state.child_iterations,
                termination_modes=tuple(self._state.termination_modes),
            )

    def _call_child(self, task: str, inputs: list[str], context: str = "") -> dict[str, object]:
        request = ChildRequest(task=task, inputs=tuple(inputs), context=context)
        return self._execute_child(request, classify_failures=True).as_dict()

    def _execute_child(self, request: ChildRequest, *, classify_failures: bool) -> ChildOutcome:
        rendered = _validate_recursive_prompt(request.render(), max_chars=self._options.max_prompt_chars)
        staged_files = self._materialize_inputs(request)
        source_manifest, source_manifest_sha256 = _child_source_manifest(staged_files)
        self._ensure_authorized()
        self._ensure_no_pending_batch_workers()
        local_metrics = DelegationMetrics(parent=self._metrics)
        token = _child_metrics.set(local_metrics)
        try:
            reservation = self._begin_call(rendered)
            future = self._scheduler.submit_blocking(
                lambda: self._run_reserved_call(
                    reservation,
                    request=request,
                    staged_files=staged_files,
                    source_manifest=source_manifest,
                    source_manifest_sha256=source_manifest_sha256,
                )
            )
            try:
                outcome = future.result(timeout=max(0.0, self._deadline - time.monotonic()))
            except TimeoutError:
                if not future.done():
                    self._scheduler.cancel(future)
                    self._retain_pending_batch_futures({future})
                raise TimeoutError("recursive child deadline exceeded") from None
        except (asyncio.CancelledError, FutureCancelledError, ChildRuntimeAuthorizationError, ChildRuntimeCleanupError):
            raise
        except Exception as exc:
            if not classify_failures:
                raise
            outcome = ChildOutcome(
                status="timed_out" if isinstance(exc, TimeoutError) else "failed",
                source_manifest_sha256=source_manifest_sha256,
                error_category=_recursive_failure_category(exc),
                usage=_child_usage(local_metrics),
            )
        finally:
            _child_metrics.reset(token)
        return outcome.model_copy(update={"usage": _child_usage(local_metrics)})

    def _materialize_inputs(self, request: ChildRequest) -> Mapping[str, bytes]:
        self._ensure_authorized()
        if self._input_materializer is None:
            if request.inputs:
                raise ChildRuntimeAuthorizationError("selected child inputs are unavailable")
            return {}
        files = self._input_materializer(request)
        if not isinstance(files, Mapping):
            raise ValueError("child input materializer returned invalid files")
        normalized: dict[str, bytes] = {}
        for path, content in files.items():
            _validate_child_path(path)
            if not isinstance(content, bytes):
                raise ValueError("child input materializer must return bytes")
            if not any(path == scope or path.startswith(f"{scope.rstrip('/')}/") for scope in request.inputs):
                raise ChildRuntimeAuthorizationError("child input materializer exceeded the requested scope")
            normalized[path] = content
        return normalized

    @staticmethod
    def _child_input(arguments: Mapping[str, Any]) -> dict[str, int]:
        return {"input_count": len(arguments.get("inputs", [])) if isinstance(arguments.get("inputs"), list) else 0}

    def _call_children_batched(self, tasks: list[Mapping[str, object]]) -> list[dict[str, object]]:
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("rlm_query_batched tasks must be a non-empty list")
        normalized = tuple(ChildRequest.from_mapping(item) for item in tasks)
        rendered = tuple(
            _validate_recursive_prompt(req.render(), max_chars=self._options.max_prompt_chars) for req in normalized
        )
        staged = tuple(self._materialize_inputs(req) for req in normalized)
        manifests = tuple(_child_source_manifest(files) for files in staged)
        if time.monotonic() >= self._deadline:
            raise TimeoutError("recursive call deadline exceeded")
        self._ensure_authorized()
        self._ensure_no_pending_batch_workers()
        reservations = self._begin_batch(rendered)
        self._metrics.record_recursive_batch()

        def execute(
            reservation: RecursiveCallReservation,
            batch_cancelled: Event,
        ) -> ChildOutcome:
            index = reservation.call_index - reservations[0].call_index
            local_metrics = DelegationMetrics(parent=self._metrics)
            token = _child_metrics.set(local_metrics)
            try:
                outcome = self._run_reserved_call(
                    reservation,
                    batch_cancelled,
                    request=normalized[index],
                    staged_files=staged[index],
                    source_manifest=manifests[index][0],
                    source_manifest_sha256=manifests[index][1],
                )
            except (asyncio.CancelledError, FutureCancelledError):
                raise
            except (ChildRuntimeAuthorizationError, ChildRuntimeCleanupError):
                raise
            except TimeoutError as exc:
                category = _recursive_failure_category(exc)
                return ChildOutcome(
                    status="timed_out",
                    source_manifest_sha256=manifests[index][1],
                    error_category=category,
                    usage=_child_usage(local_metrics),
                )
            except Exception as exc:
                return ChildOutcome(
                    status="failed",
                    source_manifest_sha256=manifests[index][1],
                    error_category=_recursive_failure_category(exc),
                    usage=_child_usage(local_metrics),
                )
            finally:
                _child_metrics.reset(token)
            return outcome.model_copy(update={"usage": _child_usage(local_metrics)})

        outcomes = run_reserved_batch(
            reservations,
            execute=execute,
            deadline_monotonic=self._deadline,
            max_parallel=self._options.max_parallel_children,
            on_retain_running=self._retain_pending_batch_futures,
            scheduler=self._scheduler,
        )
        self._ensure_authorized()
        self._last_child_outcomes = tuple(outcomes)
        self.raise_if_cleanup_failed()
        return [outcome.as_dict() for outcome in outcomes]

    def raise_if_cleanup_failed(self) -> None:
        with self._state.lock:
            fatal_cleanup_error = self._state.fatal_cleanup_error
            cleanup_pending = any(not future.done() for future in self._state.pending_batch_futures)
        if fatal_cleanup_error is not None:
            raise ChildRuntimeCleanupError("recursive child cleanup failed") from fatal_cleanup_error
        if cleanup_pending:
            raise ChildRuntimeCleanupError("recursive child cleanup is still pending")
        factory_check = getattr(self._child_runtime_factory, "raise_if_cleanup_failed", None)
        if callable(factory_check):
            factory_check()

    def wait_owned(self) -> None:
        try:
            wait_deadline = time.monotonic() + _PENDING_BATCH_WAIT_TIMEOUT_S
            while True:
                with self._state.lock:
                    pending = tuple(future for future in self._state.pending_batch_futures if not future.done())
                if not pending:
                    break
                remaining = max(0.0, wait_deadline - time.monotonic())
                _, still_pending = wait(pending, timeout=remaining)
                if not still_pending:
                    continue
                with self._state.lock:
                    if self._state.fatal_cleanup_error is None:
                        self._state.fatal_cleanup_error = TimeoutError("recursive child worker quarantine timed out")
                raise ChildRuntimeCleanupError("recursive child cleanup failed") from self._state.fatal_cleanup_error

            factory_wait_owned = getattr(self._child_runtime_factory, "wait_owned", None)
            if callable(factory_wait_owned):
                factory_wait_owned()
        finally:
            if self._owns_scheduler:
                self._scheduler.shutdown()

    def _retain_pending_batch_futures(self, futures: set[Future[Any]]) -> None:
        pending = [future for future in futures if not future.done()]
        if not pending:
            return
        with self._state.lock:
            self._state.pending_batch_futures.extend(pending)

        def settled(future: Future[Any]) -> None:
            if not future.cancelled():
                with contextlib.suppress(BaseException):
                    future.exception()
            with self._state.lock:
                if future in self._state.pending_batch_futures:
                    self._state.pending_batch_futures.remove(future)

        for future in pending:
            future.add_done_callback(settled)

    def _recursive_output(self, result: Any) -> JsonValue:
        if isinstance(result, dict) and result.get("status") in {"failed", "timed_out", "cancelled"}:
            return {"status": result["status"], "error_category": result.get("error_category")}
        if self._last_completion is None:
            return {"status": "completed"}
        return dict(self._last_completion)

    @staticmethod
    def _child_batch_input(arguments: Mapping[str, Any]) -> dict[str, int]:
        tasks = arguments.get("tasks")
        return {"task_count": len(tasks) if isinstance(tasks, list) else 0}

    def _recursive_batch_output(self, result: Any) -> JsonValue:
        if isinstance(result, list):
            return {
                "status": "completed",
                "answer_count": len(result),
                "peak_child_concurrency": self._metrics.snapshot().peak_child_concurrency,
            }
        return {"status": "completed"}

    def _ensure_authorized(self) -> None:
        if self._is_authorized is not None and not self._is_authorized():
            raise ChildRuntimeAuthorizationError("Turn is no longer authorized")
        if time.monotonic() >= self._deadline:
            raise TimeoutError("recursive child deadline exceeded")

    def _ensure_call_authorized(self, batch_cancelled: Event | None) -> None:
        if batch_cancelled is not None and batch_cancelled.is_set():
            raise ChildRuntimeAuthorizationError("recursive child batch is no longer authorized")
        self._ensure_authorized()

    def _ensure_no_pending_batch_workers(self) -> None:
        with self._state.lock:
            fatal_cleanup_error = self._state.fatal_cleanup_error
            pending = any(not future.done() for future in self._state.pending_batch_futures)
        if fatal_cleanup_error is not None:
            raise ChildRuntimeCleanupError("recursive child cleanup failed") from fatal_cleanup_error
        if pending:
            raise ChildRuntimeCleanupError("recursive child cleanup is still pending")

    def _emit_progress(
        self,
        status: str,
        *,
        call_index: int,
        recursive_depth: int,
        started_at: float,
        cleanup_status: str | None = None,
        failure_category: str | None = None,
        task_label: str = "Child investigation",
        child_answer: str | None = None,
        child_evidence: tuple[str, ...] = (),
        child_gaps: tuple[str, ...] = (),
    ) -> None:
        if self._observer is None:
            return
        duration_ms = 0
        if status == "child_started":
            message = (
                f"call_index={_bounded_progress_integer(call_index)} "
                f"recursive_depth={_bounded_progress_integer(recursive_depth)}"
            )
        else:
            duration_ms = min(
                _MAX_PROGRESS_DURATION_MS,
                max(0, int((time.monotonic() - started_at) * 1000)),
            )
            message = (
                f"call_index={_bounded_progress_integer(call_index)} "
                f"recursive_depth={_bounded_progress_integer(recursive_depth)} "
                f"duration_ms={duration_ms} cleanup_status={cleanup_status or 'not_required'}"
            )
            if failure_category is not None:
                message += f" failure_category={failure_category}"
        if status == "child_started":
            state = "running"
        elif status == "child_not_started":
            state = "not_started"
        elif failure_category == "timeout":
            state = "timed_out"
        elif status == "child_failed":
            state = "failed"
        else:
            state = "completed"
        if status == "child_not_started":
            cleanup_state = "not_required"
        elif cleanup_status == "completed":
            cleanup_state = "complete"
        elif cleanup_status == "failed":
            cleanup_state = "failed"
        elif cleanup_status in {"acquired", "pending"} or status == "child_started":
            cleanup_state = "pending"
        else:
            cleanup_state = "not_required"
        child = ChildProgress(
            child_id=f"child-{_bounded_progress_integer(call_index)}",
            task_label=task_label,
            state=state,
            elapsed_ms=duration_ms,
            outcome=_child_progress_outcome(child_answer, failure_category) if status != "child_started" else None,
            evidence=tuple(sanitize_public_text(item, max_len=200) for item in child_evidence[:8]),
            gaps=tuple(sanitize_public_text(item, max_len=200) for item in child_gaps[:8]),
            cleanup_state=cleanup_state,
            parent_run_id=self._parent_run_id,
        )
        for detail in (Status("recursive", status, message), child):
            try:
                self._observer(detail)
            except Exception:
                continue

    def _begin_call(self, prompt: str) -> RecursiveCallReservation:
        return self._make_reservation(prompt, self._reserve_call_indexes((prompt,))[0])

    def _begin_batch(self, prompts: tuple[str, ...]) -> tuple[RecursiveCallReservation, ...]:
        indexes = self._reserve_call_indexes(prompts)
        return tuple(self._make_reservation(prompt, index) for prompt, index in zip(prompts, indexes, strict=True))

    def _reserve_call_indexes(self, prompts: tuple[str, ...]) -> tuple[int, ...]:
        if not prompts:
            return ()
        with self._state.lock:
            if self._state.reserved_call_count + len(prompts) > self._options.max_calls:
                raise RuntimeError("recursive call budget exhausted")
            turn_budget = self._models.budget
            if turn_budget is not None:
                turn_budget.reserve(BudgetDimension.TOOL_CALLS, len(prompts))
            start = self._state.reserved_call_count + 1
            self._state.reserved_call_count += len(prompts)
            self._state.delegated_prompt_chars += sum(len(prompt) for prompt in prompts)
            self._state.maximum_prompt_chars = max(
                self._state.maximum_prompt_chars,
                *(len(prompt) for prompt in prompts),
            )
        for _ in prompts:
            self._metrics.record_recursive_call()
        return tuple(range(start, start + len(prompts)))

    def _make_reservation(self, prompt: str, call_index: int) -> RecursiveCallReservation:
        return RecursiveCallReservation(prompt=prompt, call_index=call_index, child_depth=RLM_NATIVE_CHILD_DEPTH)

    @staticmethod
    def _start_call(reservation: RecursiveCallReservation) -> _RecursiveCall:
        started_at = time.monotonic()
        inputs = {
            "recursive_depth": reservation.child_depth,
            "call_index": reservation.call_index,
            "prompt_chars": len(reservation.prompt),
        }
        try:
            span = start_turn_span(
                "RLM.recursive_call",
                span_type="AGENT",
                inputs=inputs,
            )
        except TypeError:
            span = start_turn_span(
                "RLM.recursive_call",
                inputs=inputs,
            )
        try:
            payload = json.loads(reservation.prompt)
            raw_label = payload.get("task") if isinstance(payload, dict) else None
        except (TypeError, ValueError):
            raw_label = None
        task_label = (
            sanitize_public_text(" ".join(raw_label.split()), max_len=120)
            if isinstance(raw_label, str) and raw_label.strip()
            else "Child investigation"
        )
        return _RecursiveCall(reservation.call_index, reservation.child_depth, started_at, span, task_label)

    def _acquire_child_lease(self, call_index: int, *, profile: str) -> ChildRuntimeLease:
        if self._child_runtime_factory is None:
            raise RuntimeError("recursive child runtime is unavailable")
        self._ensure_authorized()
        factory = self._child_runtime_factory
        return factory(call_index, profile=profile)

    def _run_native_child(
        self,
        request: ChildRequest,
        call: _RecursiveCall,
        lease: ChildRuntimeLease,
        skills: tuple[SkillDefinition, ...],
        staged_files: Mapping[str, bytes],
        source_manifest: list[dict[str, str]],
        source_manifest_sha256: str,
        batch_cancelled: Event | None = None,
    ) -> tuple[ChildOutcome, dict[str, object]]:
        self._ensure_call_authorized(batch_cancelled)
        if time.monotonic() >= self._deadline:
            raise TimeoutError("recursive child deadline exceeded")
        child_models = self._models.fork_for_child(deadline=self._deadline)

        def invocation_factory() -> CodeInterpreter:
            new_invocation = getattr(lease.interpreter, "new_invocation", None)
            if not callable(new_invocation):
                raise RLMConfigError("recursive child requires an invocation-scoped interpreter factory")
            interpreter = new_invocation(turn_budget=child_models.budget, turn_request=None)
            bind_output_contract(interpreter, RecursiveSubtaskSignature)
            if self._parent_run_id is not None:
                bind_scratch = getattr(interpreter, "bind_run_scratch", None)
                if not callable(bind_scratch):
                    raise RLMConfigError("recursive child cannot bind its private scratch")
                bind_scratch(self._parent_run_id, call_index=call.call_index)
            return interpreter

        child_prompt_payload = json.loads(request.render())
        child_prompt_payload["source_manifest"] = source_manifest
        child_prompt = json.dumps(
            child_prompt_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(child_prompt.encode("utf-8")) > self._options.max_prompt_chars + _MAX_CHILD_MANIFEST_BYTES:
            raise ValueError("child task and source manifest exceed their independent metadata bounds")
        child = build_native_rlm(
            signature=RecursiveSubtaskSignature,
            options=RLMOptions(
                max_iters=self._options.child_max_iters,
                max_llm_calls=self._options.child_max_llm_calls,
                max_output_chars=self._options.child_max_output_chars,
            ),
            tools=[],
            sub_lm=child_models.sub_lm,
            skill_instructions=tuple(skill.instructions for skill in skills),
            interpreter_factory=invocation_factory,
            verbose=False,
        )
        self._ensure_call_authorized(batch_cancelled)
        with dspy.context(
            lm=child_models.root_lm,
            adapter=FleetJSONAdapter(
                deadline=self._deadline,
                wrap_up_seconds=child_models.reserve_seconds,
                budget=child_models.budget,
            ),
            callbacks=dspy_turn_callbacks(
                _RLMTraceCallback(
                    root_lm=child_models.root_lm,
                    sub_lm=child_models.sub_lm,
                    recursive_depth=call.child_depth,
                    metrics=_child_metrics.get() or self._metrics,
                    deadline=self._deadline,
                )
            ),
            track_usage=True,
        ):
            if not is_native_rlm(child):
                raise RLMConfigError("recursive child program is not a native DSPy RLM")
            skill_versions = [f"{skill.card.id}:{skill.card.version}" for skill in skills]
            invocation_span = start_turn_span(
                "RLM.child.invoke",
                span_type="CHAIN",
                inputs={
                    "call_index": call.call_index,
                    "recursive_depth": call.child_depth,
                    "source_file_count": len(staged_files),
                    "source_bytes": sum(map(len, staged_files.values())),
                    "source_manifest_sha256": source_manifest_sha256,
                    "skill_versions": skill_versions,
                },
            )
            invocation_started_at = time.monotonic()
            try:
                prediction = child(prompt=child_prompt)
            except BaseException as exc:
                invocation_span.finish(
                    phase_status="failed",
                    outputs={
                        "duration_ms": _elapsed_ms(invocation_started_at),
                        "failure_category": _recursive_failure_category(exc),
                    },
                )
                raise
            else:
                invocation_span.finish(
                    phase_status="completed",
                    outputs={"duration_ms": _elapsed_ms(invocation_started_at)},
                )
        result = prediction_result(
            prediction,
            RecursiveSubtaskSignature,
            schema_id="fleet.recursive-subtask",
            schema_version="1",
            max_output_chars=self._options.child_max_output_chars,
        )
        self._ensure_call_authorized(batch_cancelled)
        trajectory = getattr(prediction, "trajectory", ())
        child_iterations = len(trajectory) if isinstance(trajectory, list) else 0
        mode = rlm_termination_mode(prediction)
        completion_outputs = self._record_completion(call, mode=mode, child_iterations=child_iterations)
        answer = result.outputs.get("answer")
        evidence = result.outputs.get("evidence", [])
        gaps = result.outputs.get("gaps", [])
        result_paths = result.outputs.get("result_files", [])
        if (
            not isinstance(answer, str)
            or not isinstance(evidence, (list, tuple))
            or not isinstance(gaps, (list, tuple))
        ):
            raise RLMConfigError("child result contract is invalid")
        if not isinstance(result_paths, (list, tuple)) or len(result_paths) > 16:
            raise RLMConfigError("child result files are invalid")
        if any(not isinstance(item, str) or len(item) > 512 for item in (*evidence, *gaps)):
            raise RLMConfigError("child evidence contract is invalid")
        evidence_items = tuple(item for item in evidence if isinstance(item, str))
        gap_items = tuple(item for item in gaps if isinstance(item, str))
        paths = tuple(item for item in result_paths if isinstance(item, str))
        if len(paths) != len(result_paths):
            raise RLMConfigError("child result files must be text paths")
        if len(paths) != len(set(paths)):
            raise RLMConfigError("child result files must be unique")
        for path in paths:
            _validate_child_path(path)
        files: Mapping[str, bytes] = {}
        harvest_span = start_turn_span(
            "RLM.child.result_harvest",
            inputs={"call_index": call.call_index, "declared_file_count": len(paths)},
        )
        harvest_started_at = time.monotonic()
        try:
            if paths:
                files = lease.read_result_files(paths)
                if set(files) != set(paths) or any(not isinstance(data, bytes) for data in files.values()):
                    raise RLMConfigError("child runtime returned invalid result files")
            harvest_span.finish(
                phase_status="completed",
                outputs={
                    "duration_ms": _elapsed_ms(harvest_started_at),
                    "file_count": len(files),
                    "result_file_bytes": sum(map(len, files.values())),
                },
            )
        except BaseException as exc:
            harvest_span.finish(
                phase_status="failed",
                outputs={
                    "duration_ms": _elapsed_ms(harvest_started_at),
                    "failure_category": _recursive_failure_category(exc),
                },
            )
            raise
        total_result_bytes = len(result.display_text.encode("utf-8")) + sum(map(len, files.values()))
        if total_result_bytes > _MAX_CHILD_RESULT_BYTES:
            raise RLMConfigError("child result exceeds configured bound")
        references: list[str] = []
        if files:
            if self._result_writer is None:
                raise RLMConfigError("parent Run result persistence is unavailable")
            persist_span = start_turn_span(
                "RLM.child.result_persist",
                inputs={"call_index": call.call_index, "file_count": len(paths)},
            )
            persist_started_at = time.monotonic()
            try:
                for path in paths:
                    self._ensure_call_authorized(batch_cancelled)
                    references.append(self._result_writer(call.call_index, path, files[path]))
                persist_span.finish(
                    phase_status="completed",
                    outputs={
                        "duration_ms": _elapsed_ms(persist_started_at),
                        "file_count": len(references),
                        "result_file_bytes": sum(map(len, files.values())),
                    },
                )
            except BaseException as exc:
                persist_span.finish(
                    phase_status="failed",
                    outputs={
                        "duration_ms": _elapsed_ms(persist_started_at),
                        "failure_category": _recursive_failure_category(exc),
                    },
                )
                raise
        return ChildOutcome(
            status="completed",
            child_id=getattr(lease, "sandbox_id", None),
            termination=mode,
            answer=answer,
            evidence=evidence_items,
            gaps=gap_items,
            result_files=tuple(references),
            source_manifest_sha256=source_manifest_sha256,
            usage=_child_usage(_child_metrics.get() or self._metrics),
            result_bytes=total_result_bytes,
        ), completion_outputs

    def _record_completion(
        self,
        call: _RecursiveCall,
        *,
        mode: str,
        child_iterations: int,
        include_child_iterations: bool = True,
    ) -> dict[str, object]:
        with self._state.lock:
            self._state.child_iterations += child_iterations
            self._state.termination_modes.append(mode)
        completion_outputs: dict[str, object] = {"termination_mode": mode}
        if include_child_iterations:
            completion_outputs["child_iterations"] = child_iterations
        self._last_completion = {
            "status": "completed",
            "call_index": call.call_index,
            "recursive_depth": call.child_depth,
            "child_iterations": child_iterations,
            "termination_mode": mode,
        }
        return completion_outputs

    def _record_primary_failure(self, call: _RecursiveCall, exc: BaseException) -> str:
        failure_category = _recursive_failure_category(exc)
        if isinstance(exc, ChildRuntimeCleanupError):
            with self._state.lock:
                if self._state.fatal_cleanup_error is None:
                    self._state.fatal_cleanup_error = exc
        with self._state.lock:
            self._state.termination_modes.append("child_error")
        call.span.finish(
            phase_status="failed",
            outputs={"failure_category": trace_failure_category(exc)},
        )
        return failure_category

    def _finalize_call(
        self,
        call: _RecursiveCall,
        lease: ChildRuntimeLease | None,
        *,
        cleanup_status: str,
        failed: bool,
        primary_failed: bool,
        completion_outputs: dict[str, object] | None,
        failure_category: str | None,
        child_answer: str | None,
        child_evidence: tuple[str, ...],
        child_gaps: tuple[str, ...],
    ) -> None:
        cleanup_error: BaseException | None = None
        if lease is not None:
            cleanup_span = start_turn_span(
                "RLM.child.cleanup",
                inputs={"call_index": call.call_index, "recursive_depth": call.child_depth},
            )
            cleanup_started_at = time.monotonic()
            try:
                lease.close()
                cleanup_status = "completed"
            except BaseException as exc:
                cleanup_error = _as_cleanup_error(exc)
                cleanup_status = "failed"
                cleanup_span.finish(
                    phase_status="failed",
                    outputs={
                        "duration_ms": _elapsed_ms(cleanup_started_at),
                        "failure_category": "cleanup_failed",
                    },
                )
                with self._state.lock:
                    if self._state.fatal_cleanup_error is None:
                        self._state.fatal_cleanup_error = cleanup_error
            else:
                cleanup_span.finish(
                    phase_status="completed",
                    outputs={"duration_ms": _elapsed_ms(cleanup_started_at), "status": "confirmed"},
                )
        if cleanup_error is not None and not primary_failed:
            failed = True
            failure_category = "cleanup_failed"
            call.span.finish(
                phase_status="failed",
                outputs={"failure_category": failure_category},
            )
        elif not failed and completion_outputs is not None:
            call.span.finish(phase_status="completed", outputs=completion_outputs)
        if completion_outputs is not None and completion_outputs.get("status") == "not_started":
            progress_status = "child_not_started"
        elif failed:
            progress_status = "child_failed"
        else:
            progress_status = "child_completed"
        self._emit_progress(
            progress_status,
            call_index=call.call_index,
            recursive_depth=call.child_depth,
            started_at=call.started_at,
            cleanup_status=cleanup_status,
            failure_category=(failure_category if failed or progress_status == "child_not_started" else None),
            task_label=call.task_label,
            child_answer=child_answer,
            child_evidence=child_evidence if not failed and cleanup_error is None else (),
            child_gaps=child_gaps if not failed and cleanup_error is None else (),
        )
        if cleanup_error is not None and not primary_failed:
            raise cleanup_error

    def _run_reserved_call(
        self,
        reservation: RecursiveCallReservation,
        batch_cancelled: Event | None = None,
        *,
        request: ChildRequest,
        staged_files: Mapping[str, bytes],
        source_manifest: list[dict[str, str]],
        source_manifest_sha256: str,
    ) -> ChildOutcome:
        if time.monotonic() >= self._deadline:
            raise TimeoutError("recursive child deadline exceeded")
        call = self._start_call(reservation)
        lease: ChildRuntimeLease | None = None
        failed = False
        completion_outputs: dict[str, object] | None = None
        cleanup_status = "not_required"
        failure_category: str | None = None
        child_answer: str | None = None
        child_evidence: tuple[str, ...] = ()
        child_gaps: tuple[str, ...] = ()
        primary_failed = False
        child_started = False
        child_budget_reserved = False
        try:
            self._ensure_call_authorized(batch_cancelled)
            skills = self._loaded_skills()
            if len(skills) > 4 or any(not isinstance(skill, SkillDefinition) for skill in skills):
                raise RLMConfigError("loaded child Skill snapshot is invalid")
            child_files = dict(staged_files)
            for skill in skills:
                for resource in skill.resources.values():
                    path = f"skills/{skill.card.name}/{resource.path}"
                    _validate_child_path(path)
                    if path in child_files:
                        raise ChildRuntimeAuthorizationError("child Skill resource conflicts with selected input")
                    child_files[path] = resource.content.encode("utf-8")
            if sum(len(content) for content in child_files.values()) > 64 * 1024 * 1024:
                raise RLMConfigError("selected child inputs exceed the staging limit")
            source_manifest, source_manifest_sha256 = _child_source_manifest(child_files)
            call_span_outputs = getattr(call.span, "set_outputs", None)
            if callable(call_span_outputs):
                call_span_outputs(
                    {
                        "source_file_count": len(child_files),
                        "source_bytes": sum(map(len, child_files.values())),
                        "source_manifest_sha256": source_manifest_sha256,
                        "skill_versions": [f"{skill.card.id}:{skill.card.version}" for skill in skills],
                    }
                )
            if self._models.budget is not None:
                self._models.budget.reserve(BudgetDimension.RECURSIVE_CHILDREN)
                child_budget_reserved = True
            cleanup_status = "pending"
            acquire_span = start_turn_span(
                "RLM.child.acquire",
                inputs={
                    "call_index": call.call_index,
                    "recursive_depth": call.child_depth,
                    "profile": "semantic-child",
                },
            )
            acquire_started_at = time.monotonic()
            try:
                lease = self._acquire_child_lease(call.call_index, profile="semantic-child")
            except BaseException as exc:
                capacity_refusal = isinstance(exc, ChildRuntimeNotStartedError)
                acquire_span.finish(
                    phase_status="completed" if capacity_refusal else "failed",
                    outputs={
                        "duration_ms": _elapsed_ms(acquire_started_at),
                        "status": "not_started" if capacity_refusal else "failed",
                        "failure_category": _recursive_failure_category(exc),
                    },
                )
                raise
            else:
                acquire_span.finish(
                    phase_status="completed",
                    outputs={"duration_ms": _elapsed_ms(acquire_started_at), "status": "acquired"},
                )
            cleanup_status = "acquired"
            self._metrics.child_started()
            child_started = True
            self._emit_progress(
                "child_started",
                call_index=call.call_index,
                recursive_depth=call.child_depth,
                started_at=call.started_at,
                task_label=call.task_label,
            )
            stage_span = start_turn_span(
                "RLM.child.stage_inputs",
                inputs={
                    "call_index": call.call_index,
                    "file_count": len(child_files),
                    "input_bytes": sum(map(len, child_files.values())),
                },
            )
            stage_started_at = time.monotonic()
            try:
                if child_files:
                    lease.stage_files(child_files)
            except BaseException as exc:
                stage_span.finish(
                    phase_status="failed",
                    outputs={
                        "duration_ms": _elapsed_ms(stage_started_at),
                        "failure_category": _recursive_failure_category(exc),
                    },
                )
                raise
            else:
                stage_span.finish(
                    phase_status="completed",
                    outputs={
                        "duration_ms": _elapsed_ms(stage_started_at),
                        "status": "staged" if child_files else "empty",
                    },
                )
            self._metrics.record_delegated_input_bytes(sum(len(data) for data in child_files.values()))
            self._ensure_call_authorized(batch_cancelled)
            outcome, completion_outputs = self._run_native_child(
                request,
                call,
                lease,
                skills,
                child_files,
                source_manifest,
                source_manifest_sha256,
                batch_cancelled,
            )
            child_answer = outcome.answer
            child_evidence = outcome.evidence
            child_gaps = outcome.gaps
            return outcome
        except ChildRuntimeNotStartedError:
            cleanup_status = "not_acquired"
            if child_budget_reserved and self._models.budget is not None:
                self._models.budget.release_unstarted_recursive_child()
            completion_outputs = {"status": "not_started", "error_category": "capacity"}
            failure_category = "capacity"
            return ChildOutcome(
                status="not_started",
                source_manifest_sha256=source_manifest_sha256,
                error_category="capacity",
                usage=_child_usage(_child_metrics.get() or self._metrics, child_calls=0),
            )
        except BaseException as exc:
            failed = True
            primary_failed = True
            failure_category = self._record_primary_failure(call, exc)
            raise
        finally:
            try:
                self._finalize_call(
                    call,
                    lease,
                    cleanup_status=cleanup_status,
                    failed=failed,
                    primary_failed=primary_failed,
                    completion_outputs=completion_outputs,
                    failure_category=failure_category,
                    child_answer=child_answer,
                    child_evidence=child_evidence,
                    child_gaps=child_gaps,
                )
            finally:
                if child_started:
                    self._metrics.child_completed()


__all__ = [
    "RLM_NATIVE_CHILD_DEPTH",
    "ChildAsyncScheduler",
    "ChildOutcome",
    "ChildRequest",
    "ChildRuntimeAuthorizationError",
    "ChildRuntimeCleanupError",
    "ChildRuntimeFactory",
    "ChildRuntimeLease",
    "ChildRuntimeNotStartedError",
    "DelegationMetrics",
    "DelegationMetricsSnapshot",
    "RecursiveBatchError",
    "RecursiveCallReservation",
    "RecursiveCallSummary",
    "RecursiveRLMExecutor",
    "RecursiveRLMOptions",
    "RecursiveSubtaskSignature",
    "TokenUsageStatus",
    "normalize_lm_token_usage",
    "run_reserved_batch",
]
