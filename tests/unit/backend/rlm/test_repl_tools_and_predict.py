"""Tests for REPL tools (paging, searching) and typed predict() helper."""

from __future__ import annotations

import dspy

from fleet_rlm.rlm.recursion import predict
from fleet_rlm.rlm.tools import ContextInspector, page, search


def test_page_slices_within_bounds() -> None:
    text = "abcdefghijklmnopqrstuvwxyz"
    res1 = page(text, offset=0, limit=5)
    assert res1.content == "abcde"
    assert res1.offset == 0
    assert res1.limit == 5
    assert res1.total_chars == 26
    assert res1.has_more is True

    res2 = page(text, offset=20, limit=10)
    assert res2.content == "uvwxyz"
    assert res2.has_more is False

    res_empty = page("", offset=0, limit=10)
    assert res_empty.content == ""
    assert res_empty.has_more is False


def test_search_finds_matches_with_line_numbers() -> None:
    text = "line 1: hello\nline 2: world\nline 3: hello again\n"
    matches = search(text, "hello")
    assert len(matches) == 2
    assert matches[0].line_number == 1
    assert "line 1: hello" in matches[0].excerpt
    assert matches[1].line_number == 3
    assert "line 3: hello again" in matches[1].excerpt

    empty = search(text, "")
    assert empty == []


def test_context_inspector_delegates_to_page_and_search() -> None:
    inspector = ContextInspector({"doc.txt": "alpha beta gamma delta"})
    inspector.add("log.txt", "error at 12:00: server down\nall clear at 12:05\n")

    p = inspector.page("doc.txt", offset=0, limit=5)
    assert p.content == "alpha"
    assert p.has_more is True

    m = inspector.search("log.txt", "server down")
    assert len(m) == 1
    assert m[0].line_number == 1
    assert "server down" in m[0].excerpt


def test_predict_executes_typed_signature() -> None:
    class EchoSig(dspy.Signature):
        """Echo the input text."""

        text: str = dspy.InputField()
        echo: str = dspy.OutputField()

    class FakeLM(dspy.LM):
        def __init__(self) -> None:
            super().__init__(model="test/fake")

        def __call__(
            self,
            prompt: str | list[dict[str, str]] | None = None,
            messages: list[dict[str, str]] | None = None,
            **kwargs: object,
        ) -> list[str]:
            del prompt, messages, kwargs
            return ["[[ ## echo ## ]]\ntest"]

    lm = FakeLM()
    prediction = predict(EchoSig, lm=lm, text="test")
    assert prediction.echo == "test"
    assert isinstance(prediction, dspy.Prediction)
