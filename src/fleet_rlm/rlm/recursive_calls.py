"""Bounded native DSPy child-RLM calls for the Root REPL harness."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import dspy

from fleet_rlm.rlm.dspy_contract import RLMOptions, _RLMTraceCallback, build_native_rlm, prediction_result
from fleet_rlm.rlm.errors import RLMConfigError
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.tool_observer import ToolEventView, ToolObserver, observe_tool

ChildInterpreterFactory = Callable[[], Any | None]


@dataclass(frozen=True, slots=True)
class RecursiveRLMOptions:
    """Invocation limits for the custom recursive RLM Tool."""

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


def _recursive_output(_result: Any) -> dict[str, str]:
    return {"status": "completed"}


class RecursiveRLMExecutor:
    """Execute bounded recursive child RLMs from a synchronous DSPy worker.

    The native RLM constructor and synchronous ``forward`` surface are defined by
    ``dspy/predict/rlm.py:104-159`` and ``dspy/predict/rlm.py:624-675``. The
    caller owns the interpreter lifecycle; this coordinator only creates and
    closes one fresh child interpreter per Tool call.

    Args:
        models: Root and Sub LMs selected by Fleet policy.
        options: Recursion limits for this invocation.
        child_interpreter_factory: Factory for a fresh interpreter context.
        deadline: Monotonic Turn deadline.
        depth: Current RLM depth, where the Root is zero.
        state: Shared mutable aggregate counters for the invocation.
        observer: Optional bounded Tool observer for nested calls.

    Returns:
        An executor whose ``tool`` can be injected into a native ``dspy.RLM``.
    """

    def __init__(
        self,
        *,
        models: RLMModelBundle,
        options: RecursiveRLMOptions,
        child_interpreter_factory: ChildInterpreterFactory | None,
        deadline: float,
        depth: int = 0,
        state: _RecursiveState | None = None,
        observer: ToolObserver | None = None,
    ) -> None:
        self._models = models
        self._options = options
        self._child_interpreter_factory = child_interpreter_factory
        self._deadline = deadline
        self._depth = depth
        self._state = state or _RecursiveState()
        self._observer = observer
        raw_tool = dspy.Tool(
            self._call,
            name="rlm_query",
            desc=(
                "Solve one self-contained bounded semantic subproblem. Pass only selected data, "
                "not the complete Turn, history, Attachment, or Workspace. Store the concise answer."
            ),
        )
        self._tool = (
            observe_tool(
                raw_tool,
                observer,
                ToolEventView(input_projection=_recursive_input, output_projection=_recursive_output),
            )
            if observer is not None
            else raw_tool
        )

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

        self._state.call_count += 1
        self._state.delegated_prompt_chars += len(prompt)
        self._state.maximum_prompt_chars = max(self._state.maximum_prompt_chars, len(prompt))
        child_depth = self._depth + 1
        if child_depth >= self._options.max_depth:
            self._state.depth_fallback_count += 1
            answer = self._plain_sub_lm(prompt)
            self._state.termination_modes.append("depth_fallback")
            return answer

        interpreter = self._child_interpreter_factory() if self._child_interpreter_factory is not None else None
        child_executor = RecursiveRLMExecutor(
            models=self._models,
            options=self._options,
            child_interpreter_factory=self._child_interpreter_factory,
            deadline=self._deadline,
            depth=child_depth,
            state=self._state,
            observer=self._observer,
        )
        failed = False
        try:
            child = build_native_rlm(
                signature=RecursiveSubtaskSignature,
                options=RLMOptions(
                    max_iterations=self._options.child_max_iterations,
                    max_llm_calls=self._options.child_max_llm_calls,
                    max_output_chars=self._options.child_max_output_chars,
                ),
                tools=[child_executor.tool],
                sub_lm=self._models.sub_lm,
                interpreter=interpreter,
                verbose=False,
            )
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
            trajectory = getattr(prediction, "trajectory", ())
            self._state.child_iterations += len(trajectory) if isinstance(trajectory, list) else 0
            mode = (
                "native_extraction_fallback"
                if getattr(prediction, "final_reasoning", None) == "Extract forced final output"
                else "typed_submit"
            )
            self._state.termination_modes.append(mode)
            return result.display_text
        except Exception:
            failed = True
            self._state.termination_modes.append("child_error")
            raise
        finally:
            if interpreter is not None:
                try:
                    interpreter.shutdown()
                except Exception:
                    if not failed:
                        raise

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
    "ChildInterpreterFactory",
    "RecursiveCallSummary",
    "RecursiveRLMExecutor",
    "RecursiveRLMOptions",
    "RecursiveSubtaskSignature",
]
