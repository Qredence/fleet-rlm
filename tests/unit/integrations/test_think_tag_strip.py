"""Tests for ThinkTagStripCallback — DeepSeek </think> tag stripping."""

from __future__ import annotations

from fleet_rlm.integrations.observability.mlflow_runtime import (
    ThinkTagStripCallback,
    _strip_think_tags,
)

# ---------------------------------------------------------------------------
# _strip_think_tags unit tests
# ---------------------------------------------------------------------------


def test_strip_think_tags_removes_block() -> None:
    raw = "<think>some reasoning</think>[[ ## assistant_response ## ]]\nHello."
    assert _strip_think_tags(raw) == "[[ ## assistant_response ## ]]\nHello."


def test_strip_think_tags_multiline() -> None:
    raw = "<think>\nline one\nline two\n</think>[[ ## reasoning ## ]]\nok"
    result = _strip_think_tags(raw)
    assert "<think>" not in result
    assert "line one" not in result
    assert "[[ ## reasoning ## ]]" in result


def test_strip_think_tags_case_insensitive() -> None:
    raw = "<THINK>upper</THINK>after"
    assert _strip_think_tags(raw) == "after"


def test_strip_think_tags_no_think_block_unchanged() -> None:
    raw = "[[ ## reasoning ## ]]\nsome reasoning\n[[ ## assistant_response ## ]]\nHello."
    assert _strip_think_tags(raw) == raw


def test_strip_think_tags_empty_string() -> None:
    assert _strip_think_tags("") == ""


# ---------------------------------------------------------------------------
# ThinkTagStripCallback.on_lm_end tests
# ---------------------------------------------------------------------------


def _make_outputs(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_callback_strips_think_from_message_content() -> None:
    cb = ThinkTagStripCallback()
    raw = "<think>reasoning</think>[[ ## assistant_response ## ]]\nHi."
    outputs = _make_outputs(raw)
    cb.on_lm_end("id", outputs)
    assert outputs["choices"][0]["message"]["content"] == "[[ ## assistant_response ## ]]\nHi."


def test_callback_strips_case_insensitive_think_from_message_content() -> None:
    cb = ThinkTagStripCallback()
    outputs = _make_outputs("<THINK>reasoning</THINK>answer")
    cb.on_lm_end("id", outputs)
    assert outputs["choices"][0]["message"]["content"] == "answer"


def test_callback_noop_when_no_think_tag() -> None:
    cb = ThinkTagStripCallback()
    raw = "[[ ## reasoning ## ]]\nok\n[[ ## assistant_response ## ]]\nHi."
    outputs = _make_outputs(raw)
    cb.on_lm_end("id", outputs)
    assert outputs["choices"][0]["message"]["content"] == raw


def test_callback_strips_text_completion_style() -> None:
    cb = ThinkTagStripCallback()
    outputs = {"choices": [{"text": "<think>cot</think>answer"}]}
    cb.on_lm_end("id", outputs)
    assert outputs["choices"][0]["text"] == "answer"


def test_callback_noop_on_none_outputs() -> None:
    cb = ThinkTagStripCallback()
    # Must not raise
    cb.on_lm_end("id", None)


def test_callback_noop_on_missing_choices() -> None:
    cb = ThinkTagStripCallback()
    outputs: dict = {"choices": []}
    cb.on_lm_end("id", outputs)
    assert outputs["choices"] == []


def test_callback_handles_non_string_content() -> None:
    cb = ThinkTagStripCallback()
    outputs = {"choices": [{"message": {"content": None}}]}
    cb.on_lm_end("id", outputs)  # must not raise
    assert outputs["choices"][0]["message"]["content"] is None


def test_callback_multi_choice_all_stripped() -> None:
    cb = ThinkTagStripCallback()
    outputs = {
        "choices": [
            {"message": {"content": "<think>a</think>first"}},
            {"message": {"content": "<think>b</think>second"}},
        ]
    }
    cb.on_lm_end("id", outputs)
    assert outputs["choices"][0]["message"]["content"] == "first"
    assert outputs["choices"][1]["message"]["content"] == "second"
