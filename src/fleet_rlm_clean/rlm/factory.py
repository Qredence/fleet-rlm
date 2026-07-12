"""Only construction site for dspy.RLM in the clean-backend package."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import dspy

from fleet_rlm_clean.rlm.budgets import RLMBudget
from fleet_rlm_clean.rlm.errors import RLMBudgetError
from fleet_rlm_clean.rlm.model_bundle import RLMModelBundle
from fleet_rlm_clean.rlm.signature import FleetRLMSignature


class RLMFactory:
    """Deep module: one entry point that builds a fresh budgeted ``dspy.RLM``.

    Callers supply roles, budgets, interpreter, and tools. Construction details
    (installed constructor kwargs, signature default) stay behind this seam.
    Root LM application during execution belongs to RLMRunner, not this factory.
    """

    def create(
        self,
        *,
        models: RLMModelBundle,
        budget: RLMBudget,
        interpreter: Any,
        tools: Sequence[Callable[..., Any]] | None = None,
        signature: type[dspy.Signature] | str | None = None,
        verbose: bool = False,
    ) -> Any:
        """Return a new ``dspy.RLM`` instance. Never reuses a previous module."""
        try:
            budget.validate()
        except RLMBudgetError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize unexpected budget failures
            raise RLMBudgetError(str(exc)) from exc

        resolved_signature: type[dspy.Signature] | str = signature if signature is not None else FleetRLMSignature
        tool_list = list(tools) if tools is not None else None

        return dspy.RLM(
            resolved_signature,
            max_iterations=budget.max_iterations,
            max_llm_calls=budget.max_llm_calls,
            max_output_chars=budget.max_output_chars,
            verbose=verbose,
            tools=tool_list,
            sub_lm=models.sub_lm,
            interpreter=interpreter,
        )
