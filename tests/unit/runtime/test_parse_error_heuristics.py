"""Unit tests for narrowed JSON parse-error heuristics.

VAL-CORR-002: ``_is_parse_error`` in ``runtime/modules/factory.py`` must not
match broad substrings like ``"invalid"``, ``"expected"``, or ``"decode"`` in
isolation. ``"Invalid API key"`` must NOT be classified as a parse error;
``"json.decode error"``, ``"json parse error"``, ``"malformed json"``, and
``json.JSONDecodeError`` instances MUST be classified as parse errors.

VAL-CORR-003: ``_is_rlm_parse_error`` in ``runtime/modules/escalating.py`` is
narrowed identically. Additionally, the word ``"extraction"`` alone must NOT
trigger a match (it was in the old broad marker list).
"""

from __future__ import annotations

import json

from fleet_rlm.runtime.modules.escalating import _is_rlm_parse_error
from fleet_rlm.runtime.modules.factory import _StreamingRLM


class TestIsParseError:
    """VAL-CORR-002: ``_StreamingRLM._is_parse_error`` narrowed markers."""

    def test_invalid_api_key_is_not_parse_error(self) -> None:
        """'Invalid API key' is an auth error, not a parse error."""
        assert _StreamingRLM._is_parse_error(ValueError("Invalid API key")) is False

    def test_json_decode_error_matches(self) -> None:
        """'json.decode error' must be classified as a parse error."""
        assert _StreamingRLM._is_parse_error(ValueError("json.decode error: expecting value")) is True

    def test_json_parse_error_matches(self) -> None:
        """'json parse error' must be classified as a parse error."""
        assert _StreamingRLM._is_parse_error(ValueError("json parse error")) is True

    def test_malformed_json_matches(self) -> None:
        """'malformed json' must be classified as a parse error."""
        assert _StreamingRLM._is_parse_error(ValueError("malformed json")) is True

    def test_json_decode_error_exception_type_matches(self) -> None:
        """``json.JSONDecodeError`` instances must match via type check."""
        exc = json.JSONDecodeError("Expecting value", "", 0)
        assert _StreamingRLM._is_parse_error(exc) is True

    def test_generic_value_error_does_not_match(self) -> None:
        """A generic non-JSON ValueError must not be classified as a parse error."""
        assert _StreamingRLM._is_parse_error(ValueError("some other error")) is False

    def test_expected_marker_alone_does_not_match(self) -> None:
        """The word 'expected' alone must NOT trigger a match."""
        assert _StreamingRLM._is_parse_error(ValueError("expected output")) is False

    def test_runtime_error_invalid_state_does_not_match(self) -> None:
        """'invalid state' must NOT be classified as a parse error."""
        assert _StreamingRLM._is_parse_error(RuntimeError("invalid state")) is False

    def test_json_substring_matches(self) -> None:
        """Any message containing 'json' (case-insensitive) matches."""
        assert _StreamingRLM._is_parse_error(Exception("Could not serialize to JSON object")) is True

    def test_parse_error_phrase_matches(self) -> None:
        """The phrase 'parse error' must match."""
        assert _StreamingRLM._is_parse_error(Exception("parse error: unexpected token")) is True


class TestIsRlmParseError:
    """VAL-CORR-003: ``_is_rlm_parse_error`` narrowed markers."""

    def test_invalid_api_key_is_not_parse_error(self) -> None:
        """'Invalid API key' is an auth error, not a parse error."""
        assert _is_rlm_parse_error(ValueError("Invalid API key")) is False

    def test_json_decode_error_matches(self) -> None:
        """'json.decode error' must be classified as a parse error."""
        assert _is_rlm_parse_error(ValueError("json.decode error: expecting value")) is True

    def test_json_parse_error_matches(self) -> None:
        """'json parse error' must be classified as a parse error."""
        assert _is_rlm_parse_error(ValueError("json parse error")) is True

    def test_malformed_json_matches(self) -> None:
        """'malformed json' must be classified as a parse error."""
        assert _is_rlm_parse_error(ValueError("malformed json")) is True

    def test_json_decode_error_exception_type_matches(self) -> None:
        """``json.JSONDecodeError`` instances must match via type check."""
        exc = json.JSONDecodeError("Expecting value", "", 0)
        assert _is_rlm_parse_error(exc) is True

    def test_extraction_marker_alone_does_not_match(self) -> None:
        """The word 'extraction' alone must NOT trigger a match."""
        assert _is_rlm_parse_error(ValueError("extraction failed")) is False

    def test_generic_value_error_does_not_match(self) -> None:
        """A generic non-JSON ValueError must not be classified as a parse error."""
        assert _is_rlm_parse_error(ValueError("some other error")) is False

    def test_expected_marker_alone_does_not_match(self) -> None:
        """The word 'expected' alone must NOT trigger a match."""
        assert _is_rlm_parse_error(ValueError("expected output")) is False

    def test_runtime_error_invalid_state_does_not_match(self) -> None:
        """'invalid state' must NOT be classified as a parse error."""
        assert _is_rlm_parse_error(RuntimeError("invalid state")) is False

    def test_json_substring_matches(self) -> None:
        """Any message containing 'json' (case-insensitive) matches."""
        assert _is_rlm_parse_error(Exception("Could not serialize to JSON object")) is True

    def test_parse_error_phrase_matches(self) -> None:
        """The phrase 'parse error' must match."""
        assert _is_rlm_parse_error(Exception("parse error: unexpected token")) is True

    def test_broker_unavailable_does_not_match(self) -> None:
        """Regression: non-parse errors (e.g. broker failures) must not retry as parse errors."""
        assert _is_rlm_parse_error(Exception("broker_unavailable: sandbox did not respond")) is False
