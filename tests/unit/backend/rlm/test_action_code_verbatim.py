"""Verbatim code execution and normalization without prompt rewrite hacks."""

from __future__ import annotations

from fleet_rlm.rlm.submit_validation import normalize_action_code


def test_normalize_action_code_treats_quote_style_as_equivalent() -> None:
    double = 'single_result = llm_query("Return exactly ROOT")'
    single = "single_result = llm_query('Return exactly ROOT')"
    assert normalize_action_code(double) == normalize_action_code(single)


def test_normalize_action_code_strips_markdown_fences() -> None:
    fenced = "```python\nx = 1\n```"
    unfenced = "x = 1"
    assert normalize_action_code(fenced) == normalize_action_code(unfenced)


def test_normalize_action_code_handles_non_strings_and_syntax_errors() -> None:
    assert normalize_action_code(None) == ""
    assert normalize_action_code(123) == "123"
    assert normalize_action_code("def syntax error ((") == "def syntax error (("
