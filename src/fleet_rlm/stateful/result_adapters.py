"""Shared adapters for interpreter results and stateful response shaping."""

from __future__ import annotations

from typing import Any

from dspy.primitives.code_interpreter import FinalOutput


def final_output_value(result: Any) -> Any | None:
    """Return wrapped payload when result is FinalOutput, else None."""
    if isinstance(result, FinalOutput):
        return result.output
    return None


def final_output_dict(result: Any) -> dict[str, Any] | None:
    """Return output dict when interpreter result is a supported mapping payload."""
    output = final_output_value(result)
    if output is not None:
        if isinstance(output, dict):
            return output
        return None
    if isinstance(result, dict):
        return result
    return None


def extract_execute_error(result: Any) -> str | None:
    """Return stderr-like error string from interpreter string output."""
    if isinstance(result, str):
        stripped = result.strip()
        if "[Error]" in stripped:
            return stripped
    return None


def operation_error(filename: str, error: Any) -> dict[str, Any]:
    """Standardized error dictionary for workspace operations."""
    return {
        "status": "error",
        "filename": filename,
        "error": str(error),
    }


def operation_unexpected(filename: str) -> dict[str, Any]:
    """Standardized unexpected-result dictionary for workspace operations."""
    return operation_error(filename, "Unexpected result format")
