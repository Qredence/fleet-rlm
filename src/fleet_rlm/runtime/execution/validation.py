"""Validation utilities for ReAct agent response guardrails.

This module provides configurable guardrails for assistant responses,
including length checks, substantive content validation, and tool error
detection in trajectories.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from fleet_rlm.runtime.agent.trajectory_errors import trajectory_has_tool_errors


@dataclass
class ValidationConfig:
    """Configuration for assistant response validation.

    Attributes:
        guardrail_mode: How strictly to enforce guardrails.
            - "off": No enforcement, validation returns no warnings
            - "warn": Log warnings but don't block
            - "strict": Raise ValueError on hard violations
        max_output_chars: Maximum allowed response length
        min_substantive_chars: Minimum chars for substantive response
    """

    guardrail_mode: Literal["off", "warn", "strict"] = "off"
    max_output_chars: int = 10000
    min_substantive_chars: int = 20


def validate_assistant_response(
    *,
    assistant_response: str,
    trajectory: dict[str, Any] | None,
    config: ValidationConfig,
) -> tuple[str, list[str]]:
    """Apply configurable response guardrails.

    Validates the assistant response against configured constraints:
    - Non-empty response (hard check)
    - Response length under max_output_chars (hard check)
    - Minimum substantive content (soft warning)
    - Tool errors in trajectory (soft warning)

    Args:
        assistant_response: The response text to validate
        trajectory: The trajectory dict from a ReAct prediction
        config: Validation configuration settings

    Returns:
        Tuple of (sanitized_response, warning_messages)

    Raises:
        ValueError: In strict mode when hard guardrail violations occur
    """
    response = str(assistant_response or "").strip()
    mode = config.guardrail_mode

    hard_issues: list[str] = []
    warnings: list[str] = []

    if not response:
        hard_issues.append("empty assistant response")

    if len(response) > config.max_output_chars:
        hard_issues.append(
            f"assistant response length {len(response)} exceeds "
            f"max_output_chars={config.max_output_chars}"
        )

    if response and len(response) < config.min_substantive_chars:
        warnings.append(
            "assistant response appears brief; consider adding more substantive detail"
        )

    if trajectory_has_tool_errors(trajectory):
        warnings.append(
            "trajectory indicates at least one tool error; "
            "consider retrying or recovering before final response"
        )

    if hard_issues and mode == "strict":
        raise ValueError("guardrail violation: " + "; ".join(hard_issues))

    if hard_issues and mode == "warn":
        warnings.extend([f"guardrail warning: {issue}" for issue in hard_issues])

    return response, warnings if mode in {"warn", "strict"} else []
