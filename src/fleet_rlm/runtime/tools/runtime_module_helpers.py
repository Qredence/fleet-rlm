"""Shared helpers for tools that call cached runtime modules.

These helpers intentionally cover the non-recursive path where a tool reuses a
cached runtime module via ``agent.get_runtime_module(...)``. They do **not**
spawn recursive child runs; explicit recursion still flows through
``rlm_query`` and :mod:`fleet_rlm.runtime.agent.recursive_runtime`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from fleet_rlm.runtime.agent.delegation_policy import (
    RuntimeModuleExecutionRequest,
    invoke_runtime_module,
)

if TYPE_CHECKING:
    from ..agent.chat_agent import RLMReActChatAgent


def _runtime_degradation_payload(agent: RLMReActChatAgent) -> dict[str, Any]:
    """Load runtime degradation metadata without a fragile module-level import."""
    from fleet_rlm.runtime.agent.chat_turns import runtime_degradation_payload

    return runtime_degradation_payload(agent)


def coerce_int(
    value: Any,
    *,
    default: int = 0,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Parse *value* as an integer with optional bounds."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def coerce_str_list(value: Any) -> list[str]:
    """Normalize list-like prediction fields into a list of strings."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def prediction_value(prediction: Any, field_name: str, default: Any) -> Any:
    """Read a field from either a dict-shaped or attribute-shaped prediction."""
    if isinstance(prediction, dict):
        return prediction.get(field_name, default)
    return getattr(prediction, field_name, default)


def run_cached_runtime_module(
    agent: RLMReActChatAgent,
    module_name: str,
    **kwargs: Any,
) -> tuple[Any | None, dict[str, Any] | None, bool]:
    """Invoke one cached runtime module through the shared delegation policy."""
    result = invoke_runtime_module(
        RuntimeModuleExecutionRequest(
            agent=agent,
            module_name=module_name,
            module_kwargs=kwargs,
        )
    )
    return result.prediction, result.error, result.fallback_used


def runtime_metadata(
    agent: RLMReActChatAgent,
    prediction: Any,
    *,
    fallback_used: bool,
) -> dict[str, Any]:
    """Return stable metadata shared by cached runtime-module tool results."""
    metadata: dict[str, Any] = {
        "depth": coerce_int(
            prediction_value(prediction, "depth", agent._current_depth + 1),
            default=agent._current_depth + 1,
            minimum=0,
        ),
        "sub_agent_history": coerce_int(
            prediction_value(prediction, "sub_agent_history", 0),
            default=0,
            minimum=0,
        ),
        "delegate_lm_fallback": bool(fallback_used),
        "runtime_degraded": False,
        "runtime_fallback_used": False,
    }
    metadata.update(_runtime_degradation_payload(agent))
    return metadata
