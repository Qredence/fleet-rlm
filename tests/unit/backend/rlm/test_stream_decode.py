"""Direct unit tests for the DSPy JSONAdapter stream decoder.

DSPy's JSONAdapter streams raw JSON-encoded field values (surrounding quotes,
``\n``-style escapes) that can bleed into the next field and straddle fragment
boundaries. ``_extract_json_string_prefix`` decodes the first JSON string so the
TUI receives clean reasoning/code text; these tests lock the boundary cases.
"""

from __future__ import annotations

import dspy

from fleet_rlm.rlm.events import RLMCode, RLMReasoning
from fleet_rlm.rlm.runner import _extract_json_string_prefix, _NativeRLMStreamProjector


def test_decodes_complete_json_string_with_escapes() -> None:
    assert _extract_json_string_prefix('"Let me think\\nabout it","co') == "Let me think\nabout it"


def test_decodes_escaped_quotes() -> None:
    assert _extract_json_string_prefix('"quote: \\"x\\" end"') == 'quote: "x" end'


def test_stops_at_first_unescaped_quote_ignoring_field_bleed() -> None:
    assert _extract_json_string_prefix('"a","code":"x"') == "a"


def test_trailing_backslash_straddles_fragment_boundary() -> None:
    assert _extract_json_string_prefix('"abc\\') == "abc"
    assert _extract_json_string_prefix('"abc\\n"') == "abc\n"


def test_incomplete_unicode_escape_straddles_fragment_boundary() -> None:
    assert _extract_json_string_prefix('"abc\\u12') == "abc"
    assert _extract_json_string_prefix('"abc\\u1234"') == "abcሴ"


def test_invalid_unicode_escape_kept_as_literal_text() -> None:
    assert _extract_json_string_prefix('"a\\uZZZZb"') == "a\\uZZZZb"


def test_surrogate_pair_decodes_to_single_character() -> None:
    assert _extract_json_string_prefix('"a\\ud83d\\ude00b"') == "a\U0001f600b"


def test_high_surrogate_straddling_fragment_boundary_waits_for_low_half() -> None:
    assert _extract_json_string_prefix('"a\\ud83d') == "a"
    assert _extract_json_string_prefix('"a\\ud83d\\ude0') == "a"


def test_unpaired_surrogates_stay_literal_text() -> None:
    """Lone surrogates cannot be UTF-8 encoded; keep the literal escape text
    so the SSE path never raises UnicodeEncodeError."""
    assert _extract_json_string_prefix('"a\\ud83d follows"') == "a\\ud83d follows"
    assert _extract_json_string_prefix('"a\\udc00 follows"') == "a\\udc00 follows"
    assert _extract_json_string_prefix('"a\\ud83d\\u0041b"') == "a\\ud83dAb"


def test_backspace_and_formfeed_escapes_stay_literal_letters() -> None:
    """JSON \\b/\\f decode to control characters; the projector deliberately
    emits the literal letters instead so terminal output stays clean."""
    assert _extract_json_string_prefix('"a\\b\\f"') == "abf"


def test_no_leading_quote_returns_empty_prefix() -> None:
    assert _extract_json_string_prefix("plain text") == ""


def test_projector_accumulated_raw_stops_growing_past_output_bound() -> None:
    """Once the public output bound is exhausted the raw JSON buffer must stop
    growing so a pathological long stream stays memory-bounded."""

    events = []
    projector = _NativeRLMStreamProjector(run_id="run", max_chars=4, publish=events.append)
    for item in (
        dspy.streaming.StreamResponse("generate_action", "reasoning", '"abcd', False),
        dspy.streaming.StreamResponse("generate_action", "reasoning", "efgh", False),
        dspy.streaming.StreamResponse("generate_action", "reasoning", 'ij"', True),
    ):
        projector.publish(item)

    assert projector._field_raw["reasoning"] == '"abcd'  # no growth past the bound
    streamed = [event.text for event in events if isinstance(event, RLMReasoning)]
    assert streamed == ["abcd", ""]  # bounded deltas, then the empty final flush
    assert "".join(streamed) == "abcd"


def test_projector_decodes_incremental_json_across_fragments() -> None:
    events = []
    projector = _NativeRLMStreamProjector(run_id="run", max_chars=10_000, publish=events.append)
    for item in (
        dspy.streaming.StreamResponse("generate_action", "code", '"print(', False),
        dspy.streaming.StreamResponse("generate_action", "code", '\\"hello\\")"', True),
    ):
        projector.publish(item)

    code = [event.code for event in events if isinstance(event, RLMCode)]
    assert code == ["print(", '"hello")']
