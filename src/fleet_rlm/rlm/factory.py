"""Only construction site for dspy.RLM in the Fleet RLM package."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import dspy

from fleet_rlm.rlm.budgets import RunBudget
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.observable import DetailObserver, ObservableRLM
from fleet_rlm.rlm.signature import FleetRLMSignature
from fleet_rlm.skills.capabilities import RLMTool


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
        budget: RunBudget,
        interpreter: Any,
        tools: Sequence[RLMTool] | None = None,
        signature: type[dspy.Signature] | str | None = None,
        verbose: bool = False,
        observer: DetailObserver | None = None,
    ) -> Any:
        """Return a new ``dspy.RLM`` instance. Never reuses a previous module."""
        resolved_signature: type[dspy.Signature] | str = signature if signature is not None else FleetRLMSignature
        tool_list = list(tools) if tools is not None else None

        return ObservableRLM(
            resolved_signature,
            max_iterations=budget.max_iterations,
            max_llm_calls=budget.max_llm_calls,
            max_output_chars=budget.max_output_chars,
            verbose=verbose,
            tools=tool_list,
            sub_lm=models.sub_lm,
            interpreter=interpreter,
            observer=observer,
            detail_max_chars=budget.max_output_chars,
            max_tool_calls=budget.max_tool_calls,
            max_sub_lm_concurrency=budget.max_sub_lm_concurrency,
        )
