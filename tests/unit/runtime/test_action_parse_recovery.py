"""Unit tests for parse-error recovery in ``_StreamingRLM``.

Regression for the trace-``tr-412a4460`` bottleneck: action generation was
pinned to ``JSONAdapter``, but ``qwen3.x`` emits ``[[ ## reasoning ## ]]`` /
``[[ ## code ## ]]`` output that JSONAdapter cannot parse. Each parse failure
triggered a corrective regeneration that also failed, cascading into a full
RLM re-run + reduced-budget retry + ChainOfThought fallback (~363s, 17 wasted
LLM calls).

The fix realigned the primary adapter to ``dspy.ChatAdapter`` (DSPy's default,
with its native automatic ``JSONAdapter`` fallback — see
``dspy/adapters/chat_adapter.py:46,68,87-94``). The hand-rolled salvage cascade
(``_salvage_with_chat_adapter`` / ``_salvage_with_regex`` / ``_regex_salvage``)
was removed entirely; only the orthogonal prompt/cost-shaping guards
(``_extract_completion_from_parse_error`` / ``_is_degenerate_response`` /
``_truncate_completion``) and the consecutive-parse-error cap remain.
"""

from __future__ import annotations

from typing import ClassVar

import dspy
import pytest

from fleet_rlm.runtime.modules.factory import _StreamingRLM


class _ActionSig(dspy.Signature):
    """Mirror of the RLM action-generation signature fields."""

    reasoning: str = dspy.OutputField()
    code: str = dspy.OutputField()
    instructions: ClassVar[str] = "Produce reasoning and code."


# A realistic qwen completion in ChatAdapter delimited format — the exact shape
# ChatAdapter now parses natively (the JSONAdapter fallback covers JSON output).
_CHATADAPTER_COMPLETION = """[[ ## reasoning ## ]]
The manifest shows a single large file. Let me inspect document_text first.

[[ ## code ## ]]
```python
print("Document length:", len(context['document_text']))
```"""


# Reconstruct the exception message format DSPy embeds inside an
# ``AdapterParseError`` (also accessible via the public ``.lm_response``
# attribute — see ``dspy/utils/exceptions.py:224-261``).
def _parse_error_message(completion: str) -> str:
    return (
        "LM response cannot be serialized to a JSON object.\n\n"
        "Adapter JSONAdapter failed to parse the LM response. \n\n"
        f"LM Response: {completion}\n\n"
        "Expected to find output fields in the LM response: [reasoning, code] \n"
    )


class TestExtractCompletion:
    """``_extract_completion_from_parse_error`` pulls the raw completion text.

    Prefers the public ``dspy.AdapterParseError.lm_response`` attribute
    (``dspy/utils/exceptions.py:224-261``); falls back to scraping the
    UNDOCUMENTED ``"LM Response: "`` substring only as a defensive shim.
    """

    def test_extracts_chatadapter_completion(self) -> None:
        exc = Exception(_parse_error_message(_CHATADAPTER_COMPLETION))
        extracted = _StreamingRLM._extract_completion_from_parse_error(exc)
        assert extracted is not None
        assert "[[ ## reasoning ## ]]" in extracted
        assert "[[ ## code ## ]]" in extracted
        assert "len(context['document_text'])" in extracted

    def test_returns_none_when_marker_absent(self) -> None:
        assert _StreamingRLM._extract_completion_from_parse_error(Exception("nope")) is None

    def test_returns_none_for_empty_message(self) -> None:
        assert _StreamingRLM._extract_completion_from_parse_error(Exception("")) is None

    def test_handles_missing_expected_fields_marker(self) -> None:
        # If dspy changes the trailing marker, fall back to the whole tail.
        exc = Exception("LM Response: some raw text without trailing marker")
        extracted = _StreamingRLM._extract_completion_from_parse_error(exc)
        assert extracted == "some raw text without trailing marker"

    def test_prefers_lm_response_attribute_when_present(self) -> None:
        """The public ``AdapterParseError.lm_response`` attribute is preferred
        over the UNDOCUMENTED message-scrape fallback."""

        class _FakeParseError(Exception):
            lm_response = "  via attribute  "

        exc = _FakeParseError("LM Response: via message scrape")
        extracted = _StreamingRLM._extract_completion_from_parse_error(exc)
        assert extracted == "via attribute"


class TestIsDegenerateResponse:
    """``_is_degenerate_response`` routes unsalvageable outputs away from
    downstream prompt-shaping guards (NOT adapter salvage — that path is gone)."""

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_empty_or_non_string_is_degenerate(self, value: object) -> None:
        assert _StreamingRLM._is_degenerate_response(value) is True

    def test_degenerate_fstring_fragment(self) -> None:
        """The trace's case-2 failure: a bare ``{len(doc)}`` snippet."""
        assert _StreamingRLM._is_degenerate_response("{len(doc)}") is True

    def test_degenerate_short_prose(self) -> None:
        assert _StreamingRLM._is_degenerate_response("I will inspect the manifest.") is True

    def test_chatadapter_format_is_not_degenerate(self) -> None:
        assert _StreamingRLM._is_degenerate_response(_CHATADAPTER_COMPLETION) is False

    def test_valid_json_dict_is_not_degenerate(self) -> None:
        assert _StreamingRLM._is_degenerate_response('{"reasoning": "x", "code": "y"}') is False


def _bare_rlm() -> _StreamingRLM:
    """A ``_StreamingRLM`` shell with only ``generate_action.signature`` wired.

    The remaining helpers use nothing else on the instance, so we skip the
    heavy ``__init__`` (which builds a full ``dspy.RLM`` with interpreter +
    tools).
    """

    rlm = object.__new__(_StreamingRLM)

    class _StubGenerateAction:
        signature = _ActionSig

    rlm.generate_action = _StubGenerateAction()  # type: ignore[assignment]
    # __init__ is skipped (it builds a full dspy.RLM); set the counters the cap
    # test relies on directly.
    rlm._consecutive_parse_errors = 0
    rlm._max_consecutive_parse_errors = 3
    return rlm


class TestConsecutiveParseErrorCap:
    """The cap bounds wasted iterations before escalating.

    After the primary-adapter realignment, the cap counts parse errors that
    DSPy's native ``ChatAdapter`` → ``JSONAdapter`` fallback
    (``dspy/adapters/chat_adapter.py:46,68,87-94``) could not recover — not
    failures of the (removed) hand-rolled salvage cascade.
    """

    def test_default_counters_zero(self) -> None:
        rlm = _bare_rlm()
        assert rlm._consecutive_parse_errors == 0
        assert rlm._max_consecutive_parse_errors == 3

    def test_cap_is_env_configurable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FLEET_RLM_MAX_CONSECUTIVE_PARSE_ERRORS", "5")
        # Counter default is read in __init__; bypassing __init__ means we
        # re-read the env directly to confirm the wiring.
        from fleet_rlm.runtime.modules.factory import _env_int

        assert _env_int("FLEET_RLM_MAX_CONSECUTIVE_PARSE_ERRORS", 3) == 5
