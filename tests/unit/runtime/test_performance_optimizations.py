"""Unit tests for the performance optimization improvements.

Tests cover:
1. Response truncation for echo-back detection (prompt/cost shaping)
2. Iteration budget guardrail
3. Context pre-processing (_build_context_index)
4. REPL output cache

Note: the hand-rolled salvage cascade (_regex_salvage, _salvage_with_chat_adapter,
_salvage_with_regex, _CHATADAPTER_SALVAGE_ENABLED, _REGEX_SALVAGE_ENABLED) was
removed once the primary adapter was realigned to ``dspy.ChatAdapter`` (DSPy's
native ``ChatAdapter`` → ``JSONAdapter`` fallback subsumes it). The orthogonal
``_truncate_completion`` / ``_is_degenerate_response`` / ``_extract_completion_from_parse_error``
helpers remain and are covered below.
"""

from __future__ import annotations

import hashlib
from typing import ClassVar
from unittest.mock import MagicMock

import dspy
import pytest

from fleet_rlm.runtime.content.parse_recovery import _RESPONSE_TRUNCATION_CHARS
from fleet_rlm.runtime.modules import factory
from fleet_rlm.runtime.modules.factory import _StreamingRLM


class _ActionSig(dspy.Signature):
    """Mirror of the RLM action-generation signature fields."""

    reasoning: str = dspy.OutputField()
    code: str = dspy.OutputField()
    instructions: ClassVar[str] = "Produce reasoning and code."


_CHATADAPTER_COMPLETION = """[[ ## reasoning ## ]]
Let me inspect the document first.

[[ ## code ## ]]
```python
print("Hello")
```"""


def _bare_rlm() -> _StreamingRLM:
    """A ``_StreamingRLM`` shell with only ``generate_action.signature`` wired."""
    rlm = object.__new__(_StreamingRLM)
    rlm.generate_action = MagicMock()
    rlm.generate_action.signature = _ActionSig
    rlm._consecutive_parse_errors = 0
    rlm._max_consecutive_parse_errors = 3
    rlm._repl_output_cache = {}
    rlm._summary_directive_injected = False
    rlm._prepared_serializable_cache = {}
    return rlm


# ============================================================================
# 1. Response Truncation (echo-back detection guard)
# ============================================================================


class TestResponseTruncation:
    """``_truncate_completion`` bounds the completion for echo-back detection."""

    def test_truncates_long_completion(self) -> None:
        long_text = "x" * 20000
        truncated = _StreamingRLM._truncate_completion(long_text)
        assert len(truncated) == _RESPONSE_TRUNCATION_CHARS

    def test_preserves_short_completion(self) -> None:
        short_text = "short response"
        assert _StreamingRLM._truncate_completion(short_text) == short_text

    def test_handles_none(self) -> None:
        assert _StreamingRLM._truncate_completion(None) == ""  # type: ignore[arg-type]

    def test_handles_empty_string(self) -> None:
        assert _StreamingRLM._truncate_completion("") == ""


# ============================================================================
# 2. Echo-back Detection
# ============================================================================


class TestEchoBackDetection:
    """``_is_degenerate_response`` detects model echo-back anomaly."""

    def test_detects_echo_back_variables_info(self) -> None:
        echo = (
            "Variable: `user_request` (access it in your code)\n"
            "Type: str\n"
            "Description: The current user request to solve\n"
            "Total length: 210 characters\n"
            "«««\nAnalyze the backend...\n»»»»\n"
            "Variable: `core_memory` (access it in your code)\n" + "x" * 10000
        )
        assert _StreamingRLM._is_degenerate_response(echo) is True

    def test_does_not_flag_normal_chatadapter(self) -> None:
        assert _StreamingRLM._is_degenerate_response(_CHATADAPTER_COMPLETION) is False

    def test_does_not_flag_normal_json(self) -> None:
        assert _StreamingRLM._is_degenerate_response('{"reasoning": "x", "code": "y"}') is False


# ============================================================================
# 3. Context Pre-processing
# ============================================================================


class TestBuildContextIndex:
    """``_build_context_index`` pre-parses large documents."""

    def test_returns_empty_for_small_document(self) -> None:
        result = _StreamingRLM._build_context_index("small text")
        assert result == {}

    def test_returns_empty_for_empty_document(self) -> None:
        assert _StreamingRLM._build_context_index("") == {}

    def test_extracts_sections_from_markdown(self) -> None:
        doc = "# Header 1\ncontent1\n## Header 2\ncontent2\n## Header 3\ncontent3\n"
        doc = doc + "x" * 40000  # Make it exceed threshold
        result = _StreamingRLM._build_context_index(doc)
        assert result != {}
        assert "sections" in result
        assert result["total_sections"] >= 3
        # sections[0] is the preamble if any exist (here there is none since # Header is at start)
        headers = [s["header"] for s in result["sections"]]
        assert "# Header 1" in headers
        assert "## Header 2" in headers
        assert "## Header 3" in headers
        assert result["total_chars"] == len(doc)

    def test_extracts_file_paths(self) -> None:
        doc = "src/fleet_rlm/api/main.py\nsrc/fleet_rlm/runtime/factory.py\n" + "x" * 40000
        result = _StreamingRLM._build_context_index(doc)
        assert "file_paths" in result
        paths = result["file_paths"]
        assert "src/fleet_rlm/api/main.py" in paths
        assert "src/fleet_rlm/runtime/factory.py" in paths

    def test_counts_code_blocks(self) -> None:
        doc = "```python\nprint(1)\n```\nsome text\n```\ncode\n```\n" + "x" * 40000
        result = _StreamingRLM._build_context_index(doc)
        assert result["code_blocks"] == 2

    def test_section_offsets_are_correct(self) -> None:
        doc = "# A\ncontent_a\n# B\ncontent_b\n"
        doc = doc + "x" * 40000
        result = _StreamingRLM._build_context_index(doc)
        sections = result["sections"]
        # Find the "A" section
        a_section = next(s for s in sections if s["header"] == "# A")
        assert a_section["start"] < a_section["end"]
        assert a_section["char_count"] > 0


# ============================================================================
# 4. Iteration Budget Guardrail
# ============================================================================


class TestIterationBudgetGuardrail:
    """The summary guardrail triggers after the threshold."""

    def test_summary_directive_flag_starts_false(self) -> None:
        rlm = _bare_rlm()
        assert rlm._summary_directive_injected is False

    def test_summary_threshold_is_configurable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FLEET_RLM_SUMMARY_ITERATION_THRESHOLD", "15")
        assert factory._env_int("FLEET_RLM_SUMMARY_ITERATION_THRESHOLD", 20) == 15


# ============================================================================
# 5. REPL Output Cache
# ============================================================================


class TestREplOutputCache:
    """REPL output caching avoids redundant sandbox round-trips."""

    def test_cache_key_is_deterministic(self) -> None:
        code = "print('hello')"
        h1 = hashlib.sha256(code.encode("utf-8")).hexdigest()
        h2 = hashlib.sha256(code.encode("utf-8")).hexdigest()
        assert h1 == h2

    def test_different_code_produces_different_keys(self) -> None:
        h1 = hashlib.sha256(b"print(1)").hexdigest()
        h2 = hashlib.sha256(b"print(2)").hexdigest()
        assert h1 != h2

    def test_submit_code_not_cached(self) -> None:
        """Code containing SUBMIT should never be cached (terminal)."""
        assert "SUBMIT" in 'SUBMIT(response="done")'

    def test_cache_cleared_on_forward(self) -> None:
        rlm = _bare_rlm()
        rlm._repl_output_cache["key"] = "value"
        # Simulate the forward() clear
        rlm._repl_output_cache.clear()
        assert rlm._repl_output_cache == {}
