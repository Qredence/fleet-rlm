"""Orthogonal LM-completion shaping helpers for RLM action generation.

These helpers are NOT adapter-parsing logic — they shape the raw completion
before it ever reaches a DSPy adapter, and harden error extraction. DSPy's
native ``ChatAdapter`` → ``JSONAdapter`` fallback
(``dspy/adapters/chat_adapter.py:46,68,87-94``) handles adapter parse recovery;
this module only owns the prompt/cost-shaping guards that are orthogonal to
that fallback.

DSPy public APIs consumed (3.3.0b1):
  - ``dspy.AdapterParseError.lm_response``  (``dspy/utils/exceptions.py:224-261``)
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """Read an integer from environment variable, returning default if not set or invalid."""
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


# Bound for ``truncate_completion`` (prompt/cost shaping only — not adapter
# parsing logic). DSPy's native ``ChatAdapter`` → ``JSONAdapter`` fallback
# (``dspy/adapters/chat_adapter.py:46,68,87-94``) handles parse recovery; this
# constant only bounds the completion length we inspect for the echo-back
# anomaly (the model replaying ``variables_info`` as its "response").
_RESPONSE_TRUNCATION_CHARS = _env_int("FLEET_RLM_RESPONSE_TRUNCATION_CHARS", 8000)


def extract_completion_from_parse_error(exc: Exception) -> str | None:
    """Pull the raw LM completion text out of an ``AdapterParseError``.

    Public API consumed (DSPy 3.3.0b1): ``dspy.AdapterParseError.lm_response``
    (``dspy/utils/exceptions.py:224-261``). Prefer that attribute first; the
    message-scrape fallback below relies on the UNDOCUMENTED ``"LM Response: "``
    substring format and exists only as a defensive shim for older / wrapped
    exception types.

    Using the exception payload (rather than ``lm.history``) is robust to the
    bounded action-LM having already gone out of scope when the parse error is
    caught. This is orthogonal to adapter parsing — it is used by the echo-back
    / degenerate-output guard.
    """
    raw = getattr(exc, "lm_response", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return _scrape_message(str(exc))


def _scrape_message(msg: str) -> str | None:
    """Defensive fallback: scrape the completion out of the exception message.

    Relies on the UNDOCUMENTED DSPy message format (``"LM Response: <text>"``);
    prefer the public ``AdapterParseError.lm_response`` attribute via
    ``extract_completion_from_parse_error`` above.
    """
    marker = "LM Response: "
    idx = msg.find(marker)
    if idx < 0:
        return None
    tail = msg[idx + len(marker) :]
    end = tail.find("\n\nExpected to find output fields")
    completion = tail if end < 0 else tail[:end]
    completion = completion.strip()
    return completion or None


def is_degenerate_response(completion: Any) -> bool:
    """Prompt-shaping guard: detect unusable outputs (e.g. ``{len(doc)}``).

    This is NOT adapter-parsing logic — DSPy's native ``ChatAdapter`` →
    ``JSONAdapter`` fallback (``dspy/adapters/chat_adapter.py:46,68,87-94``)
    handles parsing. This guard classifies completions for the echo-back /
    degenerate-output budget guard: outputs that carry ``[[ ## field ## ]]``
    delimiters or are valid JSON objects are non-degenerate; short fragments,
    bare f-string snippets, and prose are degenerate.

    Also detects the echo-back anomaly: if the completion is longer than the
    truncation limit AND contains the ``variables_info`` sentinel pattern
    (``«««`` or ``Variable:``), it is classified as degenerate — the model is
    replaying its input rather than generating an action.
    """
    if not isinstance(completion, str):
        return True
    stripped = completion.strip()
    if not stripped:
        return True
    if "[[ ##" in stripped:
        return False
    try:
        import json

        if isinstance(json.loads(stripped), dict):
            return False
    except (ValueError, TypeError):
        # Not valid JSON (or wrong input type): continue heuristic checks.
        pass
    # Detect echo-back: the model is replaying variables_info instead of
    # generating an action. This was the exact cause of the 13s anomaly
    # at iteration 19 in the observed trace.
    if len(stripped) >= _RESPONSE_TRUNCATION_CHARS:
        echo_markers = ("Variable: `", "«««", "Total length:", "Description:")
        marker_hits = sum(1 for m in echo_markers if m in stripped)
        if marker_hits >= 3:
            logger.debug(
                "RLM action gen: detected echo-back anomaly (completion length=%s, markers=%s) — marking degenerate",
                len(stripped),
                marker_hits,
            )
            return True
    return True


def truncate_completion(completion: str) -> str:
    """Truncate the raw completion for echo-back detection (prompt/cost shaping).

    The observed trace showed a 13-second ChatAdapter parse when the model
    echoed the entire ``variables_info`` (5K+ chars) back as its "response".
    Truncating to ``_RESPONSE_TRUNCATION_CHARS`` bounds the inspection cost and
    normalizes the content so the echo-back detection in ``is_degenerate_response``
    can fire on the *truncated* text.

    This is NOT adapter-parsing logic — DSPy's native ``ChatAdapter`` →
    ``JSONAdapter`` fallback handles parsing.
    """
    if not isinstance(completion, str):
        return ""
    limit = _RESPONSE_TRUNCATION_CHARS
    if len(completion) <= limit:
        return completion
    return completion[:limit]
