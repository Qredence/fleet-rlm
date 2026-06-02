"""Factory functions and shared config for constructing DSPy runtime modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import dspy


class _NoCallbackRLM(dspy.RLM):
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

    rlm_cls = dspy.RLM if include_llm_tools else _NoCallbackRLM
    return rlm_cls(
        **kwargs,
    )


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
) -> dspy.Module:
    """Create a runtime RLM from a shared build config."""
    return create_runtime_rlm(
        signature=signature,
        interpreter=config.interpreter,
        max_iterations=config.max_iterations,
        max_llm_calls=config.max_llm_calls,
        max_output_chars=config.max_output_chars,
        verbose=config.verbose,
        sub_lm=config.sub_lm,
    )


__all__ = [
    "RuntimeModuleBuildConfig",
    "_create_configured_runtime_rlm",
    "build_recursive_subquery_rlm",
    "build_runtime_module_config",
    "create_runtime_rlm",
]
