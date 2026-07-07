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
    reasoning_content = extract_reasoning_content_from_lm_response(raw)
    if reasoning_content:
        return reasoning_content
    visible_text = _extract_visible_text_from_lm_response(raw)
    if visible_text:
        return visible_text
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    scraped = _scrape_message(str(exc))
    reasoning_content = extract_reasoning_content_from_lm_response(scraped)
    if reasoning_content:
        return reasoning_content
    visible_text = _extract_visible_text_from_lm_response(scraped)
    if visible_text:
        return visible_text
    return scraped


def extract_reasoning_content_from_parse_error(exc: Exception | BaseException) -> str | None:
    """Extract provider-side reasoning from an adapter parse error payload.

    Some OpenAI-compatible reasoning providers return an object-shaped payload
    like ``{"text": "", "reasoning_content": "..."}``. DSPy correctly raises
    an adapter parse error because no user-facing output field is present, but
    the reasoning payload can still contain a recoverable RLM action. Keep this
    extraction local and explicit so the app can recover actions or synthesize a
    safe degraded response without exposing the raw wrapper to users.
    """
    raw = getattr(exc, "lm_response", None)
    reasoning_content = extract_reasoning_content_from_lm_response(raw)
    if reasoning_content:
        return reasoning_content
    scraped = _scrape_message(str(exc))
    return extract_reasoning_content_from_lm_response(scraped)


def extract_reasoning_content_from_lm_response(value: Any) -> str | None:
    """Return ``reasoning_content`` from provider dict/repr payloads.

    The helper intentionally accepts both an actual dict and the string repr
    that DSPy embeds into ``AdapterParseError.lm_response``.
    """
    payload = value
    if isinstance(value, str):
        if "reasoning_content" not in value:
            return None
        try:
            import ast

            payload = ast.literal_eval(value.strip())
        except (SyntaxError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None

    reasoning = payload.get("reasoning_content")
    if not isinstance(reasoning, str) or not reasoning.strip():
        return None
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        return None
    return reasoning.strip()


def _extract_visible_text_from_lm_response(value: Any) -> str | None:
    payload = value
    if isinstance(value, str):
        if "'text'" not in value and '"text"' not in value:
            return None
        try:
            import ast

            payload = ast.literal_eval(value.strip())
        except (SyntaxError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    text = payload.get("text")
    return text.strip() if isinstance(text, str) and text.strip() else None


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


def format_parse_error_output(exc: Exception) -> str:
    """Extract, analyze, and safely format a parse error to prevent REPL history pollution."""
    raw_completion = extract_completion_from_parse_error(exc)
    if raw_completion:
        truncated = truncate_completion(raw_completion)
        if is_degenerate_response(truncated):
            return f"[ParseError] Degenerate response or echo-back detected: {truncated[:200]}..."
        return f"[ParseError] Malformed structured output: {truncated[:200]}..."
    return f"[ParseError] {str(exc)[:500]}"


_PYTHON_FENCE_LANGS = {"python", "py", ""}


def safe_strip_code_fences(code: str) -> str:
    """Strip code fences using last-standalone-line matching.

    The upstream _strip_code_fences uses ``remainder.find("```")`` which matches
    the *first* triple-backtick in the remainder. When the LLM generates code
    containing triple backticks inside string literals (e.g. regex patterns like
    ``r'--- FILE: (.+?) ---\\s*\n```(?:\\w+)?\n(.*?)\n```'``), the function
    truncates at the interior backtick, causing ``unterminated string literal``
    errors in the REPL. This replacement walks lines and uses the *last*
    standalone ```` line as the closing fence.
    """
    code = code.strip()
    if "```" not in code:
        return code

    lines = code.splitlines()
    # Strip outer decorative fence pairs
    while len(lines) >= 2 and lines[0].strip() == "```" and lines[-1].strip() == "```":
        lines.pop(0)
        lines.pop()
    code = "\n".join(lines).strip()
    if "```" not in code:
        return code

    # Only treat as a fenced block when the code itself starts with an opening
    # fence. Otherwise the code is plain Python that happens to contain ``` inside
    # string literals (e.g. regex patterns with embedded triple backticks); the
    # upstream ``find("```")`` would match that interior backtick and truncate
    # valid input or raise a spurious SyntaxError.
    if not code.startswith("```"):
        return code

    # Opening fence is at the start of the code; split off the lang/info line.
    lang_line, sep, remainder = code[3:].partition("\n")
    if not sep:
        return code

    lang = (lang_line.strip().split(maxsplit=1)[0] if lang_line.strip() else "").lower()
    if lang not in _PYTHON_FENCE_LANGS:
        raise SyntaxError(f"Expected Python code but got ```{lang} fence. Write Python code, not {lang}.")

    # Find closing fence: walk backwards for the last line that is exactly "```"
    rlines = remainder.splitlines()
    for i in range(len(rlines) - 1, -1, -1):
        if rlines[i].strip() == "```":
            return "\n".join(rlines[:i]).strip()
    return remainder.strip()
