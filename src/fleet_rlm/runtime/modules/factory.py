"""Factory functions and shared config for constructing DSPy runtime modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import dspy
from dspy.predict.rlm import _strip_code_fences

_DSPY_RLM_BASE: Any = dspy.RLM

# Threshold (chars) above which a turn auto-routes to the RLM sandbox.
VARIABLE_MODE_THRESHOLD = 32_000

# Lower max_output_chars for sandbox-heavy paths forces the LLM to work
# through REPL variables (peek, grep, sub_rlm) instead of printing large
# output back into its own context.
VARIABLE_MODE_MAX_OUTPUT_CHARS = 5_000


def interpreter_delegation_tools(interpreter: Any | None) -> list[Any]:
    """Collect the interpreter's recursive delegation callables as plain tools."""
    tools: list[Any] = []
    if interpreter is None:
        return tools
    for attr_name in ("sub_rlm", "sub_rlm_batched"):
        fn = getattr(interpreter, attr_name, None)
        if callable(fn):
            tools.append(fn)
    return tools


class _EmittingAction(dspy.Module):
    """Wraps the RLM's ``generate_action`` predictor to stream each step.

    Emits ``rlm_reasoning`` and ``rlm_tool_call`` as soon as the action LM
    call returns, before sandbox execution starts, so chat clients see
    progress in real time.
    """

    def __init__(self, inner: Any, emit: Callable[[dict[str, Any]], None]) -> None:
        super().__init__()
        self._inner = inner
        self._emit = emit
        self.current_iteration = 0

    def __getattr__(self, name: str) -> Any:
        # Delegate predictor attributes (signature, demos, ...) to the wrapped
        # Predict so optimizers and introspection keep working.
        inner = self.__dict__.get("_inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)

    @staticmethod
    def _iteration_index(kwargs: dict[str, Any]) -> int:
        raw = str(kwargs.get("iteration", "") or "")
        head = raw.split("/", 1)[0].strip()
        try:
            return max(0, int(head) - 1)
        except ValueError:
            return 0

    def _emit_action(self, prediction: Any, iteration: int) -> None:
        reasoning = str(getattr(prediction, "reasoning", "") or "")
        code_raw = str(getattr(prediction, "code", "") or "")
        try:
            code = _strip_code_fences(code_raw)
        except SyntaxError:
            code = code_raw
        self._emit(
            {
                "phase": "rlm_reasoning",
                "iteration": iteration,
                "reasoning": reasoning,
                "code_preview": code[:500],
            }
        )
        self._emit(
            {
                "phase": "rlm_tool_call",
                "iteration": iteration,
                "code": code,
                "tool_name": "repl_execute",
            }
        )

    def forward(self, **kwargs: Any) -> Any:
        self.current_iteration = self._iteration_index(kwargs)
        prediction = self._inner(**kwargs)
        self._emit_action(prediction, self.current_iteration)
        return prediction

    async def aforward(self, **kwargs: Any) -> Any:
        self.current_iteration = self._iteration_index(kwargs)
        prediction = await self._inner.acall(**kwargs)
        self._emit_action(prediction, self.current_iteration)
        return prediction


class _StreamingRLM(_DSPY_RLM_BASE):
    """``dspy.RLM`` that streams per-iteration progress via interpreter callback.

    Instead of re-implementing ``_execute_iteration``, this subclass hooks two
    stable seams: the ``generate_action`` predictor (action streaming) and
    ``_process_execution_result`` (sandbox output streaming).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.generate_action = _EmittingAction(self.generate_action, self._emit_step)

    def _emit_step(self, payload: dict[str, Any]) -> None:
        interpreter = getattr(self, "_interpreter", None)
        if interpreter is None:
            return
        callback = getattr(interpreter, "_turn_step_callback", None)
        if not callable(callback):
            return
        try:
            callback(payload)
        except Exception:
            return

    def _process_execution_result(
        self,
        pred: Any,
        code: str,
        result: Any,
        history: Any,
        output_field_names: list[str],
    ) -> Any:
        processed = super()._process_execution_result(pred, code, result, history, output_field_names)
        if not isinstance(processed, dspy.Prediction):
            if isinstance(result, list):
                output = "\n".join(map(str, result))
            else:
                output = str(result) if result else ""
            iteration = getattr(self.generate_action, "current_iteration", 0)
            self._emit_step(
                {
                    "phase": "rlm_tool_result",
                    "iteration": iteration,
                    "output": output,
                    "observation": output,
                    "tool_name": "repl_execute",
                }
            )
        return processed

    def _execute_code(self, repl: Any, code: str, input_args: dict[str, Any]) -> Any:
        from fleet_rlm.integrations.observability.mlflow_context import (
            _bounded_value,
            mlflow_child_span,
            set_mlflow_span_outputs,
        )

        iteration = getattr(self.generate_action, "current_iteration", 0)
        with mlflow_child_span(
            "fleet_rlm.rlm_repl_execute",
            span_type="TOOL",
            attributes={
                "fleet_rlm.tool_name": "repl_execute",
                "fleet_rlm.rlm_iteration": str(iteration),
                "fleet_rlm.execution_origin": "dspy_rlm_execute_code",
            },
            inputs={
                "tool_name": "repl_execute",
                "iteration": iteration,
                "code": _bounded_value(code),
                "variable_names": sorted(str(key) for key in input_args),
            },
        ) as span:
            result = super()._execute_code(repl, code, input_args)
            failed = isinstance(result, str) and result.startswith("[Error]")
            set_mlflow_span_outputs(
                span,
                {
                    "status": "error" if failed else "ok",
                    "result": _bounded_value(result),
                },
            )
            if failed and span is not None:
                set_status = getattr(span, "set_status", None)
                if callable(set_status):
                    set_status("ERROR")
            return result

    def _prepare_serializable_vars(self, input_args: dict[str, Any], repl: Any) -> dict[str, Any]:
        from dspy.predict.rlm import SandboxSerializable

        from fleet_rlm.integrations.observability.mlflow_context import (
            mlflow_child_span,
            set_mlflow_span_outputs,
        )

        serializable_names = sorted(
            str(name) for name, value in input_args.items() if isinstance(value, SandboxSerializable)
        )
        if not serializable_names:
            return super()._prepare_serializable_vars(input_args, repl)

        with mlflow_child_span(
            "fleet_rlm.rlm_prepare_variables",
            span_type="CHAIN",
            attributes={
                "fleet_rlm.serializable_variable_count": str(len(serializable_names)),
                "fleet_rlm.execution_origin": "dspy_rlm_prepare_serializable_vars",
            },
            inputs={"serializable_variables": serializable_names},
        ) as span:
            regular_args = super()._prepare_serializable_vars(input_args, repl)
            set_mlflow_span_outputs(
                span,
                {
                    "regular_variable_count": len(regular_args),
                    "regular_variables": sorted(str(key) for key in regular_args),
                },
            )
            return regular_args

    def _record_trajectory_spans(self, result: Any) -> None:
        try:
            from fleet_rlm.integrations.observability.mlflow_context import record_rlm_trajectory_spans

            record_rlm_trajectory_spans(getattr(result, "trajectory", None))
        except Exception:
            return

    def forward(self, **input_args: Any) -> dspy.Prediction:
        result = super().forward(**input_args)
        self._record_trajectory_spans(result)
        return result

    async def aforward(self, **input_args: Any) -> dspy.Prediction:
        result = await super().aforward(**input_args)
        self._record_trajectory_spans(result)
        return result


class _NoCallbackRLM(_StreamingRLM):
    """RLM variant for REPL-only tasks where host semantic callbacks are disabled."""

    def _build_signatures(self) -> tuple[Any, Any]:
        action_sig, extract_sig = super()._build_signatures()
        instructions = str(action_sig.instructions)
        instructions = instructions.replace(
            "- `llm_query(prompt)` - query a sub-LLM (~500K char capacity) for semantic analysis\n",
            "",
        ).replace(
            "- `llm_query_batched(prompts)` - query multiple prompts concurrently (much faster for multiple queries)\n",
            "",
        )
        instructions = instructions.replace(
            "4. USE llm_query FOR SEMANTICS - String matching finds WHERE things are; "
            "llm_query understands WHAT things mean.",
            "4. USE PYTHON INSPECTION - Extract headings, links, counts, samples, and sections with code; "
            "semantic callbacks are disabled for this run.",
        )
        instructions = instructions.replace(
            f"You have max {self.max_llm_calls} sub-LLM calls. When done, call SUBMIT() with your output.",
            "Semantic callbacks are disabled. When done, call SUBMIT() with your output.",
        )
        return action_sig.with_instructions(instructions), extract_sig

    def _make_llm_tools(self, max_workers: int = 8) -> dict[str, Any]:
        _ = max_workers
        return {}

    def _repl_only_context(self) -> Any:
        return dspy.settings.context(adapter=dspy.JSONAdapter())

    def forward(self, **input_args: Any) -> dspy.Prediction:
        interpreter = getattr(self, "_interpreter", None)
        previous = getattr(interpreter, "semantic_callbacks_enabled", True)
        try:
            if interpreter is not None:
                setattr(interpreter, "semantic_callbacks_enabled", False)
            with self._repl_only_context():
                return super().forward(**input_args)
        finally:
            if interpreter is not None:
                setattr(interpreter, "semantic_callbacks_enabled", previous)

    async def aforward(self, **input_args: Any) -> dspy.Prediction:
        interpreter = getattr(self, "_interpreter", None)
        previous = getattr(interpreter, "semantic_callbacks_enabled", True)
        try:
            if interpreter is not None:
                setattr(interpreter, "semantic_callbacks_enabled", False)
            with self._repl_only_context():
                return await super().aforward(**input_args)
        finally:
            if interpreter is not None:
                setattr(interpreter, "semantic_callbacks_enabled", previous)


def create_runtime_rlm(
    *,
    signature: type[dspy.Signature],
    interpreter: Any,
    max_iterations: int,
    max_llm_calls: int,
    max_output_chars: int | None = None,
    verbose: bool,
    tools: list[Any] | None = None,
    sub_lm: dspy.LM | None = None,
    include_llm_tools: bool = True,
) -> dspy.Module:
    """Create a canonical RLM instance for a runtime signature."""

    kwargs: dict[str, Any] = {
        "signature": signature,
        "interpreter": interpreter,
        "max_iterations": max_iterations,
        "max_llm_calls": max_llm_calls,
        "verbose": verbose,
    }
    if max_output_chars is not None:
        kwargs["max_output_chars"] = max_output_chars
    if tools is not None:
        kwargs["tools"] = tools
    if sub_lm is not None:
        kwargs["sub_lm"] = sub_lm

    rlm_cls: type[Any]
    if not include_llm_tools:
        rlm_cls = _NoCallbackRLM
    else:
        rlm_cls = _StreamingRLM

    return rlm_cls(**kwargs)


def build_recursive_subquery_rlm(
    *,
    interpreter: Any,
    max_iterations: int,
    max_llm_calls: int,
    max_output_chars: int | None = None,
    verbose: bool,
    sub_lm: dspy.LM | None = None,
) -> dspy.Module:
    """Build the canonical recursive child-query RLM."""

    from fleet_rlm.runtime.agent.signatures import RecursiveSubQuerySignature

    return create_runtime_rlm(
        signature=RecursiveSubQuerySignature,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        max_output_chars=max_output_chars,
        verbose=verbose,
        sub_lm=sub_lm,
    )


@dataclass(frozen=True)
class RuntimeModuleBuildConfig:
    """Shared constructor parameters for runtime-module RLMs."""

    interpreter: Any
    max_iterations: int
    max_llm_calls: int
    verbose: bool
    max_output_chars: int | None = None
    sub_lm: dspy.LM | None = None


def build_runtime_module_config(
    *,
    interpreter: Any,
    max_iterations: int,
    max_llm_calls: int,
    verbose: bool,
    max_output_chars: int | None = None,
    sub_lm: dspy.LM | None = None,
) -> RuntimeModuleBuildConfig:
    """Construct a ``RuntimeModuleBuildConfig`` from keyword arguments."""
    return RuntimeModuleBuildConfig(
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
        max_output_chars=max_output_chars,
        sub_lm=sub_lm,
    )


def _create_configured_runtime_rlm(
    config: RuntimeModuleBuildConfig,
    *,
    signature: type[dspy.Signature],
    tools: list[Any] | None = None,
) -> dspy.Module:
    """Create a runtime RLM from a shared build config."""
    return create_runtime_rlm(
        signature=signature,
        interpreter=config.interpreter,
        max_iterations=config.max_iterations,
        max_llm_calls=config.max_llm_calls,
        max_output_chars=config.max_output_chars,
        verbose=config.verbose,
        tools=tools,
        sub_lm=config.sub_lm,
    )


__all__ = [
    "RuntimeModuleBuildConfig",
    "VARIABLE_MODE_MAX_OUTPUT_CHARS",
    "VARIABLE_MODE_THRESHOLD",
    "_create_configured_runtime_rlm",
    "build_recursive_subquery_rlm",
    "build_runtime_module_config",
    "create_runtime_rlm",
    "interpreter_delegation_tools",
]
