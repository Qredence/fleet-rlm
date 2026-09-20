"""Verbatim code execution and normalization without prompt rewrite hacks."""

from __future__ import annotations

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.events import RLMCode
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


def test_interpreter_execute_preserves_code_verbatim_without_rewrite() -> None:
    captured: list[str] = []

    def llm_query(prompt: str) -> str:
        captured.append(prompt)
        return prompt

    observed: list[object] = []
    interpreter = DaytonaCodeInterpreter(
        backend=InProcessInterpreterBackend(),
        tools={"llm_query": llm_query},
    )
    interpreter.bind_observer(observed.append, max_chars=4_000)
    interpreter.bind_turn_request("Return exactly ROOT in the prompt")
    original_code = 'single_result = llm_query("my original prompt")\n_out = single_result'
    interpreter.execute(original_code)

    codes = [item.code for item in observed if isinstance(item, RLMCode)]
    assert codes
    assert "my original prompt" in codes[0]
    assert "Return exactly ROOT" not in codes[0]
    assert captured == ["my original prompt"]
