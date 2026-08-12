from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from scripts.benchmarks.scorers import (
    BUILTIN_SCORER_NAMES,
    CUSTOM_SCORER_NAMES,
    DEFAULT_GUIDELINES,
    SCORER_NAMES,
    build_scorer,
    build_scorers,
    response_present_impl,
    tool_evidence_used_impl,
)


class _FakeSpan:
    def __init__(self, span_type: str, outputs: Any = None) -> None:
        self.span_type = span_type
        self.outputs = outputs


class _FakeTrace:
    def __init__(self, spans: list[_FakeSpan] | None = None) -> None:
        self.data = SimpleNamespace(spans=list(spans or []))


def test_response_present_requires_non_empty_output() -> None:
    assert response_present_impl(outputs="Yes, the deadline was timely.")
    assert response_present_impl(outputs=" x ")
    assert not response_present_impl(outputs=None)
    assert not response_present_impl(outputs="")
    assert not response_present_impl(outputs="   ")


def test_tool_evidence_used_requires_tool_spans_covering_evidence() -> None:
    trace = _FakeTrace([_FakeSpan("TOOL", "checked A1 and A8, confirmed receipt on 2025-02-28")])
    assert tool_evidence_used_impl(trace=trace, expectations={"required_evidence": ["A1", "A8"]})
    assert not tool_evidence_used_impl(trace=trace, expectations={"required_evidence": ["A1", "A9"]})


def test_tool_evidence_used_is_strict_without_evidence() -> None:
    trace = _FakeTrace([_FakeSpan("TOOL", "checked A1")])
    assert not tool_evidence_used_impl(trace=trace, expectations={})
    assert not tool_evidence_used_impl(trace=trace, expectations={"required_evidence": []})
    assert not tool_evidence_used_impl(trace=trace, expectations={"required_evidence": "A1"})
    assert not tool_evidence_used_impl(trace=None, expectations={"required_evidence": ["A1"]})
    llm_only = _FakeTrace([_FakeSpan("LLM", "A1 appears")])
    assert not tool_evidence_used_impl(trace=llm_only, expectations={"required_evidence": ["A1"]})


def _install_fake_mlflow_scorers(monkeypatch: pytest.MonkeyPatch) -> None:
    scorers_mod = ModuleType("mlflow.genai.scorers")

    def fake_scorer(func=None, **_kwargs):
        if func is not None:
            return func
        return lambda function: function

    class _FakeGuidelines:
        def __init__(self, *, name, guidelines, model) -> None:
            self.name = name
            self.guidelines = guidelines
            self.model = model

    class _FakeRetrievalGroundedness:
        def __init__(self, *, name, model) -> None:
            self.name = name
            self.model = model

    scorers_mod.scorer = fake_scorer  # type: ignore[attr-defined]
    scorers_mod.Guidelines = _FakeGuidelines  # type: ignore[attr-defined]
    scorers_mod.RetrievalGroundedness = _FakeRetrievalGroundedness  # type: ignore[attr-defined]

    genai_mod = ModuleType("mlflow.genai")
    genai_mod.scorers = scorers_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "mlflow.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "mlflow.genai.scorers", scorers_mod)


def test_custom_scorers_build_without_judge_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_mlflow_scorers(monkeypatch)

    present = build_scorer("response_present")
    evidence = build_scorer("tool_evidence_used")

    assert present is response_present_impl
    assert evidence is tool_evidence_used_impl


def test_builtin_scorers_require_judge_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_mlflow_scorers(monkeypatch)

    with pytest.raises(ValueError, match="judge-model"):
        build_scorer("guidelines")
    with pytest.raises(ValueError, match="judge-model"):
        build_scorer("retrieval_groundedness")

    guidelines = build_scorer("guidelines", judge_model="databricks:/model")
    assert guidelines.name == "guidelines"
    assert guidelines.guidelines == DEFAULT_GUIDELINES
    assert guidelines.model == "databricks:/model"

    groundedness = build_scorer("retrieval_groundedness", judge_model="databricks:/model")
    assert groundedness.name == "retrieval_groundedness"
    assert groundedness.model == "databricks:/model"


def test_build_scorer_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown"):
        build_scorer("not_a_scorer")


def test_build_scorers_preserves_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_mlflow_scorers(monkeypatch)

    scorers = build_scorers(["response_present", "tool_evidence_used"], judge_model="databricks:/model")

    assert scorers == [response_present_impl, tool_evidence_used_impl]


def test_scorer_name_catalog_is_disjoint_and_explicit() -> None:
    assert set(CUSTOM_SCORER_NAMES).isdisjoint(BUILTIN_SCORER_NAMES)
    assert SCORER_NAMES == CUSTOM_SCORER_NAMES + BUILTIN_SCORER_NAMES
    assert SCORER_NAMES == ("response_present", "tool_evidence_used", "guidelines", "retrieval_groundedness")
