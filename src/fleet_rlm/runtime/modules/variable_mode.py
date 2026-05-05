"""Variable-mode RLM execution module (Algorithm 1, arXiv 2512.24601v2).

This module implements the variable-mode execution path where large inputs are
stored as REPL variables and the LLM explores them through code rather than
having them in the prompt context directly.
"""

from __future__ import annotations

from typing import Any

import dspy

from fleet_rlm.runtime.agent.signatures import RLMVariableSignature
from fleet_rlm.runtime.modules.factory import create_runtime_rlm

# ---------------------------------------------------------------------------
# True-RLM variable-mode execution (Algorithm 1, arXiv 2512.24601v2)
# ---------------------------------------------------------------------------
# dspy.RLM natively:
#   1. Stores all InputField values as REPL variables (_build_variables)
#   2. Shows only metadata (type, length, preview) to the LLM
#   3. Provides llm_query() and SUBMIT() built-in
#   4. Accepts custom tools= for additional callables (sub_rlm, etc.)
# See https://dspy.ai/api/modules/RLM/
# ---------------------------------------------------------------------------

# Threshold (chars) above which rlm_query auto-routes to variable mode
VARIABLE_MODE_THRESHOLD = 32_000

# Lower max_output_chars for variable-mode forces the LLM to use
# variables (peek, grep, sub_rlm) instead of printing large output.
# dspy.RLM's REPLEntry.format() already shows "Output (N chars):" as
# metadata — this keeps it short so the LLM relies on REPL state.
VARIABLE_MODE_MAX_OUTPUT_CHARS = 5_000


class RLMVariableExecutionModule(dspy.Module):
    """Variable-mode RLM wrapper that preserves the caller's signature.

    This thin wrapper:
    1. Collects ``sub_rlm`` / ``sub_rlm_batched`` from the interpreter
       and registers them as ``dspy.Tool`` instances on the inner RLM.
    2. Reuses the requested DSPy signature so cached runtime-module callers
       keep their existing input/output field names.
    3. Relies on ``dspy.RLM``'s native variable handling to store each input
       field as a REPL variable while exposing only metadata/previews to the LM.

    All heavy lifting (REPL loop, metadata display, iteration budget,
    llm_query) is handled by ``dspy.RLM`` itself.
    """

    def __init__(
        self,
        *,
        signature: type[dspy.Signature] = RLMVariableSignature,
        interpreter: Any,
        max_iterations: int = 20,
        max_llm_calls: int = 50,
        verbose: bool = False,
        max_output_chars: int | None = None,
        sub_lm: dspy.LM | None = None,
        extra_tools: list[Any] | None = None,
    ) -> None:
        super().__init__()
        # Gather sub_rlm tools from the interpreter (if it exposes them)
        tools: list[Any] = list(extra_tools or [])
        for attr_name in ("sub_rlm", "sub_rlm_batched"):
            fn = getattr(interpreter, attr_name, None)
            if callable(fn):
                tools.append(fn)

        self._rlm = create_runtime_rlm(
            signature=signature,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            # Use a tighter output limit for variable mode to force the LLM
            # to work through REPL variables rather than printing large output.
            max_output_chars=max_output_chars or VARIABLE_MODE_MAX_OUTPUT_CHARS,
            verbose=verbose,
            tools=tools or None,
            sub_lm=sub_lm,
        )

    def forward(self, **kwargs: Any) -> dspy.Prediction:
        """Run a true-RLM loop while preserving the caller's DSPy fields.

        ``dspy.RLM`` stores each input field as a REPL variable and the
        model writes code to explore those variables before calling
        ``SUBMIT(...)`` with the signature's declared outputs.
        """
        return self._rlm(**kwargs)


def build_variable_mode_rlm(
    *,
    signature: type[dspy.Signature] = RLMVariableSignature,
    interpreter: Any,
    max_iterations: int = 20,
    max_llm_calls: int = 50,
    verbose: bool = False,
    max_output_chars: int | None = None,
    sub_lm: dspy.LM | None = None,
    extra_tools: list[Any] | None = None,
) -> RLMVariableExecutionModule:
    """Factory for the true-RLM variable-mode execution module.

    Use for any task where one or more large inputs should stay in the REPL
    instead of the model context. The LLM sees only metadata and explores
    through code + sub_rlm() recursion.
    """
    return RLMVariableExecutionModule(
        signature=signature,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
        max_output_chars=max_output_chars,
        sub_lm=sub_lm,
        extra_tools=extra_tools,
    )


__all__ = [
    "RLMVariableExecutionModule",
    "VARIABLE_MODE_MAX_OUTPUT_CHARS",
    "VARIABLE_MODE_THRESHOLD",
    "build_variable_mode_rlm",
]
