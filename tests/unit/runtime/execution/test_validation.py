from __future__ import annotations

from fleet_rlm.runtime.execution.validation import (
    ValidationConfig,
    validate_assistant_response,
)


def test_validate_assistant_response_warns_on_tool_errors_by_default() -> None:
    response, warnings = validate_assistant_response(
        assistant_response="Here is the result.",
        trajectory={"error_0": "SyntaxError: invalid syntax"},
        config=ValidationConfig(),
    )

    assert response == "Here is the result."
    assert any("tool error" in warning for warning in warnings)


def test_validate_assistant_response_off_mode_suppresses_warnings() -> None:
    response, warnings = validate_assistant_response(
        assistant_response="Here is the result.",
        trajectory={"error_0": "SyntaxError: invalid syntax"},
        config=ValidationConfig(guardrail_mode="off"),
    )

    assert response == "Here is the result."
    assert warnings == []
