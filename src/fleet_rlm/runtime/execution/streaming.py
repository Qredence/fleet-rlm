"""Sync/async streaming helpers for the RLM agent.

Event construction, status parsing, citation handling, and payload building
live in :mod:`fleet_rlm.runtime.execution.streaming_events`.

The :class:`StreamingContext` dataclass lives in
:mod:`fleet_rlm.runtime.execution.streaming_context`.

Both are re-exported here for backwards compatibility.
"""

from __future__ import annotations

import logging

from fleet_rlm.runtime.execution.streaming_context import (
    StreamingContext as StreamingContext,
)
from fleet_rlm.runtime.execution.streaming_events import (
    ReActStatusProvider as ReActStatusProvider,
    _normalize_trajectory as _normalize_trajectory,
    classify_tool_event_kind as classify_tool_event_kind,
    parse_tool_call_payload as parse_tool_call_payload,
    parse_tool_call_status as parse_tool_call_status,
    parse_tool_result_payload as parse_tool_result_payload,
    parse_tool_result_status as parse_tool_result_status,
)
from fleet_rlm.runtime.models.streaming import StreamEvent as StreamEvent

logger = logging.getLogger(__name__)
TERMINAL_STREAM_EVENT_KINDS: frozenset[str] = frozenset({"done", "error"})


def is_terminal_stream_event_kind(kind: str) -> bool:
    """Return whether *kind* is terminal for both runtime and websocket flows."""
    return kind in TERMINAL_STREAM_EVENT_KINDS
