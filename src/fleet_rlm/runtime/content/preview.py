"""Head+tail preview helper following DSPy 3.2.0's REPL output convention.

Produces a compact preview that retains both the beginning and end of long
strings with a middle ellipsis that reports the omitted character count. See
``dspy.primitives.repl_types.REPLEntry.format_output`` (PR #9282) for the
upstream convention this mirrors.
"""

from __future__ import annotations


def head_tail_preview(text: str, *, max_chars: int = 500) -> tuple[str, int]:
    """Return a (preview, original_length) pair for JSON-persisted output.

    When ``text`` fits within ``max_chars``, returns it unchanged. Otherwise
    slices an equal head and tail around an omitted-character marker::

        "<head>\\n\\n... (N characters omitted) ...\\n\\n<tail>"

    The preview contains at most ``max_chars`` content characters (split
    evenly between head and tail). The omission marker (~35 chars) is added
    on top, so the total returned string is slightly longer than ``max_chars``
    — callers that need a strict byte cap should budget for the overhead.

    The returned length is always the length of the *original* text, which
    callers can persist separately (e.g., ``answer_length``) so analytics can
    reason about how much was elided.

    ``max_chars`` must be at least 2; values below 2 are clamped to 2.
    """
    raw_len = len(text)
    if raw_len <= max_chars:
        return text, raw_len
    half = max(1, max_chars // 2)
    omitted = raw_len - (2 * half)
    preview = f"{text[:half]}\n\n... ({omitted:,} characters omitted) ...\n\n{text[-half:]}"
    return preview, raw_len


__all__ = ["head_tail_preview"]
