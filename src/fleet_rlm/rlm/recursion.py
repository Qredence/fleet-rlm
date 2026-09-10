"""Bounded native DSPy recursion for the Root REPL harness.

Owns the provider-neutral child-runtime protocol, thread-safe delegation
metrics, bounded ThreadPool batch settlement, and the native child-RLM
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
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait
from concurrent.futures import CancelledError as FutureCancelledError
from contextvars import Context, copy_context
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from threading import Event, Lock, RLock, Thread
from typing import Any, Literal, NoReturn, Protocol, Self, TypeAlias
from urllib.parse import unquote, urlsplit

import dspy
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.config.settings import Settings
from fleet_rlm.observability.diagnostics import trace_failure_category
from fleet_rlm.observability.tracing import start_turn_span
from fleet_rlm.rlm.budget import BudgetDimension
from fleet_rlm.rlm.compat_3_3_1 import CodeInterpreter, _RLMTraceCallback, is_native_rlm
from fleet_rlm.rlm.events import Status, ToolEventView, ToolObserver, observe_tool
from fleet_rlm.rlm.output_contract import bind_output_contract
from fleet_rlm.rlm.program import (
    FleetJSONAdapter,
    RLMModelBundle,
    RLMOptions,
    build_native_rlm,
    build_session_context_payload,
)
from fleet_rlm.rlm.result import RLMConfigError, prediction_result, rlm_termination_mode
from fleet_rlm.sessions.history_transport import CommittedSessionHistory
from fleet_rlm.workspace.models import WORKSPACE_MEMORY_INJECTION_TAIL_BYTES, WorkspaceCapabilityMetadata

# ---------------------------------------------------------------------------
# Provider-neutral child-runtime protocol
# ---------------------------------------------------------------------------


class ChildRuntimeCleanupError(RuntimeError):
    """A child runtime could not be proved clean before Root commit."""


class ChildRuntimeAuthorizationError(RuntimeError):
    """A child runtime operation was attempted after Run authority was revoked."""


class ChildRuntimeLease(Protocol):
    """A dedicated child interpreter and its strictly owned cleanup operation."""

    @property
    def interpreter(self) -> CodeInterpreter:
        """Return the caller-owned interpreter for this child lease."""
        ...

    sandbox_id: str
    volume_id: str
    volume_subpath: str

    def close(self) -> None:
        """Close the child runtime lease and release its resources."""
        ...


ChildRuntimeFactory = Callable[..., ChildRuntimeLease]


# ---------------------------------------------------------------------------
# Immutable Session snapshot for delegated children (P47.4)
# ---------------------------------------------------------------------------


class _ImmutableMessageDict(dict[str, Any]):
    """Dictionary-shaped history record that rejects in-place mutation."""

    def __setitem__(self, _key: str, _value: Any) -> NoReturn:
        raise TypeError("recursive Session snapshot history is immutable")

    def __delitem__(self, _key: str) -> NoReturn:
        raise TypeError("recursive Session snapshot history is immutable")

    def __ior__(self, _value: Mapping[str, Any]) -> Self:  # ty: ignore[invalid-method-override]
        raise TypeError("recursive Session snapshot history is immutable")

    def clear(self) -> NoReturn:
        raise TypeError("recursive Session snapshot history is immutable")

    def pop(self, _key: str, _default: Any = None) -> NoReturn:  # ty: ignore[invalid-method-override]
        raise TypeError("recursive Session snapshot history is immutable")

    def popitem(self) -> NoReturn:
        raise TypeError("recursive Session snapshot history is immutable")

    def setdefault(self, _key: str, _default: Any = None) -> NoReturn:
        raise TypeError("recursive Session snapshot history is immutable")

    def update(self, *_args: Any, **_kwargs: Any) -> NoReturn:
        raise TypeError("recursive Session snapshot history is immutable")


class _ImmutableMessageList(list[Any]):
    """List-shaped history container that rejects in-place mutation."""

    def __setitem__(self, _index: int | slice, _value: Any) -> NoReturn:  # ty: ignore[invalid-method-override]
        raise TypeError("recursive Session snapshot history is immutable")

    def __delitem__(self, _index: int | slice) -> NoReturn:  # ty: ignore[invalid-method-override]
        raise TypeError("recursive Session snapshot history is immutable")

    def __iadd__(self, _value: Any) -> Self:
        raise TypeError("recursive Session snapshot history is immutable")

    def __imul__(self, _value: int) -> Self:  # ty: ignore[invalid-method-override]
        raise TypeError("recursive Session snapshot history is immutable")

    def append(self, _value: Any) -> NoReturn:
        raise TypeError("recursive Session snapshot history is immutable")

    def clear(self) -> NoReturn:
        raise TypeError("recursive Session snapshot history is immutable")

    def extend(self, _value: Any) -> NoReturn:
        raise TypeError("recursive Session snapshot history is immutable")

    def insert(self, _index: int, _value: Any) -> NoReturn:  # ty: ignore[invalid-method-override]
        raise TypeError("recursive Session snapshot history is immutable")

    def pop(self, _index: int = -1) -> NoReturn:  # ty: ignore[invalid-method-override]
        raise TypeError("recursive Session snapshot history is immutable")

    def remove(self, _value: Any) -> NoReturn:
        raise TypeError("recursive Session snapshot history is immutable")

    def reverse(self) -> NoReturn:
        raise TypeError("recursive Session snapshot history is immutable")

    def sort(self, **_kwargs: Any) -> NoReturn:
        raise TypeError("recursive Session snapshot history is immutable")


def _freeze_history_value(value: Any) -> Any:
    """Recursively copy JSON-shaped history values into immutable containers."""
    if isinstance(value, Mapping):
        return _ImmutableMessageDict({key: _freeze_history_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return _ImmutableMessageList(_freeze_history_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_history_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_history_value(item) for item in value)
    return value


def _immutable_history(records: Sequence[Mapping[str, Any]]) -> dspy.History:
    """Create a dspy-compatible history whose nested records cannot be changed."""
    frozen_records = _ImmutableMessageList(_freeze_history_value(dict(record)) for record in records)
    materialized = dspy.History(messages=list(frozen_records))
    # DSPy freezes field assignment but intentionally keeps ``messages`` a list.
    # The child-visible snapshot has a stronger contract: neither the list nor a
    # nested record may be mutated by one child and observed by another.
    object.__setattr__(materialized, "messages", frozen_records)
    return materialized


@dataclass(frozen=True, slots=True)
class RecursiveSessionSnapshot:
    """Immutable Session material one delegated child may read (P47.4).

    Children never receive the live Root interpreter, mutable Root Python
    state, or the Session runtime. The snapshot is materialized and copied at
    Turn preparation time; later mutation of the source conversation cannot
    change what a delegated child observes. ``history_transport`` retains the
    typed DSPy ``SandboxSerializable`` form for remote interpreters; the
    regular ``history`` remains the preferred in-process dspy value.
    """

    request: str
    history: dspy.History
    session_context: SessionContextManifest
    workspace: WorkspaceCapabilityMetadata
    models: RLMModelBundle
    history_transport: CommittedSessionHistory | None = None
    workspace_memory_digest: str = ""


def build_recursive_session_snapshot(
    *,
    request: str,
    history: dspy.History | CommittedSessionHistory | None,
    session_context: SessionContextManifest,
    workspace: WorkspaceCapabilityMetadata,
    models: RLMModelBundle,
    workspace_memory_digest: str = "",
) -> RecursiveSessionSnapshot:
    """Materialize the immutable child-visible Session snapshot.

    Parameters:
        request (str): Current committed user request the subproblems are delegated from.
        history (dspy.History | CommittedSessionHistory | None): Committed conversation;
            transport History is materialized and every message record is copied so the
            snapshot can never observe later mutations. A typed transport copy is retained
            when the source is ``CommittedSessionHistory``.
        session_context (SessionContextManifest): Bounded Session navigation metadata.
        workspace (WorkspaceCapabilityMetadata): Authorized read/write capability view.
        models (RLMModelBundle): Root/Sub model policy; each child forks its own copy.
        workspace_memory_digest (str): Bounded memory tail included in the child context payload.

    Returns:
        RecursiveSessionSnapshot: The immutable Session material handed to every delegated child.

    Raises:
        RLMConfigError: If the committed History type or memory digest is invalid.
    """
    if (
        not isinstance(workspace_memory_digest, str)
        or len(workspace_memory_digest.encode("utf-8")) > WORKSPACE_MEMORY_INJECTION_TAIL_BYTES
    ):
        raise RLMConfigError("recursive Session snapshot memory context is invalid")
    transport: CommittedSessionHistory | None = None
    if isinstance(history, CommittedSessionHistory):
        records = history.messages
        transport = CommittedSessionHistory([dict(record) for record in records])
    elif isinstance(history, dspy.History):
        records = history.messages
        # Production Turn preparation starts with dspy.History, but the remote
        # Daytona interpreter needs DSPy's explicit SandboxSerializable form.
        # Keep the native value above for in-process children and retain a
        # transport copy when the canonical committed record shape is present.
        try:
            transport = CommittedSessionHistory([dict(record) for record in records])
        except (TypeError, ValueError):
            transport = None
    elif history is None:
        records = ()
    else:
        raise RLMConfigError("recursive Session snapshot history type is invalid")
    materialized = _immutable_history(records)
    return RecursiveSessionSnapshot(
        request=request,
        history=materialized,
        session_context=session_context,
        workspace=workspace,
        models=models,
        history_transport=transport,
        workspace_memory_digest=workspace_memory_digest,
    )


# ---------------------------------------------------------------------------
# Thread-safe internal delegation metrics
# ---------------------------------------------------------------------------

# Closed observability contract: token totals are either truly observed from a
# provider/history entry or unavailable. There is no "estimated" state.
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
    depth_fallback_calls: int = 0
    peak_child_concurrency: int = 0
    lm_call_counts: tuple[tuple[str, int, int], ...] = ()
    lm_latency_ms: tuple[tuple[str, int, float], ...] = ()
    # Entries are (role, recursive_depth, input_tokens, output_tokens, total_tokens);
    # input/output are kept alongside the total so partial usage never reads as 0.
    # Entries exist only for calls where usage was actually observed; a call
    # whose provider reported no usage must never emit an all-zero entry.
    lm_token_totals: tuple[tuple[str, int, int, int, int], ...] = ()
    token_usage_status: TokenUsageStatus = "unavailable"

    def as_dict(self) -> dict[str, object]:
        """
        Return a bounded JSON- and MLflow-compatible representation of the metrics snapshot.

        Returns:
            dict[str, object]: Serialized metrics, including call counts, latency
                totals rounded to three decimal places, observed token totals, and
                token usage status.
        """
        return {
            "root_lm_calls_depth_0": self.root_lm_calls_depth_0,
            "sub_lm_calls_depth_0": self.sub_lm_calls_depth_0,
            "child_root_lm_calls_depth_1": self.child_root_lm_calls_depth_1,
            "child_sub_lm_calls_depth_1": self.child_sub_lm_calls_depth_1,
            "recursive_child_calls": self.recursive_child_calls,
            "recursive_batch_calls": self.recursive_batch_calls,
            "recursive_children_started": self.recursive_children_started,
            "recursive_children_completed": self.recursive_children_completed,
            "depth_fallback_calls": self.depth_fallback_calls,
            "peak_child_concurrency": self.peak_child_concurrency,
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

    def __init__(self) -> None:
        self._lock = Lock()
        self._lm_calls: dict[tuple[str, int], int] = {}
        self._lm_latency_ms: dict[tuple[str, int], float] = {}
        self._lm_input_tokens: dict[tuple[str, int], int] = {}
        self._lm_output_tokens: dict[tuple[str, int], int] = {}
        self._lm_tokens: dict[tuple[str, int], int] = {}
        self._lm_usage_observed: set[tuple[str, int]] = set()
        self._recursive_child_calls = 0
        self._recursive_batch_calls = 0
        self._recursive_children_started = 0
        self._recursive_children_completed = 0
        self._depth_fallback_calls = 0
        self._active_children = 0
        self._peak_child_concurrency = 0

    def record_lm_call(
        self,
        role: str,
        recursive_depth: int,
        *,
        duration_ms: float = 0.0,
        usage: Mapping[str, Any] | None = None,
    ) -> None:
        """
        Record a language-model request and its aggregate metrics.

        Parameters:
            role (str): Model role, normalized to ``"root"``, ``"sub"``, or ``"unknown"``.
            recursive_depth (int): Recursion depth associated with the request.
            duration_ms (float): Request duration in milliseconds.
            usage (Mapping[str, Any] | None): Provider token-usage data, if available.
                Token totals are recorded only when usage is observed.
        """
        normalized_role = role if role in {"root", "sub"} else "unknown"
        key = (normalized_role, max(0, int(recursive_depth)))
        normalized_usage = normalize_lm_token_usage(usage)
        # Only an actually-observed usage mapping creates token buckets. Call
        # counts and latency stay unconditional; token totals remain absent
        # when the provider reported nothing, so zero can never masquerade as
        # a measurement.
        usage_observed = bool(normalized_usage)
        input_tokens = normalized_usage.get("input_tokens", 0)
        output_tokens = normalized_usage.get("output_tokens", 0)
        tokens = normalized_usage.get("total_tokens", 0)
        with self._lock:
            self._lm_calls[key] = self._lm_calls.get(key, 0) + 1
            self._lm_latency_ms[key] = self._lm_latency_ms.get(key, 0.0) + max(0.0, float(duration_ms))
            if usage_observed:
                self._lm_usage_observed.add(key)
                self._lm_input_tokens[key] = self._lm_input_tokens.get(key, 0) + input_tokens
                self._lm_output_tokens[key] = self._lm_output_tokens.get(key, 0) + output_tokens
                self._lm_tokens[key] = self._lm_tokens.get(key, 0) + tokens

    def record_recursive_call(self) -> None:
        """Record one recursive child call."""
        with self._lock:
            self._recursive_child_calls += 1

    def record_recursive_batch(self) -> None:
        with self._lock:
            self._recursive_batch_calls += 1

    def record_depth_fallback(self) -> None:
        with self._lock:
            self._depth_fallback_calls += 1

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
        """Create an immutable snapshot of the accumulated delegation metrics.

        Returns:
            DelegationMetricsSnapshot: The current metrics, including call counts,
                latency totals, concurrency data, and token usage status.
        """
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
                depth_fallback_calls=self._depth_fallback_calls,
                peak_child_concurrency=self._peak_child_concurrency,
                lm_call_counts=calls,
                lm_latency_ms=latency,
                lm_token_totals=tokens,
                token_usage_status="observed" if self._lm_usage_observed else "unavailable",
            )


def normalize_lm_token_usage(usage: Mapping[str, Any] | None) -> dict[str, int]:
    """
    Normalize provider token usage fields into canonical token names.

    Parameters:
        usage (Mapping[str, Any] | None): Provider usage data containing supported token field aliases.

    Returns:
        dict[str, int]: Canonical nonnegative token counts, with total tokens derived
            from input and output counts when unavailable.
    """
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
) -> list[Any]:
    """Run reserved child work with bounded fan-out and input-order results.

    Preserves atomic submit failure retention, first-failure cancellation of
    queued work, deadline-aware aggregation, and running-worker retention via
    ``on_retain_running`` (Futures are the retain tokens for still-running
    workers). Does not own recursive child construction or leases.
    """
    if not reservations:
        raise ValueError("reserved batch must not be empty")
    workers = min(max_parallel, len(reservations))
    answers: list[Any] = []
    batch_cancelled = Event()
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fleet-rlm-child")
    futures: list[Future[Any]] = []
    try:
        try:
            for reservation in reservations:
                # Capture the submitter ContextVar state (MLflow turn span, etc.)
                # before the worker starts; copy_context() inside the worker would
                # see an empty thread-local context.
                ctx = copy_context()

                def _run(
                    reserved: RecursiveCallReservation = reservation,
                    context: Context = ctx,
                ) -> Any:
                    return context.run(execute, reserved, batch_cancelled)

                futures.append(pool.submit(_run))
        except BaseException:
            batch_cancelled.set()
            pending = {future for future in futures if not future.done()}
            for future in pending:
                future.cancel()
            on_retain_running(pending)
            raise
        remaining = max(0.0, deadline_monotonic - time.monotonic())
        done, not_done = wait(futures, timeout=remaining, return_when=FIRST_EXCEPTION)
        failures = _future_failures(done)
        if failures or not_done:
            batch_cancelled.set()
            for future in not_done:
                future.cancel()
        if not_done:
            # Running Python threads cannot be force-cancelled. Each worker
            # retains its own lease until its deadline-bound LM call exits;
            # queued work is cancelled and executor teardown never performs
            # a second unbounded join on the Root worker.
            on_retain_running(not_done)
            if failures:
                raise RecursiveBatchError() from failures[0]
            raise TimeoutError("recursive child batch deadline exceeded")
        if failures:
            raise RecursiveBatchError() from failures[0]
        answers = [future.result(timeout=0) for future in futures]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
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

# bounded Sub fallback. This is a product invariant owned here, not an
# operator-facing policy knob.
RLM_NATIVE_CHILD_DEPTH = 1
_MAX_CAPSULE_BYTES = 50_000
_MAX_CAPSULE_FRAGMENTS = 16
_MAX_CAPSULE_REFERENCES = 32


class SubproblemCapsule(BaseModel):
    """Strict selected child input; never a copied Session or Workspace view.

    The model is intentionally closed and frozen.  ``model_validate`` accepts
    JSON arrays for tuple-shaped fields, while unknown keys and unsafe local
    paths fail before a child reservation is admitted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task: str = Field(min_length=1, max_length=_MAX_CAPSULE_BYTES)
    fragments: tuple[str, ...] = Field(default=(), max_length=_MAX_CAPSULE_FRAGMENTS)
    authorized_references: tuple[str, ...] = Field(default=(), max_length=_MAX_CAPSULE_REFERENCES)
    expected_result_shape: str = Field(default="concise answer", min_length=1, max_length=2_000)
    evidence_requirements: tuple[str, ...] = Field(default=(), max_length=16)
    allocation_chars: int = Field(default=4_000, gt=0, le=_MAX_CAPSULE_BYTES)
    # A selected-file digest is evidence metadata, not an instruction.  It is
    # included only when a caller actually selected a local file.
    selected_file_checksums: tuple[tuple[str, str], ...] = Field(default=(), max_length=_MAX_CAPSULE_REFERENCES)

    @field_validator("fragments", "authorized_references", "evidence_requirements", mode="before")
    @classmethod
    def _normalize_text_collection(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, (tuple, list)):
            raise ValueError("capsule collections must be arrays of text")
        return tuple(value)

    @field_validator("selected_file_checksums", mode="before")
    @classmethod
    def _normalize_checksums(cls, value: object) -> tuple[tuple[str, str], ...]:
        if value is None:
            return ()
        if not isinstance(value, (tuple, list)):
            raise ValueError("capsule checksums must be an array")
        normalized: list[tuple[str, str]] = []
        for item in value:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise ValueError("capsule checksum entries must contain path and digest")
            normalized.append((item[0], item[1]))
        return tuple(normalized)

    @model_validator(mode="after")
    def _validate_selected_input(self) -> SubproblemCapsule:
        values = (*self.fragments, *self.authorized_references, *self.evidence_requirements)
        if not self.task.strip() or not self.expected_result_shape.strip():
            raise ValueError("capsule fields must contain non-empty text")
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("capsule fields must contain non-empty text")
        for reference in self.authorized_references:
            _validate_capsule_reference(reference)
        for path, digest in self.selected_file_checksums:
            _validate_capsule_path(path)
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError("capsule file checksum must be a SHA-256 hex digest")
            try:
                int(digest, 16)
            except ValueError:
                raise ValueError("capsule file checksum must be a SHA-256 hex digest") from None
        if len(self.render().encode("utf-8")) > min(self.allocation_chars, _MAX_CAPSULE_BYTES):
            raise ValueError("capsule serialized bytes exceed its allocation")
        return self

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SubproblemCapsule:
        """Parse one strict mapping without silently dropping unknown fields."""
        if not isinstance(value, Mapping):
            raise ValueError("capsule must be a JSON object")
        return cls.model_validate(value)

    @property
    def serialized_bytes(self) -> int:
        """Return the deterministic UTF-8 size admitted by this capsule."""
        return len(self.render().encode("utf-8"))

    def render(self) -> str:
        """Serialize selected untrusted inputs deterministically."""
        payload = {
            "task": self.task.strip(),
            "selected_fragments": list(self.fragments),
            "authorized_references": list(self.authorized_references),
            "expected_result_shape": self.expected_result_shape.strip(),
            "evidence_requirements": list(self.evidence_requirements),
            "selected_file_checksums": [
                {"path": path, "sha256": digest.lower()} for path, digest in self.selected_file_checksums
            ],
        }
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _validate_capsule_path(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("capsule reference path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("capsule reference path escapes its authorized scope")


def _validate_capsule_reference(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("capsule reference is invalid")
    parsed = urlsplit(value)
    if parsed.scheme:
        if (
            parsed.scheme.lower() != "artifact"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("capsule reference is outside its authorized scope")
        authority = unquote(parsed.netloc)
        path = unquote(parsed.path)
        if authority in {".", ".."} or "/" in authority or "\\" in authority:
            raise ValueError("capsule reference is outside its authorized scope")
        if path:
            _validate_capsule_path(path.lstrip("/"))
        return
    _validate_capsule_path(value)


@dataclass(frozen=True, slots=True)
class CapsuleResult:
    """Bounded child evidence returned to the Root, which remains publisher."""

    status: Literal["complete"]
    answer: str
    source_references: tuple[str, ...]
    uncertainty: str
    usage: Mapping[str, int]
    outcome_status: Literal["completed", "failed", "timeout", "cancelled"] = "completed"
    verified_observations: tuple[str, ...] = ()
    error_category: str | None = None


CapsuleStatus: TypeAlias = Literal["completed", "failed", "timeout", "cancelled"]


@dataclass(frozen=True, slots=True)
class ChildOutcome:
    """Bounded per-child evidence; private trajectories never cross this DTO."""

    status: CapsuleStatus
    answer: str = ""
    source_references: tuple[str, ...] = ()
    verified_observations: tuple[str, ...] = ()
    uncertainty: str = ""
    usage: Mapping[str, int] = field(default_factory=dict)
    error_category: str | None = None
    selected_input_bytes: int = 0
    result_bytes: int = 0

    def as_dict(self) -> dict[str, object]:
        """Return the public JSON-shaped representation of this outcome."""
        return {
            "status": self.status,
            "answer": self.answer,
            "source_references": list(self.source_references),
            "verified_observations": list(self.verified_observations),
            "uncertainty": self.uncertainty,
            "usage": dict(self.usage),
            "error_category": self.error_category,
            "selected_input_bytes": self.selected_input_bytes,
            "result_bytes": self.result_bytes,
        }


# Wait bound for detached child workers retained after batch settlement. A
# provider, LM call, or interpreter that never returns becomes a cleanup
# failure instead of an unbounded root-cleanup hang; when the worker later
# unwinds, its lease close still runs through the factory's late-cleanup lane.
_PENDING_BATCH_WAIT_TIMEOUT_S = 60.0

# Grace bound for a fenced child invocation to unwind cooperatively after the
# absolute Turn deadline fires. A child that still will not settle is retained
# under cleanup ownership instead of blocking the synchronous recursive Tool;
# its lease close then drives the interpreter shutdown that unwinds it. Read
# at call time so fault-injection lanes can shorten it.
_CHILD_FENCE_SETTLE_GRACE_S = 5.0


class ChildAsyncScheduler:
    """Application-owned async workers for native child invocations.

    DSPy's ``RLM.acall`` is asynchronous, while the interpreter contract is
    synchronous.  A child therefore needs a thread whose event loop can await
    the RLM without blocking Fleet's serving loop.  The old implementation
    created and destroyed one event loop for every child.  This scheduler
    creates a bounded set of long-lived loops once and routes child futures to
    them; callers retain the returned ``concurrent.futures.Future`` as the
    ownership/cancellation token.
    """

    def __init__(self, max_workers: int = 1) -> None:
        if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers <= 0:
            raise ValueError("child scheduler worker count must be a positive integer")
        self._lock = Lock()
        self._closed = False
        self._next = 0
        self._loops: list[asyncio.AbstractEventLoop | None] = [None] * max_workers
        self._threads: list[Thread] = []
        self._ready: list[Event] = []
        self._tasks: dict[Future[Any], tuple[asyncio.AbstractEventLoop, asyncio.Task[Any]]] = {}
        self._cancel_requested: set[Future[Any]] = set()
        for index in range(max_workers):
            ready = Event()
            thread = Thread(
                target=self._run_loop,
                args=(index, ready),
                name=f"fleet-rlm-child-scheduler-{index}",
                daemon=True,
            )
            self._threads.append(thread)
            self._ready.append(ready)
            thread.start()
        for ready in self._ready:
            if not ready.wait(5.0):
                self.shutdown()
                raise RuntimeError("child scheduler failed to start")

    def _run_loop(self, index: int, ready: Event) -> None:
        loop = asyncio.new_event_loop()
        with self._lock:
            self._loops[index] = loop
        asyncio.set_event_loop(loop)
        ready.set()
        try:
            loop.run_forever()
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    def submit(self, awaitable: Any) -> Future[Any]:
        """Submit one awaitable to a scheduler loop and return its ownership token."""
        with self._lock:
            if self._closed:
                if inspect.iscoroutine(awaitable):
                    awaitable.close()
                raise RuntimeError("child scheduler is closed")
            loops = tuple(loop for loop in self._loops if loop is not None and not loop.is_closed())
            if not loops:
                if inspect.iscoroutine(awaitable):
                    awaitable.close()
                raise RuntimeError("child scheduler has no running loop")
            loop = loops[self._next % len(loops)]
            self._next += 1
        future_box: list[Future[Any]] = []

        async def run_owned() -> Any:
            task = asyncio.current_task()
            if task is None:  # pragma: no cover - asyncio always supplies a current task
                raise RuntimeError("child scheduler task was not created")
            # The outer Future is assigned immediately after submission.  Yield
            # once so cancellation can race safely with task registration.
            while not future_box:
                await asyncio.sleep(0)
            outer = future_box[0]
            with self._lock:
                self._tasks[outer] = (loop, task)
                requested = outer in self._cancel_requested
                if requested:
                    self._cancel_requested.discard(outer)
            if requested:
                if inspect.iscoroutine(awaitable):
                    awaitable.close()
                task.cancel()
                raise asyncio.CancelledError
            try:
                return await awaitable
            finally:
                with self._lock:
                    self._tasks.pop(outer, None)
                    self._cancel_requested.discard(outer)

        future = asyncio.run_coroutine_threadsafe(run_owned(), loop)
        future_box.append(future)
        return future

    def cancel(self, future: Future[Any]) -> bool:
        """Request cooperative cancellation without cancelling the ownership token."""
        with self._lock:
            owned = self._tasks.get(future)
            if owned is None:
                if future.done():
                    return False
                self._cancel_requested.add(future)
                return True
            loop, task = owned
        if not task.done() and not loop.is_closed():
            loop.call_soon_threadsafe(task.cancel)
            return True
        return False

    def shutdown(self, *, timeout: float = 5.0) -> None:
        """Stop scheduler loops after their owned futures have been settled."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loops = tuple(loop for loop in self._loops if loop is not None and not loop.is_closed())
        for loop in loops:
            loop.call_soon_threadsafe(loop.stop)
        deadline = time.monotonic() + max(0.0, timeout)
        for thread in self._threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))


def _invoke_async_child(
    child_acall: Callable[..., Any],
    interpreter: Any,
    prompt: str,
    *,
    native: bool,
    deadline: float,
    retain_pending: Callable[[Future[Any]], None],
    extra_inputs: Mapping[str, Any] | None = None,
    scheduler: ChildAsyncScheduler | None = None,
) -> Any:
    """Await a native child under the one absolute Turn deadline.

    ``RLM`` executes the synchronous recursive Tool either on the parent
    worker's running event loop or from a plain synchronous caller.  Either
    way, the child invocation is submitted to the application-owned bounded
    scheduler so the synchronous Tool never blocks the parent loop, and the
    join is fenced by the remaining Turn deadline (p39b): a child that never
    completes cannot hold the Tool past the deadline.

    When the fence fires, the child task receives one cooperative
    cancellation and is given a bounded grace window to unwind.  A child that
    still refuses to settle is retained through ``retain_pending`` so the
    executor's ownership boundary (``wait_owned``) settles it fail-closed
    instead of leaking the wait.  A child that completed with its own error
    before the fence keeps that classification.
    """

    async def invoke() -> Any:
        if native:
            if extra_inputs:
                return await child_acall(interpreter, prompt=prompt, **extra_inputs)
            return await child_acall(interpreter, prompt=prompt)
        return await child_acall(interpreter=interpreter, prompt=prompt)

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("recursive child deadline exceeded")

    if scheduler is None:
        raise RuntimeError("recursive child scheduler is unavailable")
    future = scheduler.submit(invoke())
    try:
        return future.result(timeout=remaining)
    except TimeoutError:
        # A child that settled with its own error concurrently with the fence
        # keeps that classification; a late result past the deadline is
        # discarded by the fence below.
        if future.done():
            child_error = future.exception()
            if child_error is not None:
                raise child_error from None

    # The deadline fired while the child was still running.  Cancellation of
    # the scheduler future propagates to the Task on its long-lived loop.
    scheduler.cancel(future)
    try:
        future.result(timeout=_CHILD_FENCE_SETTLE_GRACE_S)
    except TimeoutError:
        # A child that still will not settle stays owned: the retained future
        # joins the executor's cleanup boundary instead of blocking the Tool.
        # Its lease close runs in the caller's finally and drives the
        # interpreter shutdown that unwinds the child; ownership then reports
        # pending until it does.
        retain_pending(future)
    except BaseException:
        # A late child error after the deadline fired is superseded by the
        # fence: the Turn is out of time either way.
        pass
    raise TimeoutError("recursive child deadline exceeded")


@dataclass(frozen=True, slots=True)
class RecursiveRLMOptions:
    """Invocation limits for the custom recursive RLM Tool."""

    enabled: bool = False
    max_calls: int = 4
    max_prompt_chars: int = 50_000
    child_max_iters: int = 8
    child_max_llm_calls: int = 12
    child_max_output_chars: int = 4_000
    # A single worker is the safe default for a one-call reservation. Policy
    # can raise this explicitly, but never above the global child-call limit.
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
    """Project Settings onto the bounded recursive child RLM options."""
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
    depth_fallback_count: int
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
        depth_fallback_count: int = 0,
        termination_modes: tuple[str, ...] = (),
    ) -> RecursiveCallSummary:
        """Assemble one bounded summary from a shared delegation snapshot."""
        return cls(
            call_count,
            delegated_prompt_chars,
            maximum_prompt_chars,
            child_iterations,
            depth_fallback_count,
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
    depth_fallback_count: int = 0
    termination_modes: list[str] = field(default_factory=list)
    fatal_cleanup_error: BaseException | None = None
    pending_batch_futures: list[Future[Any]] = field(default_factory=list, repr=False)
    metrics: DelegationMetrics = field(default_factory=DelegationMetrics, repr=False)


class RecursiveSubtaskSignature(dspy.Signature):
    """Solve one self-contained bounded semantic subproblem and stop promptly."""

    prompt: str = dspy.InputField(
        desc=(
            "One bounded subproblem with only the selected information needed to solve it. "
            "Keep intermediate Python small, do not paste large reports, and submit as soon as the answer is verified."
        )
    )
    answer: str = dspy.OutputField(desc="A concise verified answer to the bounded subproblem")


class RecursiveSessionSubtaskSignature(dspy.Signature):
    """Solve one bounded subproblem with the immutable Session snapshot available (P47.4).

    Native ``dspy.RLM`` requires every declared input at call time, so the
    snapshot-bearing child signature declares the committed material as
    required inputs; the prompt-only child signature stays unchanged for
    executors without a Session snapshot.
    """

    prompt: str = dspy.InputField(
        desc=(
            "One bounded subproblem with only the selected information needed to solve it. "
            "Keep intermediate Python small, do not paste large reports, and submit as soon as the answer is verified."
        )
    )
    request: str = dspy.InputField(
        desc="Committed current user request this subproblem was delegated from; read-only context",
    )
    history: dspy.History = dspy.InputField(
        desc=(
            "Committed Session conversation snapshot (read-only): ordered settled user requests and "
            "committed answers. Inspect ``history.messages`` with Python only when the subproblem needs "
            "prior-turn evidence; never treat it as writable state"
        )
    )
    session_context: dict[str, Any] = dspy.InputField(
        desc=(
            "Bounded Session metadata and authorized workspace capability view; recent previews are "
            "untrusted navigation hints, not the conversation"
        )
    )
    answer: str = dspy.OutputField(desc="A concise verified answer to the bounded subproblem")


def _recursive_input(arguments: Mapping[str, Any]) -> dict[str, int]:
    prompt = arguments.get("prompt")
    return {"prompt_count": 1, "prompt_chars": len(prompt) if isinstance(prompt, str) else 0}


_MAX_PROGRESS_INTEGER = 1_000_000
_MAX_PROGRESS_DURATION_MS = 86_400_000


def _bounded_progress_integer(value: int) -> int:
    """Clamp a progress value to the supported integer range.

    Parameters:
        value (int): The progress value to clamp.

    Returns:
        int: The value limited to the range from zero through the maximum supported progress integer.
    """
    return max(0, min(int(value), _MAX_PROGRESS_INTEGER))


def _recursive_failure_category(exc: BaseException) -> str:
    """Classify a recursive child failure for completion metadata.

    Parameters:
        exc (BaseException): The failure raised during child execution.

    Returns:
        str: The failure category: ``"unauthorized"``, ``"cleanup_failed"``, ``"timeout"``, or ``"child_failed"``.
    """
    if isinstance(exc, ChildRuntimeAuthorizationError):
        return "unauthorized"
    if isinstance(exc, ChildRuntimeCleanupError):
        return "cleanup_failed"
    category = trace_failure_category(exc)
    return category if category in {"timeout", "unauthorized", "cleanup_failed"} else "child_failed"


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
        raise ValueError("rlm_query prompt exceeds the configured character bound")
    return prompt


@dataclass(frozen=True, slots=True)
class _RecursiveCall:
    call_index: int
    child_depth: int
    started_at: float
    span: Any


class RecursiveRLMExecutor:
    """Execute bounded recursive child RLMs from a synchronous DSPy worker.

    The native RLM constructor and synchronous ``forward`` surface are defined by
    ``dspy/predict/rlm.py:104-159`` and ``dspy/predict/rlm.py:624-675``. The
    caller supplies a dedicated child runtime lease; this coordinator closes the
    lease before returning to Root code.

    Args:
        models: Root and Sub LMs selected by Fleet policy.
        options: Recursion limits for this invocation.
        child_runtime_factory: Factory for a dedicated child runtime lease.
        deadline: Monotonic Turn deadline.
        depth: Current RLM depth, where the Root is zero.
        state: Shared mutable aggregate counters for the invocation.
        observer: Optional bounded Tool observer for nested calls.
        is_authorized: Optional live Run-authority fence checked at child boundaries.

    Returns:
        An executor whose ``tool`` can be injected into a native ``dspy.RLM``.
    """

    def __init__(
        self,
        *,
        models: RLMModelBundle,
        options: RecursiveRLMOptions,
        child_runtime_factory: ChildRuntimeFactory | None,
        deadline: float,
        depth: int = 0,
        state: _RecursiveState | None = None,
        metrics: DelegationMetrics | None = None,
        observer: ToolObserver | None = None,
        is_authorized: Callable[[], bool] | None = None,
        snapshot: RecursiveSessionSnapshot | None = None,
        scheduler: ChildAsyncScheduler | None = None,
    ) -> None:
        """
        Configure a bounded recursive RLM executor.

        Parameters:
            models (RLMModelBundle): Models used for recursive and fallback execution.
            options (RecursiveRLMOptions): Limits and behavior for recursive calls.
            child_runtime_factory (ChildRuntimeFactory | None): Factory for acquiring child runtimes.
            deadline (float): Absolute execution deadline.
            depth (int): Current recursion depth.
            state (_RecursiveState | None): Shared state for aggregating nested-call metadata.
            observer (ToolObserver | None): Optional observer for tool execution events.
            is_authorized (Callable[[], bool] | None): Optional authorization check for recursive execution.
            snapshot (RecursiveSessionSnapshot | None): Immutable Session material delegated to
                every native child (P47.4). When absent, children receive only the delegated prompt.
            scheduler (ChildAsyncScheduler | None): Application-owned scheduler for async child RLM calls.
        """
        self._models = models
        self._options = options
        self._child_runtime_factory = child_runtime_factory
        self._deadline = deadline
        self._depth = depth
        self._state = state or _RecursiveState()
        if metrics is not None:
            self._state.metrics = metrics
        self._metrics = self._state.metrics
        self._observer = observer
        self._is_authorized = is_authorized
        self._snapshot = snapshot
        self._owns_scheduler = scheduler is None
        self._last_completion: dict[str, object] | None = None
        self._last_capsule_outcomes: tuple[ChildOutcome, ...] = ()
        raw_tool = dspy.Tool(
            self._call,
            name="rlm_query",
            desc=(
                "Solve one self-contained bounded semantic subproblem. Pass only selected data, "
                "not the complete Turn, history, Attachment, or Workspace. Store the concise answer."
            ),
        )
        if observer is not None or is_authorized is not None:
            self._tool = observe_tool(
                raw_tool,
                observer or (lambda _detail: None),
                ToolEventView(input_projection=_recursive_input, output_projection=self._recursive_output),
                is_authorized=is_authorized,
            )
        else:
            self._tool = raw_tool
        raw_batch_tool = dspy.Tool(
            self._call_batched,
            name="rlm_query_batched",
            desc=(
                "Solve multiple independent bounded subproblems with isolated child RLMs. "
                "Inputs and outputs preserve order; use only when every item needs iterative exploration."
            ),
        )
        if observer is not None or is_authorized is not None:
            self._batched_tool = observe_tool(
                raw_batch_tool,
                observer or (lambda _detail: None),
                ToolEventView(
                    input_projection=self._recursive_batch_input,
                    output_projection=self._recursive_batch_output,
                ),
                is_authorized=is_authorized,
            )
        else:
            self._batched_tool = raw_batch_tool
        raw_capsule_tool = dspy.Tool(
            self._call_capsule,
            name="rlm_query_capsule",
            desc=(
                "Solve one selected-input subproblem. Provide task, selected fragments, authorized references, "
                "expected result shape, evidence requirements and a bounded allocation. Do not pass Session history."
            ),
        )
        if observer is not None or is_authorized is not None:
            self._capsule_tool = observe_tool(
                raw_capsule_tool,
                observer or (lambda _detail: None),
                ToolEventView(input_projection=self._capsule_input, output_projection=self._recursive_output),
                is_authorized=is_authorized,
            )
        else:
            self._capsule_tool = raw_capsule_tool
        raw_capsule_batch_tool = dspy.Tool(
            self._call_capsules_batched,
            name="rlm_query_capsules_batched",
            desc=(
                "Solve multiple independent selected-input capsules with isolated child RLMs. "
                "Inputs and outputs preserve order; the initial contract is all-or-nothing."
            ),
        )
        if observer is not None or is_authorized is not None:
            self._capsule_batch_tool = observe_tool(
                raw_capsule_batch_tool,
                observer or (lambda _detail: None),
                ToolEventView(
                    input_projection=self._capsule_batch_input,
                    output_projection=self._recursive_batch_output,
                ),
                is_authorized=is_authorized,
            )
        else:
            self._capsule_batch_tool = raw_capsule_batch_tool
        raw_readonly_partial_capsule_batch_tool = dspy.Tool(
            self._call_capsules_readonly_batched,
            name="rlm_query_capsules_readonly_batched",
            desc=(
                "Read bounded evidence from multiple independent selected-input capsules. "
                "Completed siblings are returned when an ordinary child fails; verify every result "
                "and do not publish it."
            ),
        )
        if observer is not None or is_authorized is not None:
            self._readonly_partial_capsule_batch_tool = observe_tool(
                raw_readonly_partial_capsule_batch_tool,
                observer or (lambda _detail: None),
                ToolEventView(
                    input_projection=self._capsule_batch_input,
                    output_projection=self._recursive_batch_output,
                ),
                is_authorized=is_authorized,
            )
        else:
            self._readonly_partial_capsule_batch_tool = raw_readonly_partial_capsule_batch_tool
        # Delay creation until all Tool bindings have succeeded. If startup
        # fails while assembling the executor, no owned scheduler thread is
        # left behind; externally supplied schedulers remain untouched.
        self._scheduler = scheduler or ChildAsyncScheduler(max_workers=options.max_parallel_children)

    @property
    def tool(self) -> dspy.Tool:
        """Return the custom Tool accepted by the native RLM constructor."""
        return self._tool

    @property
    def batched_tool(self) -> dspy.Tool:
        """Return the Root-only batched recursive Tool."""
        return self._batched_tool

    @property
    def capsule_tool(self) -> dspy.Tool:
        """Return the strict selected-input recursive tool for Root programs."""
        return self._capsule_tool

    @property
    def capsule_batch_tool(self) -> dspy.Tool:
        """Return the ordered strict selected-input batch Tool."""
        return self._capsule_batch_tool

    @property
    def readonly_partial_capsule_batch_tool(self) -> dspy.Tool:
        """Return the explicit read-only partial-result capsule batch Tool."""
        return self._readonly_partial_capsule_batch_tool

    @property
    def last_capsule_outcomes(self) -> tuple[ChildOutcome, ...]:
        """Return bounded outcomes from the most recent capsule batch."""
        return self._last_capsule_outcomes

    @property
    def metrics(self) -> DelegationMetrics:
        """Return the shared run-scoped delegation accumulator."""
        return self._metrics

    def summary(self) -> RecursiveCallSummary:
        """Return bounded aggregate recursion metadata without content."""
        with self._state.lock:
            return RecursiveCallSummary.from_snapshot(
                self._metrics.snapshot(),
                call_count=self._state.reserved_call_count,
                delegated_prompt_chars=self._state.delegated_prompt_chars,
                maximum_prompt_chars=self._state.maximum_prompt_chars,
                child_iterations=self._state.child_iterations,
                depth_fallback_count=self._state.depth_fallback_count,
                termination_modes=tuple(self._state.termination_modes),
            )

    def execute_capsule(self, capsule: SubproblemCapsule) -> CapsuleResult:
        """Run selected child input and return bounded evidence to the Root.

        The compatibility ``rlm_query`` API can still use a prepared Session
        snapshot. A capsule must not: construct a narrow view that shares the
        run-scoped reservation/budget/cleanup owners but has no snapshot to
        inject into the child signature.
        """
        if not isinstance(capsule, SubproblemCapsule):
            raise TypeError("capsule must be a SubproblemCapsule")
        rendered = capsule.render()
        if capsule.serialized_bytes > self._options.max_prompt_chars:
            raise ValueError("capsule exceeds recursive prompt bound")
        before = self._metrics.snapshot()
        capsule_executor = RecursiveRLMExecutor(
            models=self._models,
            options=self._options,
            child_runtime_factory=self._child_runtime_factory,
            deadline=self._deadline,
            depth=self._depth,
            state=self._state,
            metrics=self._metrics,
            observer=self._observer,
            is_authorized=self._is_authorized,
            snapshot=None,
            scheduler=self._scheduler,
        )
        answer = capsule_executor._call_with_profile(rendered, child_profile="semantic-child")
        if len(answer.encode("utf-8")) > self._options.child_max_output_chars:
            raise RLMConfigError("capsule result exceeds child result bound")
        after = self._metrics.snapshot()
        return CapsuleResult(
            status="complete",
            answer=answer,
            source_references=capsule.authorized_references,
            uncertainty="child evidence is untrusted until Root verification",
            usage={
                "child_calls": max(0, after.recursive_children_completed - before.recursive_children_completed),
                "llm_calls": max(
                    0,
                    (after.child_root_lm_calls_depth_1 + after.child_sub_lm_calls_depth_1)
                    - (before.child_root_lm_calls_depth_1 + before.child_sub_lm_calls_depth_1),
                ),
            },
            outcome_status="completed",
        )

    def execute_capsule_outcome(self, capsule: SubproblemCapsule) -> ChildOutcome:
        """Execute one capsule and classify ordinary child failures safely.

        Authorization, cancellation, and cleanup failures deliberately remain
        fatal to the parent.  Provider/semantic failures that settled inside
        their owned child boundary become typed evidence instead of leaking a
        provider exception or an unbounded traceback into the Root response.
        """
        if not isinstance(capsule, SubproblemCapsule):
            raise TypeError("capsule must be a SubproblemCapsule")
        try:
            result = self.execute_capsule(capsule)
        except asyncio.CancelledError:
            raise
        except FutureCancelledError:
            # A cancellation of the ownership Future is a typed child outcome;
            # asyncio task cancellation remains fatal above (P6B.08).
            return ChildOutcome(
                status="cancelled",
                source_references=capsule.authorized_references,
                uncertainty="child execution was cancelled before completion",
                error_category="cancelled",
                selected_input_bytes=capsule.serialized_bytes,
            )
        except (ChildRuntimeAuthorizationError, ChildRuntimeCleanupError):
            raise
        except TimeoutError:
            return ChildOutcome(
                status="timeout",
                source_references=capsule.authorized_references,
                uncertainty="child did not settle before the shared deadline",
                error_category="timeout",
                selected_input_bytes=capsule.serialized_bytes,
            )
        except Exception as exc:
            return ChildOutcome(
                status="failed",
                source_references=capsule.authorized_references,
                uncertainty="child evidence is unavailable",
                error_category=_recursive_failure_category(exc),
                selected_input_bytes=capsule.serialized_bytes,
            )
        return ChildOutcome(
            status="completed",
            answer=result.answer,
            source_references=result.source_references,
            verified_observations=result.verified_observations,
            uncertainty=result.uncertainty,
            usage=result.usage,
            error_category=result.error_category,
            selected_input_bytes=capsule.serialized_bytes,
            result_bytes=len(result.answer.encode("utf-8")),
        )

    def _call_capsule(
        self,
        task: str,
        fragments: list[str] | None = None,
        authorized_references: list[str] | None = None,
        expected_result_shape: str = "concise answer",
        evidence_requirements: list[str] | None = None,
        allocation_chars: int = 4_000,
        selected_file_checksums: list[list[str]] | None = None,
    ) -> dict[str, object]:
        """Tool-shaped adapter that rejects undeclared child input fields."""
        capsule = SubproblemCapsule(
            task=task,
            fragments=tuple(fragments or ()),
            authorized_references=tuple(authorized_references or ()),
            expected_result_shape=expected_result_shape,
            evidence_requirements=tuple(evidence_requirements or ()),
            allocation_chars=allocation_chars,
            selected_file_checksums=tuple(tuple(item) for item in (selected_file_checksums or ())),
        )
        outcome = self.execute_capsule_outcome(capsule)
        payload = outcome.as_dict()
        payload["status"] = "complete" if outcome.status == "completed" else outcome.status
        return payload

    def _call_capsules_batched(self, capsules: list[Mapping[str, object]]) -> list[dict[str, object]]:
        """Run selected-input capsules in order with atomic admission.

        The scheduler replacement intentionally keeps the legacy all-or-nothing
        result policy: every capsule is admitted up front, and no sibling
        evidence is returned when one ordinary child fails.  The typed outcome
        records remain available through :attr:`last_capsule_outcomes` for a
        later explicitly read-only partial-result policy.
        """
        return self._run_capsule_batch(capsules, allow_partial_results=False)

    def _call_capsules_readonly_batched(self, capsules: list[Mapping[str, object]]) -> list[dict[str, object]]:
        """Return read-only sibling outcomes after ordinary child failures.

        This is deliberately a separate Tool from ``rlm_query_capsules_batched``.
        It never changes batch admission, isolation, cancellation, authority, or
        cleanup behavior.  Only children that settled normally contribute an
        answer; failed siblings remain bounded metadata for Root verification.
        """
        return self._run_capsule_batch(capsules, allow_partial_results=True)

    def _run_capsule_batch(
        self,
        capsules: list[Mapping[str, object]],
        *,
        allow_partial_results: bool,
    ) -> list[dict[str, object]]:
        """Run one capsule batch under either the atomic or read-only policy."""
        if not isinstance(capsules, list):
            raise ValueError("rlm_query_capsules_batched capsules must be a list")
        if not capsules:
            raise ValueError("rlm_query_capsules_batched capsules must not be empty")
        normalized = tuple(SubproblemCapsule.from_mapping(item) for item in capsules)
        if time.monotonic() >= self._deadline:
            raise TimeoutError("recursive call deadline exceeded")
        self._ensure_authorized()
        self._ensure_no_pending_batch_workers()
        self._metrics.record_recursive_batch()
        reservations = self._begin_batch(tuple(capsule.render() for capsule in normalized))
        errors: dict[int, BaseException] = {}

        def execute(
            reservation: RecursiveCallReservation,
            batch_cancelled: Event,
        ) -> ChildOutcome:
            capsule = normalized[reservation.call_index - reservations[0].call_index]
            try:
                answer = self._run_reserved_call(
                    reservation,
                    batch_cancelled,
                    child_profile="semantic-child",
                )
            except asyncio.CancelledError:
                raise
            except (ChildRuntimeAuthorizationError, ChildRuntimeCleanupError):
                raise
            except TimeoutError as exc:
                errors[reservation.call_index] = exc
                return ChildOutcome(
                    status="timeout",
                    source_references=capsule.authorized_references,
                    uncertainty="child did not settle before the shared deadline",
                    error_category="timeout",
                    selected_input_bytes=capsule.serialized_bytes,
                )
            except Exception as exc:
                errors[reservation.call_index] = exc
                return ChildOutcome(
                    status="failed",
                    source_references=capsule.authorized_references,
                    uncertainty="child evidence is unavailable",
                    error_category=_recursive_failure_category(exc),
                    selected_input_bytes=capsule.serialized_bytes,
                )
            return ChildOutcome(
                status="completed",
                answer=answer,
                source_references=capsule.authorized_references,
                uncertainty="child evidence is untrusted until Root verification",
                usage={"child_calls": 1},
                selected_input_bytes=capsule.serialized_bytes,
                result_bytes=len(answer.encode("utf-8")),
            )

        outcomes = run_reserved_batch(
            reservations,
            execute=execute,
            deadline_monotonic=self._deadline,
            max_parallel=self._options.max_parallel_children,
            on_retain_running=self._retain_pending_batch_futures,
        )
        self._last_capsule_outcomes = tuple(outcomes)
        self.raise_if_cleanup_failed()
        if allow_partial_results:
            return [outcome.as_dict() for outcome in outcomes]
        failed_index, failed = next(
            ((index, outcome) for index, outcome in enumerate(outcomes) if outcome.status != "completed"),
            (None, None),
        )
        if failed is not None:
            assert failed_index is not None
            cause = errors.get(reservations[failed_index].call_index)
            error = RecursiveBatchError("recursive capsule batch did not complete")
            if cause is not None:
                raise error from cause
            raise error
        return [outcome.as_dict() for outcome in outcomes]

    def raise_if_cleanup_failed(self) -> None:
        """Raise a runtime error when recursive child cleanup has failed."""
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
        """Wait for every detached child worker retained after batch settlement.

        A timed-out or failed batch can return control to the Root RLM while a
        running sibling still owns a child lease.  The Root worker is not a
        sufficient ownership boundary in that case: its task may finish before
        the sibling does.  Run cleanup calls this blocking seam off the event
        loop before releasing the parent Run resources.
        """
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

            # A Daytona factory adopts timed-out provider acquisitions so a late
            # Sandbox/permit cannot be orphaned.  Keep that ownership under the
            # same cleanup boundary when the optional hook is available.
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

    def _recursive_output(self, _result: Any) -> dict[str, object]:
        """Return metadata for the most recent recursive completion."""
        if self._last_completion is None:
            return {"status": "completed"}
        return dict(self._last_completion)

    @staticmethod
    def _recursive_batch_input(arguments: Mapping[str, Any]) -> dict[str, int]:
        prompts = arguments.get("prompts")
        if not isinstance(prompts, list):
            return {"prompt_count": 0, "prompt_chars": 0}
        return {
            "prompt_count": len(prompts),
            "prompt_chars": sum(len(item) for item in prompts if isinstance(item, str)),
        }

    @staticmethod
    def _capsule_batch_input(arguments: Mapping[str, Any]) -> dict[str, int]:
        capsules = arguments.get("capsules")
        if not isinstance(capsules, list):
            return {"capsule_count": 0, "selected_input_bytes": 0}
        selected_bytes = 0
        for value in capsules:
            if isinstance(value, Mapping):
                try:
                    selected_bytes += SubproblemCapsule.from_mapping(value).serialized_bytes
                except ValueError:
                    continue
        return {"capsule_count": len(capsules), "selected_input_bytes": selected_bytes}

    @staticmethod
    def _capsule_input(arguments: Mapping[str, Any]) -> dict[str, int]:
        """Project capsule metadata without exporting selected content."""
        return {
            "fragment_count": len(arguments.get("fragments", ()))
            if isinstance(arguments.get("fragments", ()), list)
            else 0,
            "reference_count": len(arguments.get("authorized_references", ()))
            if isinstance(arguments.get("authorized_references", ()), list)
            else 0,
            "task_chars": len(arguments.get("task", "")) if isinstance(arguments.get("task"), str) else 0,
        }

    def _recursive_batch_output(self, result: Any) -> dict[str, object]:
        if isinstance(result, list):
            return {
                "status": "completed",
                "answer_count": len(result),
                "peak_child_concurrency": self._metrics.snapshot().peak_child_concurrency,
            }
        return {"status": "completed"}

    def _ensure_authorized(self) -> None:
        """Ensure the current recursive child execution remains authorized.

        Raises:
            ChildRuntimeAuthorizationError: If authorization is no longer valid.
        """
        if self._is_authorized is not None and not self._is_authorized():
            raise ChildRuntimeAuthorizationError("Turn is no longer authorized")

    def _ensure_call_authorized(self, batch_cancelled: Event | None) -> None:
        if batch_cancelled is not None and batch_cancelled.is_set():
            raise ChildRuntimeAuthorizationError("recursive child batch is no longer authorized")
        self._ensure_authorized()

    def _ensure_no_pending_batch_workers(self) -> None:
        """Prevent new work while prior child ownership is still unsettled."""
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
    ) -> None:
        """
        Emit a bounded recursive execution status event to the configured observer.

        Parameters:
            status (str): The execution status to emit.
            call_index (int): The recursive call index.
            recursive_depth (int): The recursive execution depth.
            started_at (float): The monotonic start time used to calculate completion duration.
            cleanup_status (str | None): The cleanup outcome associated with the call.
            failure_category (str | None): The failure classification, when the call failed.
        """
        if self._observer is None:
            return
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
        try:
            self._observer(Status("recursive", status, message))
        except Exception:
            return

    def _begin_call(self, prompt: str) -> RecursiveCallReservation:
        return self._make_reservation(prompt, self._reserve_call_indexes((prompt,))[0])

    def _begin_batch(self, prompts: tuple[str, ...]) -> tuple[RecursiveCallReservation, ...]:
        indexes = self._reserve_call_indexes(prompts)
        return tuple(self._make_reservation(prompt, index) for prompt, index in zip(prompts, indexes, strict=True))

    def _reserve_call_indexes(self, prompts: tuple[str, ...]) -> tuple[int, ...]:
        """
        Reserve call indexes for a batch of recursive prompts.

        Parameters:
            prompts (tuple[str, ...]): Prompts whose recursive call capacity should be reserved.

        Returns:
            tuple[int, ...]: Consecutive 1-based indexes assigned to the prompts.

        Raises:
            RuntimeError: If reserving the prompts would exceed the configured recursive call limit.
        """
        if not prompts:
            return ()
        with self._state.lock:
            if self._state.reserved_call_count + len(prompts) > self._options.max_calls:
                raise RuntimeError("recursive call budget exhausted")
            turn_budget = self._models.budget
            if turn_budget is not None:
                # A recursive request is both a Tool invocation and a child
                # admission. Reserve the Tool counter first so an attempted
                # request that fails the child ceiling is still accounted for.
                turn_budget.reserve(BudgetDimension.TOOL_CALLS, len(prompts))
                turn_budget.reserve(BudgetDimension.RECURSIVE_CHILDREN, len(prompts))
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
        return RecursiveCallReservation(prompt=prompt, call_index=call_index, child_depth=self._depth + 1)

    @staticmethod
    def _start_call(reservation: RecursiveCallReservation) -> _RecursiveCall:
        started_at = time.monotonic()
        span = start_turn_span(
            "RLM.recursive_call",
            inputs={
                "recursive_depth": reservation.child_depth,
                "call_index": reservation.call_index,
                "prompt_chars": len(reservation.prompt),
            },
        )
        return _RecursiveCall(reservation.call_index, reservation.child_depth, started_at, span)

    def _run_depth_fallback(self, prompt: str, call: _RecursiveCall) -> tuple[str, dict[str, object]]:
        if time.monotonic() >= self._deadline:
            raise TimeoutError("recursive child deadline exceeded")
        with self._state.lock:
            self._state.depth_fallback_count += 1
        self._metrics.record_depth_fallback()
        answer = self._plain_sub_lm(prompt)
        self._ensure_authorized()
        completion_outputs = self._record_completion(
            call,
            mode="depth_fallback",
            child_iterations=0,
            include_child_iterations=False,
        )
        return answer, completion_outputs

    def _acquire_child_lease(self, call_index: int, *, profile: str) -> ChildRuntimeLease:
        if self._child_runtime_factory is None:
            raise RuntimeError("recursive child runtime is unavailable")
        self._ensure_authorized()
        factory = self._child_runtime_factory
        try:
            signature = inspect.signature(factory)
        except (TypeError, ValueError):
            return factory(call_index)
        parameters = signature.parameters.values()
        accepts_profile = "profile" in signature.parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
        )
        return factory(call_index, profile=profile) if accepts_profile else factory(call_index)

    def _run_native_child(
        self,
        prompt: str,
        call: _RecursiveCall,
        lease: ChildRuntimeLease,
        batch_cancelled: Event | None = None,
    ) -> tuple[str, dict[str, object]]:
        """
        Execute a recursive child using the native RLM runtime.

        Parameters:
            prompt (str): The prompt to send to the child.
            call (_RecursiveCall): Reserved call metadata, including the child depth.
            lease (ChildRuntimeLease): Runtime lease used for child execution.
            batch_cancelled (Event | None): Optional event indicating that the enclosing batch was cancelled.

        Returns:
            tuple[str, dict[str, object]]: The child's bounded display text and completion metadata.
        """
        self._ensure_call_authorized(batch_cancelled)
        if time.monotonic() >= self._deadline:
            raise TimeoutError("recursive child deadline exceeded")
        child_models = self._models.fork_for_child(deadline=self._deadline)
        bind_budget = getattr(lease.interpreter, "bind_turn_budget", None)
        if callable(bind_budget):
            bind_budget(child_models.budget)
        child_executor = RecursiveRLMExecutor(
            models=child_models,
            options=self._options,
            child_runtime_factory=self._child_runtime_factory,
            deadline=self._deadline,
            depth=call.child_depth,
            state=self._state,
            metrics=self._metrics,
            observer=self._observer,
            is_authorized=lambda: (
                (batch_cancelled is None or not batch_cancelled.is_set())
                and (self._is_authorized is None or self._is_authorized())
            ),
            snapshot=self._snapshot,
            scheduler=self._scheduler,
        )
        child_signature: type[dspy.Signature] = RecursiveSubtaskSignature
        child_inputs: dict[str, Any] = {}
        if self._snapshot is not None:
            child_signature = RecursiveSessionSubtaskSignature
            # A remote Daytona interpreter cannot carry a raw dspy.History as
            # a per-iteration variable.  Keep that preferred value for the
            # in-process seam, but use the complete SandboxSerializable copy
            # when the interpreter advertises remote variable injection.
            child_history: dspy.History | CommittedSessionHistory = self._snapshot.history
            if self._snapshot.history_transport is not None and bool(
                getattr(lease.interpreter, "supports_sandbox_serializable_inputs", False)
            ):
                child_history = self._snapshot.history_transport
            child_inputs = {
                "request": self._snapshot.request,
                "history": child_history,
                "session_context": build_session_context_payload(
                    session_context=self._snapshot.session_context,
                    workspace=self._snapshot.workspace,
                    workspace_memory_digest=self._snapshot.workspace_memory_digest,
                ),
            }
        child = build_native_rlm(
            signature=child_signature,
            options=RLMOptions(
                max_iters=self._options.child_max_iters,
                max_llm_calls=self._options.child_max_llm_calls,
                max_output_chars=self._options.child_max_output_chars,
            ),
            tools=[child_executor.tool],
            sub_lm=child_models.sub_lm,
            verbose=False,
        )
        self._ensure_call_authorized(batch_cancelled)
        bind_output_contract(lease.interpreter, getattr(child, "signature", None))
        with dspy.context(
            lm=child_models.root_lm,
            # Same pinned JSON action protocol plus bounded corrective re-ask
            # as the Root lane; a child action must not die on one empty
            # provider response either.
            adapter=FleetJSONAdapter(
                deadline=self._deadline,
                wrap_up_seconds=child_models.reserve_seconds,
                budget=child_models.budget,
            ),
            callbacks=[
                _RLMTraceCallback(
                    root_lm=child_models.root_lm,
                    sub_lm=child_models.sub_lm,
                    recursive_depth=call.child_depth,
                    metrics=self._metrics,
                    deadline=self._deadline,
                )
            ],
            track_usage=True,
        ):
            child_acall = getattr(child, "acall", None)
            if callable(child_acall):
                # Native production children use the same caller-owned async
                # seam as Root.  Narrow deterministic doubles may expose only
                # ``__call__`` and remain supported for private tests.
                prediction = _invoke_async_child(
                    child_acall,
                    lease.interpreter,
                    prompt,
                    native=is_native_rlm(child),
                    deadline=self._deadline,
                    retain_pending=lambda pending: self._retain_pending_batch_futures({pending}),
                    extra_inputs=child_inputs,
                    scheduler=self._scheduler,
                )
            else:
                prediction = child(lease.interpreter, prompt=prompt)
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
        return result.display_text, completion_outputs

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
    ) -> None:
        cleanup_error: BaseException | None = None
        if lease is not None:
            try:
                lease.close()
                cleanup_status = "completed"
            except BaseException as exc:
                cleanup_error = _as_cleanup_error(exc)
                cleanup_status = "failed"
                with self._state.lock:
                    if self._state.fatal_cleanup_error is None:
                        self._state.fatal_cleanup_error = cleanup_error
        if cleanup_error is not None and not primary_failed:
            failed = True
            failure_category = "cleanup_failed"
            call.span.finish(
                phase_status="failed",
                outputs={"failure_category": failure_category},
            )
        elif not failed and completion_outputs is not None:
            call.span.finish(phase_status="completed", outputs=completion_outputs)
        self._emit_progress(
            "child_failed" if failed else "child_completed",
            call_index=call.call_index,
            recursive_depth=call.child_depth,
            started_at=call.started_at,
            cleanup_status=cleanup_status,
            failure_category=failure_category if failed else None,
        )
        if cleanup_error is not None and not primary_failed:
            raise cleanup_error

    def _call(self, prompt: str) -> str:
        """Execute the public prompt-only recursive Tool.

        ``child_profile`` is intentionally not part of this callable's
        signature: DSPy reflects Tool signatures into the sandbox namespace,
        and exposing an internal profile selector would let model code bypass
        the capsule admission contract.
        """
        return self._call_with_profile(prompt, child_profile="workspace-child")

    def _call_with_profile(self, prompt: str, *, child_profile: str) -> str:
        """
        Execute a bounded recursive query for the given prompt.

        Parameters:
            prompt (str): The trimmed textual prompt to delegate.

        Returns:
            str: The bounded answer produced by the recursive child query.

        Raises:
            ValueError: If the prompt is not text, is empty, or exceeds the configured character limit.
            RuntimeError: If the recursive call budget is exhausted or child runtime is unavailable.
            TimeoutError: If the recursive call deadline has expired.
        """
        prompt = _validate_recursive_prompt(prompt, max_chars=self._options.max_prompt_chars)
        if time.monotonic() >= self._deadline:
            raise TimeoutError("recursive call deadline exceeded")
        self._ensure_authorized()
        self._ensure_no_pending_batch_workers()
        reservation = self._begin_call(prompt)
        return self._run_reserved_call(reservation, child_profile=child_profile)

    def _run_reserved_call(
        self,
        reservation: RecursiveCallReservation,
        batch_cancelled: Event | None = None,
        *,
        child_profile: str = "workspace-child",
    ) -> str:
        """Run one already-reserved child call and always settle its lease."""
        prompt = reservation.prompt
        # Batched workers can sit queued behind the bounded pool. Do not open
        # a recursive span or acquire a child lease when that worker only
        # starts after the absolute Turn deadline.
        if time.monotonic() >= self._deadline:
            raise TimeoutError("recursive child deadline exceeded")
        call = self._start_call(reservation)
        self._emit_progress(
            "child_started",
            call_index=call.call_index,
            recursive_depth=call.child_depth,
            started_at=call.started_at,
        )
        lease: ChildRuntimeLease | None = None
        failed = False
        completion_outputs: dict[str, object] | None = None
        cleanup_status = "not_required"
        failure_category: str | None = None
        primary_failed = False
        child_started = False
        try:
            self._ensure_call_authorized(batch_cancelled)
            if call.child_depth > RLM_NATIVE_CHILD_DEPTH:
                answer, completion_outputs = self._run_depth_fallback(prompt, call)
            else:
                cleanup_status = "not_acquired"
                lease = self._acquire_child_lease(call.call_index, profile=child_profile)
                cleanup_status = "acquired"
                self._metrics.child_started()
                child_started = True
                self._ensure_call_authorized(batch_cancelled)
                answer, completion_outputs = self._run_native_child(prompt, call, lease, batch_cancelled)
            return answer
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
                )
            finally:
                if child_started:
                    self._metrics.child_completed()

    def _plain_sub_lm(self, prompt: str) -> str:
        """
        Generate a concise answer for a child subproblem using the configured sub-language model.

        Parameters:
                prompt (str): The bounded child subproblem to answer.

        Returns:
                str: The validated, bounded answer.
        """
        predictor = dspy.Predict(RecursiveSubtaskSignature)
        with dspy.context(
            lm=self._models.sub_lm,
            adapter=FleetJSONAdapter(deadline=self._deadline, budget=self._models.budget),
            callbacks=[
                _RLMTraceCallback(
                    root_lm=self._models.root_lm,
                    sub_lm=self._models.sub_lm,
                    recursive_depth=self._depth + 1,
                    metrics=self._metrics,
                    deadline=self._deadline,
                )
            ],
            track_usage=True,
        ):
            prediction = predictor(prompt=prompt)
        result = prediction_result(
            prediction,
            RecursiveSubtaskSignature,
            schema_id="fleet.recursive-subtask",
            schema_version="1",
            max_output_chars=self._options.child_max_output_chars,
        )
        return result.display_text

    def _call_batched(self, prompts: list[str]) -> list[str]:
        """Execute independent recursive child calls with bounded fan-out."""
        if not isinstance(prompts, list):
            raise ValueError("rlm_query_batched prompts must be a list")
        normalized = tuple(
            _validate_recursive_prompt(prompt, max_chars=self._options.max_prompt_chars) for prompt in prompts
        )
        if not normalized:
            raise ValueError("rlm_query_batched prompts must not be empty")
        if time.monotonic() >= self._deadline:
            raise TimeoutError("recursive call deadline exceeded")
        self._ensure_authorized()
        self._ensure_no_pending_batch_workers()
        self._metrics.record_recursive_batch()
        reservations = self._begin_batch(normalized)
        results = run_reserved_batch(
            reservations,
            execute=self._run_reserved_call,
            deadline_monotonic=self._deadline,
            max_parallel=self._options.max_parallel_children,
            on_retain_running=self._retain_pending_batch_futures,
        )
        self.raise_if_cleanup_failed()
        return results


__all__ = [
    "RLM_NATIVE_CHILD_DEPTH",
    "CapsuleResult",
    "CapsuleStatus",
    "ChildAsyncScheduler",
    "ChildOutcome",
    "ChildRuntimeAuthorizationError",
    "ChildRuntimeCleanupError",
    "ChildRuntimeFactory",
    "ChildRuntimeLease",
    "DelegationMetrics",
    "DelegationMetricsSnapshot",
    "RecursiveBatchError",
    "RecursiveCallReservation",
    "RecursiveCallSummary",
    "RecursiveRLMExecutor",
    "RecursiveRLMOptions",
    "RecursiveSessionSnapshot",
    "RecursiveSessionSubtaskSignature",
    "RecursiveSubtaskSignature",
    "SubproblemCapsule",
    "TokenUsageStatus",
    "build_recursive_session_snapshot",
    "normalize_lm_token_usage",
    "run_reserved_batch",
]
