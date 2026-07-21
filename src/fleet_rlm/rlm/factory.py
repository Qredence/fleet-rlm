"""Runtime RLM factory and public access to the native constructor seam."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import dspy

from fleet_rlm.rlm.dspy_contract import RLMOptions, build_native_rlm
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.signature import FleetRLMSignature

__all__ = ["RLMFactory", "build_native_rlm"]


class RLMFactory:
    """Build fresh native RLM modules through the pinned constructor seam."""

    def create(
        self,
        *,
        models: RLMModelBundle,
        options: RLMOptions,
        interpreter: Any,
        tools: Sequence[dspy.Tool] | None = None,
        signature: type[dspy.Signature] | str | None = None,
        verbose: bool = True,
    ) -> Any:
        """Return a new ``dspy.RLM`` instance. Never reuses a previous module."""
        resolved_signature: type[dspy.Signature] | str = signature if signature is not None else FleetRLMSignature
        return build_native_rlm(
            signature=resolved_signature,
            options=options,
            tools=tools,
            sub_lm=models.sub_lm,
            interpreter=interpreter,
            verbose=verbose,
        )
