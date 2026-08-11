"""Bounded native DSPy child-RLM calls for the Root REPL harness."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait
from contextvars import copy_context
from dataclasses import dataclass, field
from threading import Event, RLock
from typing import Any, cast

import dspy

from fleet_rlm.observability.failure_diagnostics import trace_failure_category
from fleet_rlm.observability.turn_tracing import start_turn_span
from fleet_rlm.rlm.child_runtime import (
    ChildRuntimeAuthorizationError,
    ChildRuntimeCleanupError,
    ChildRuntimeFactory,
    ChildRuntimeLease,
)
from fleet_rlm.rlm.delegation_metrics import DelegationMetrics, DelegationMetricsSnapshot
from fleet_rlm.rlm.dspy_contract import RLMOptions, _RLMTraceCallback, build_native_rlm, prediction_result
from fleet_rlm.rlm.errors import RLMConfigError
from fleet_rlm.rlm.events import Status
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.tool_observer import ToolEventView, ToolObserver, observe_tool

# Fleet supports exactly one native recursive child level followed by a
# bounded Sub fallback. This is a product invariant owned here, not an
# operator-facing policy knob.
RLM_NATIVE_CHILD_DEPTH = 1

# Wait bound for detached child workers retained after batch settlement. A
# provider, LM call, or interpreter that never returns becomes a cleanup
# failure instead of an unbounded root-cleanup hang; when the worker later
# unwinds, its lease close still runs through the factory's late-cleanup lane.
_PENDING_BATCH_WAIT_TIMEOUT_S = 60.0


@dataclass(frozen=True, slots=True)
class RecursiveRLMOptions:
    """Invocation limits for the custom recursive RLM Tool."""

    enabled: bool = False
    max_calls: int = 4
    max_prompt_chars: int = 50_000
    child_max_iterations: int = 8
    child_max_llm_calls: int = 12
    child_max_output_chars: int = 4_000
    max_parallel_children: int = 2

    def __post_init__(self) -> None:
        for name, value in (
            ("max_calls", self.max_calls),
            ("max_prompt_chars", self.max_prompt_chars),
            ("child_max_iterations", self.child_max_iterations),
            ("child_max_llm_calls", self.child_max_llm_calls),
            ("child_max_output_chars", self.child_max_output_chars),
            ("max_parallel_children", self.max_parallel_children),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise RLMConfigError(f"{name} must be a positive integer, got {value!r}")
        if self.max_parallel_children > 8:
            raise RLMConfigError("max_parallel_children must not exceed 8")


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


@dataclass(slots=True)
class _RecursiveState:
    lock: RLock = field(default_factory=RLock, repr=False)
    call_count: int = 0
    delegated_prompt_chars: int = 0
    maximum_prompt_chars: int = 0
    child_iterations: int = 0
    depth_fallback_count: int = 0
    termination_modes: list[str] = field(default_factory=list)
    fatal_cleanup_error: BaseException | None = None
    pending_batch_futures: list[Future[str]] = field(default_factory=list, repr=False)
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
class _RecursiveReservation:
    call_index: int
    child_depth: int


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
        self._last_completion: dict[str, object] | None = None
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

    @property
    def tool(self) -> dspy.Tool:
        """Return the custom Tool accepted by the native RLM constructor."""
        return self._tool

    @property
    def batched_tool(self) -> dspy.Tool:
        """Return the Root-only batched recursive Tool."""
        return self._batched_tool

    @property
    def metrics(self) -> DelegationMetrics:
        """Return the shared run-scoped delegation accumulator."""
        return self._metrics

    def summary(self) -> RecursiveCallSummary:
        """Return bounded aggregate recursion metadata without content."""
        with self._state.lock:
            snapshot = self._metrics.snapshot()
            return RecursiveCallSummary(
                call_count=self._state.call_count,
                delegated_prompt_chars=self._state.delegated_prompt_chars,
                maximum_prompt_chars=self._state.maximum_prompt_chars,
                child_iterations=self._state.child_iterations,
                depth_fallback_count=self._state.depth_fallback_count,
                termination_modes=tuple(self._state.termination_modes),
                recursive_batch_calls=snapshot.recursive_batch_calls,
                recursive_children_started=snapshot.recursive_children_started,
                recursive_children_completed=snapshot.recursive_children_completed,
                peak_child_concurrency=snapshot.peak_child_concurrency,
                delegation_metrics=snapshot,
            )

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

    def _retain_pending_batch_futures(self, futures: set[Future[str]]) -> None:
        pending = [future for future in futures if not future.done()]
        if not pending:
            return
        with self._state.lock:
            self._state.pending_batch_futures.extend(pending)

        def settled(future: Future[str]) -> None:
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

    def _begin_call(self, prompt: str) -> _RecursiveReservation:
        return self._make_reservation(self._reserve_call_indexes((prompt,))[0])

    def _begin_batch(self, prompts: tuple[str, ...]) -> tuple[_RecursiveReservation, ...]:
        indexes = self._reserve_call_indexes(prompts)
        return tuple(self._make_reservation(index) for index in indexes)

    def _reserve_call_indexes(self, prompts: tuple[str, ...]) -> tuple[int, ...]:
        if not prompts:
            return ()
        with self._state.lock:
            if self._state.call_count + len(prompts) > self._options.max_calls:
                raise RuntimeError("recursive call budget exhausted")
            start = self._state.call_count + 1
            self._state.call_count += len(prompts)
            self._state.delegated_prompt_chars += sum(len(prompt) for prompt in prompts)
            self._state.maximum_prompt_chars = max(
                self._state.maximum_prompt_chars,
                *(len(prompt) for prompt in prompts),
            )
        for _prompt in prompts:
            self._metrics.record_recursive_call()
        return tuple(range(start, start + len(prompts)))

    def _make_reservation(self, call_index: int) -> _RecursiveReservation:
        return _RecursiveReservation(call_index, self._depth + 1)

    @staticmethod
    def _start_call(prompt: str, reservation: _RecursiveReservation) -> _RecursiveCall:
        started_at = time.monotonic()
        span = start_turn_span(
            "RLM.recursive_call",
            inputs={
                "recursive_depth": reservation.child_depth,
                "call_index": reservation.call_index,
                "prompt_chars": len(prompt),
            },
        )
        return _RecursiveCall(reservation.call_index, reservation.child_depth, started_at, span)

    def _run_depth_fallback(self, prompt: str, call: _RecursiveCall) -> tuple[str, dict[str, object]]:
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

    def _acquire_child_lease(self, call_index: int) -> ChildRuntimeLease:
        if self._child_runtime_factory is None:
            raise RuntimeError("recursive child runtime is unavailable")
        self._ensure_authorized()
        return self._child_runtime_factory(call_index)

    def _run_native_child(
        self,
        prompt: str,
        call: _RecursiveCall,
        lease: ChildRuntimeLease,
        batch_cancelled: Event | None = None,
    ) -> tuple[str, dict[str, object]]:
        self._ensure_call_authorized(batch_cancelled)
        child_models = self._models.fork_for_child(deadline=self._deadline)
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
        )
        child = build_native_rlm(
            signature=RecursiveSubtaskSignature,
            options=RLMOptions(
                max_iterations=self._options.child_max_iterations,
                max_llm_calls=self._options.child_max_llm_calls,
                max_output_chars=self._options.child_max_output_chars,
            ),
            tools=[child_executor.tool],
            sub_lm=child_models.sub_lm,
            verbose=False,
        )
        self._ensure_call_authorized(batch_cancelled)
        with dspy.context(
            lm=child_models.root_lm,
            adapter=dspy.JSONAdapter(),
            callbacks=[
                _RLMTraceCallback(
                    root_lm=child_models.root_lm,
                    sub_lm=child_models.sub_lm,
                    recursive_depth=call.child_depth,
                    metrics=self._metrics,
                )
            ],
            track_usage=True,
        ):
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
        mode = (
            "native_extraction_fallback"
            if getattr(prediction, "final_reasoning", None) == "Extract forced final output"
            else "typed_submit"
        )
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
        call = self._begin_call(prompt)
        return self._run_reserved_call(prompt, call)

    def _run_reserved_call(
        self,
        prompt: str,
        reservation: _RecursiveReservation,
        batch_cancelled: Event | None = None,
    ) -> str:
        """Run one already-reserved child call and always settle its lease."""
        call = self._start_call(prompt, reservation)
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
                lease = self._acquire_child_lease(call.call_index)
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
            adapter=dspy.JSONAdapter(),
            callbacks=[
                _RLMTraceCallback(
                    root_lm=self._models.root_lm,
                    sub_lm=self._models.sub_lm,
                    recursive_depth=self._depth + 1,
                    metrics=self._metrics,
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
        calls = self._begin_batch(normalized)
        workers = min(self._options.max_parallel_children, len(calls))
        results: list[str | None] = [None] * len(calls)
        batch_cancelled = Event()
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fleet-rlm-child")
        futures: list[Future[str]] = []
        try:
            try:
                for prompt, call in zip(normalized, calls, strict=True):
                    submitted = pool.submit(
                        copy_context().run,
                        self._run_reserved_call,
                        prompt,
                        call,
                        batch_cancelled,
                    )
                    futures.append(cast(Future[str], submitted))
            except BaseException:
                batch_cancelled.set()
                pending = {future for future in futures if not future.done()}
                for future in pending:
                    future.cancel()
                self._retain_pending_batch_futures(pending)
                raise
            remaining = max(0.0, self._deadline - time.monotonic())
            raw_done, raw_not_done = wait(futures, timeout=remaining, return_when=FIRST_EXCEPTION)
            done = raw_done
            not_done = raw_not_done
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
                self._retain_pending_batch_futures(not_done)
                if failures:
                    raise RecursiveBatchError() from failures[0]
                raise TimeoutError("recursive child batch deadline exceeded")
            if failures:
                raise RecursiveBatchError() from failures[0]
            for index, future in enumerate(futures):
                results[index] = future.result(timeout=0)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        self.raise_if_cleanup_failed()
        if any(answer is None for answer in results):
            raise RecursiveBatchError()
        return [answer for answer in results if answer is not None]


def _future_failures(futures: set[Future[str]]) -> list[BaseException]:
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


class RecursiveBatchError(RuntimeError):
    """Bounded all-or-nothing failure for one recursive batch."""

    def __init__(self) -> None:
        super().__init__("recursive child batch failed")


__all__ = [
    "RLM_NATIVE_CHILD_DEPTH",
    "ChildRuntimeFactory",
    "RecursiveBatchError",
    "RecursiveCallSummary",
    "RecursiveRLMExecutor",
    "RecursiveRLMOptions",
    "RecursiveSubtaskSignature",
]
