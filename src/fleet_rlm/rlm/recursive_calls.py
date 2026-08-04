"""Bounded native DSPy child-RLM calls for the Root REPL harness."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import dspy

from fleet_rlm.observability.failure_diagnostics import trace_failure_category
from fleet_rlm.observability.turn_tracing import start_turn_span
from fleet_rlm.rlm.child_runtime import (
    ChildRuntimeAuthorizationError,
    ChildRuntimeCleanupError,
    ChildRuntimeFactory,
    ChildRuntimeLease,
)
from fleet_rlm.rlm.dspy_contract import RLMOptions, _RLMTraceCallback, build_native_rlm, prediction_result
from fleet_rlm.rlm.errors import RLMConfigError
from fleet_rlm.rlm.events import Status
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.tool_observer import ToolEventView, ToolObserver, observe_tool


@dataclass(frozen=True, slots=True)
class RecursiveRLMOptions:
    """Invocation limits for the custom recursive RLM Tool."""

    enabled: bool = False
    max_depth: int = 2
    max_calls: int = 4
    max_prompt_chars: int = 50_000
    child_max_iterations: int = 8
    child_max_llm_calls: int = 12
    child_max_output_chars: int = 4_000

    def __post_init__(self) -> None:
        for name, value in (
            ("max_depth", self.max_depth),
            ("max_calls", self.max_calls),
            ("max_prompt_chars", self.max_prompt_chars),
            ("child_max_iterations", self.child_max_iterations),
            ("child_max_llm_calls", self.child_max_llm_calls),
            ("child_max_output_chars", self.child_max_output_chars),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise RLMConfigError(f"{name} must be a positive integer, got {value!r}")


@dataclass(frozen=True, slots=True)
class RecursiveCallSummary:
    """Bounded aggregate evidence for one Root invocation."""

    call_count: int
    delegated_prompt_chars: int
    maximum_prompt_chars: int
    child_iterations: int
    depth_fallback_count: int
    termination_modes: tuple[str, ...]


@dataclass(slots=True)
class _RecursiveState:
    call_count: int = 0
    delegated_prompt_chars: int = 0
    maximum_prompt_chars: int = 0
    child_iterations: int = 0
    depth_fallback_count: int = 0
    termination_modes: list[str] = field(default_factory=list)
    fatal_cleanup_error: BaseException | None = None


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
    return max(0, min(int(value), _MAX_PROGRESS_INTEGER))


def _recursive_failure_category(exc: BaseException) -> str:
    if isinstance(exc, ChildRuntimeAuthorizationError):
        return "unauthorized"
    if isinstance(exc, ChildRuntimeCleanupError):
        return "cleanup_failed"
    category = trace_failure_category(exc)
    return category if category in {"timeout", "unauthorized", "cleanup_failed"} else "child_failed"


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
        observer: ToolObserver | None = None,
        is_authorized: Callable[[], bool] | None = None,
    ) -> None:
        self._models = models
        self._options = options
        self._child_runtime_factory = child_runtime_factory
        self._deadline = deadline
        self._depth = depth
        self._state = state or _RecursiveState()
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

    @property
    def tool(self) -> dspy.Tool:
        """Return the custom Tool accepted by the native RLM constructor."""
        return self._tool

    def summary(self) -> RecursiveCallSummary:
        """Return bounded aggregate recursion metadata without content."""
        return RecursiveCallSummary(
            call_count=self._state.call_count,
            delegated_prompt_chars=self._state.delegated_prompt_chars,
            maximum_prompt_chars=self._state.maximum_prompt_chars,
            child_iterations=self._state.child_iterations,
            depth_fallback_count=self._state.depth_fallback_count,
            termination_modes=tuple(self._state.termination_modes),
        )

    def raise_if_cleanup_failed(self) -> None:
        """Prevent a typed Root prediction from committing after failed child cleanup."""
        if self._state.fatal_cleanup_error is not None:
            raise RuntimeError("recursive child cleanup failed") from self._state.fatal_cleanup_error

    def _recursive_output(self, _result: Any) -> dict[str, object]:
        if self._last_completion is None:
            return {"status": "completed"}
        return dict(self._last_completion)

    def _ensure_authorized(self) -> None:
        if self._is_authorized is not None and not self._is_authorized():
            raise ChildRuntimeAuthorizationError("Turn is no longer authorized")

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

    def _call(self, prompt: str) -> str:
        if not isinstance(prompt, str):
            raise ValueError("rlm_query prompt must be text")
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("rlm_query prompt must not be empty")
        if len(prompt) > self._options.max_prompt_chars:
            raise ValueError("rlm_query prompt exceeds the configured character bound")
        if self._state.call_count >= self._options.max_calls:
            raise RuntimeError("recursive call budget exhausted")
        if time.monotonic() >= self._deadline:
            raise TimeoutError("recursive call deadline exceeded")
        self._ensure_authorized()

        self._state.call_count += 1
        call_index = self._state.call_count
        self._state.delegated_prompt_chars += len(prompt)
        self._state.maximum_prompt_chars = max(self._state.maximum_prompt_chars, len(prompt))
        child_depth = self._depth + 1
        started_at = time.monotonic()
        span = start_turn_span(
            "RLM.recursive_call",
            inputs={
                "recursive_depth": child_depth,
                "call_index": call_index,
                "prompt_chars": len(prompt),
            },
        )
        self._emit_progress(
            "child_started",
            call_index=call_index,
            recursive_depth=child_depth,
            started_at=started_at,
        )
        lease: ChildRuntimeLease | None = None
        failed = False
        completion_outputs: dict[str, object] | None = None
        cleanup_status = "not_required"
        failure_category: str | None = None
        primary_failed = False
        try:
            self._ensure_authorized()
            if child_depth >= self._options.max_depth:
                self._state.depth_fallback_count += 1
                answer = self._plain_sub_lm(prompt)
                self._ensure_authorized()
                self._state.termination_modes.append("depth_fallback")
                completion_outputs = {"termination_mode": "depth_fallback"}
                self._last_completion = {
                    "status": "completed",
                    "call_index": call_index,
                    "recursive_depth": child_depth,
                    "child_iterations": 0,
                    "termination_mode": "depth_fallback",
                }
                return answer

            cleanup_status = "not_acquired"
            if self._child_runtime_factory is None:
                raise RuntimeError("recursive child runtime is unavailable")
            self._ensure_authorized()
            lease = self._child_runtime_factory(call_index)
            cleanup_status = "acquired"
            self._ensure_authorized()
            child_executor = RecursiveRLMExecutor(
                models=self._models,
                options=self._options,
                child_runtime_factory=self._child_runtime_factory,
                deadline=self._deadline,
                depth=child_depth,
                state=self._state,
                observer=self._observer,
                is_authorized=self._is_authorized,
            )
            child = build_native_rlm(
                signature=RecursiveSubtaskSignature,
                options=RLMOptions(
                    max_iterations=self._options.child_max_iterations,
                    max_llm_calls=self._options.child_max_llm_calls,
                    max_output_chars=self._options.child_max_output_chars,
                ),
                tools=[child_executor.tool],
                sub_lm=self._models.sub_lm,
                interpreter=lease.interpreter,
                verbose=False,
            )
            self._ensure_authorized()
            with dspy.context(
                lm=self._models.root_lm,
                adapter=dspy.JSONAdapter(),
                callbacks=[
                    _RLMTraceCallback(
                        root_lm=self._models.root_lm,
                        sub_lm=self._models.sub_lm,
                        recursive_depth=self._depth + 1,
                    )
                ],
                track_usage=True,
            ):
                prediction = child(prompt=prompt)
            result = prediction_result(
                prediction,
                RecursiveSubtaskSignature,
                schema_id="fleet.recursive-subtask",
                schema_version="1",
                max_output_chars=self._options.child_max_output_chars,
            )
            self._ensure_authorized()
            trajectory = getattr(prediction, "trajectory", ())
            child_iterations = len(trajectory) if isinstance(trajectory, list) else 0
            self._state.child_iterations += child_iterations
            mode = (
                "native_extraction_fallback"
                if getattr(prediction, "final_reasoning", None) == "Extract forced final output"
                else "typed_submit"
            )
            self._state.termination_modes.append(mode)
            completion_outputs = {"termination_mode": mode, "child_iterations": child_iterations}
            self._last_completion = {
                "status": "completed",
                "call_index": call_index,
                "recursive_depth": child_depth,
                "child_iterations": child_iterations,
                "termination_mode": mode,
            }
            return result.display_text
        except BaseException as exc:
            failed = True
            primary_failed = True
            failure_category = _recursive_failure_category(exc)
            if isinstance(exc, ChildRuntimeCleanupError) and self._state.fatal_cleanup_error is None:
                self._state.fatal_cleanup_error = exc
            self._state.termination_modes.append("child_error")
            span.finish(
                phase_status="failed",
                outputs={"failure_category": trace_failure_category(exc)},
            )
            raise
        finally:
            cleanup_error: BaseException | None = None
            if lease is not None:
                try:
                    lease.close()
                    cleanup_status = "completed"
                except BaseException as exc:
                    cleanup_error = exc
                    cleanup_status = "failed"
                    if self._state.fatal_cleanup_error is None:
                        self._state.fatal_cleanup_error = exc
            if cleanup_error is not None and not primary_failed:
                failed = True
                failure_category = "cleanup_failed"
                span.finish(
                    phase_status="failed",
                    outputs={"failure_category": failure_category},
                )
            elif not failed and completion_outputs is not None:
                span.finish(phase_status="completed", outputs=completion_outputs)
            self._emit_progress(
                "child_failed" if failed else "child_completed",
                call_index=call_index,
                recursive_depth=child_depth,
                started_at=started_at,
                cleanup_status=cleanup_status,
                failure_category=failure_category if failed else None,
            )
            if cleanup_error is not None and not primary_failed:
                raise cleanup_error

    def _plain_sub_lm(self, prompt: str) -> str:
        """Use the configured Sub LM at the depth cap.

        ``dspy.Predict`` and ``dspy.context`` are native DSPy Module/configuration
        surfaces (`dspy/primitives/module.py:94` and
        `dspy/dsp/utils/settings.py:216-235`).

        Args:
            prompt: The bounded child subproblem.

        Returns:
            The validated concise Sub LM answer.
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


__all__ = [
    "ChildRuntimeFactory",
    "RecursiveCallSummary",
    "RecursiveRLMExecutor",
    "RecursiveRLMOptions",
    "RecursiveSubtaskSignature",
]
