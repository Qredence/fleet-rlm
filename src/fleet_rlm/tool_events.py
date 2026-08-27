"""Provider-neutral tool event projection primitives.

This module deliberately has no dependency on the RLM runtime.  Workspace,
attachment, and other host domains can expose bounded tool projections without
creating a dependency cycle into ``fleet_rlm.rlm``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fleet_rlm.json_types import JsonValue

ToolInputProjection = Callable[[Mapping[str, Any]], JsonValue]
ToolOutputProjection = Callable[[Any], JsonValue]
ToolAfterResult = Callable[[Any], None]


def _empty_input(*_arguments: Any) -> JsonValue:
    """Return an empty JSON object for any argument mapping."""
    return {}


def _empty_output(*_result: Any) -> JsonValue:
    """Return an empty JSON object for an unprojected result."""
    return {}


def bound_event_text(value: object, *, max_chars: int = 256) -> str:
    """Bound one allowlisted structural text value without rewriting it."""
    limit = max(4, int(max_chars))
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass(frozen=True, slots=True)
class ToolEventView:
    """Host-owned, fail-closed public projection for one Tool."""

    input_projection: ToolInputProjection = _empty_input
    output_projection: ToolOutputProjection = _empty_output
    allow_repeated_identical: bool = False

    @classmethod
    def metadata_only(cls) -> ToolEventView:
        return cls()

    def input(self, arguments: Mapping[str, Any]) -> JsonValue:
        try:
            return self.input_projection(arguments)
        except Exception:
            return {}

    def output(self, result: Any) -> JsonValue:
        try:
            return self.output_projection(result)
        except Exception:
            return {}

    def error(self, *, validation: bool, exception: BaseException | None = None) -> str:
        public_message = getattr(exception, "public_message", None)
        if isinstance(public_message, str) and public_message:
            return public_message
        return "Tool arguments are invalid" if validation else "Tool failed"


__all__ = [
    "ToolAfterResult",
    "ToolEventView",
    "ToolInputProjection",
    "ToolOutputProjection",
    "bound_event_text",
]
