from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass
from types import SimpleNamespace

import pytest


@dataclass
class _FakeAssessmentSource:
    source_type: str
    source_id: str


@dataclass
class _FakeFeedback:
    value: object
    rationale: str
    source: object | None = None


class _FakeJudge:
    def __init__(self, *, model: str) -> None:
        self.model = model


def _install_fake_mlflow(monkeypatch: pytest.MonkeyPatch) -> None:
    mlflow_module = types.ModuleType("mlflow")
    entities_module = types.ModuleType("mlflow.entities")
    genai_module = types.ModuleType("mlflow.genai")
    scorers_module = types.ModuleType("mlflow.genai.scorers")

    entities_module.AssessmentSource = _FakeAssessmentSource  # ty: ignore[unresolved-attribute]
    entities_module.Feedback = _FakeFeedback  # ty: ignore[unresolved-attribute]

    def _fake_scorer(*, name: str):
        def decorator(fn):
            fn._scorer_name = name
            return fn

        return decorator

    class RelevanceToQuery(_FakeJudge):
        pass

    class RetrievalGroundedness(_FakeJudge):
        pass

    class ToolCallCorrectness(_FakeJudge):
        pass

    class ToolCallEfficiency(_FakeJudge):
        pass

    scorers_module.RelevanceToQuery = RelevanceToQuery  # ty: ignore[unresolved-attribute]
    scorers_module.RetrievalGroundedness = RetrievalGroundedness  # ty: ignore[unresolved-attribute]
    scorers_module.ToolCallCorrectness = ToolCallCorrectness  # ty: ignore[unresolved-attribute]
    scorers_module.ToolCallEfficiency = ToolCallEfficiency  # ty: ignore[unresolved-attribute]
    scorers_module.scorer = _fake_scorer  # ty: ignore[unresolved-attribute]

    monkeypatch.setitem(sys.modules, "mlflow", mlflow_module)
    monkeypatch.setitem(sys.modules, "mlflow.entities", entities_module)
    monkeypatch.setitem(sys.modules, "mlflow.genai", genai_module)
    monkeypatch.setitem(sys.modules, "mlflow.genai.scorers", scorers_module)


def _import_scorers_module(monkeypatch: pytest.MonkeyPatch):
    _install_fake_mlflow(monkeypatch)
    sys.modules.pop("fleet_rlm.quality.scorers", None)
    return importlib.import_module("fleet_rlm.quality.scorers")


def test_build_rlm_scorers_uses_env_model_and_optional_reasoning_judge(clean_runtime_env, monkeypatch) -> None:
    module = _import_scorers_module(monkeypatch)
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/custom-judge")
    monkeypatch.setenv("FLEET_RLM_ENABLE_REASONING_JUDGE", "true")

    scorers = module.build_rlm_scorers(include_retrieval_groundedness=False)

    assert [type(item).__name__ for item in scorers[:3]] == [
        "RelevanceToQuery",
        "ToolCallCorrectness",
        "ToolCallEfficiency",
    ]
    assert [item.model for item in scorers[:3]] == ["openai/custom-judge"] * 3
    assert getattr(scorers[3], "_scorer_name") == "reasoning_quality"


def test_reasoning_quality_scorer_uses_predict_judge_and_redacts_trace_inputs(clean_runtime_env, monkeypatch) -> None:
    module = _import_scorers_module(monkeypatch)
    fake_lm = SimpleNamespace(model="demo-model")
    captured: dict[str, object] = {}

    def fake_judge(lm, reasoning_text):
        captured["lm"] = lm
        captured["reasoning_text"] = reasoning_text
        return 5, "Clear step-by-step logic."

    monkeypatch.setattr(module, "_judge_reasoning", fake_judge)
    monkeypatch.setattr(
        "fleet_rlm.runtime.config.resolve_lm",
        lambda role, **kwargs: fake_lm,
    )
    trace = SimpleNamespace(
        search_spans=lambda: [SimpleNamespace(name="Thought 1", inputs={"token": "super-secret", "note": "keep"})]
    )

    judge = module.reasoning_quality_scorer("openai:/demo-model")
    feedback = judge(trace)

    assert feedback == _FakeFeedback(
        value=5,
        rationale="Clear step-by-step logic.",
        source=_FakeAssessmentSource(source_type="LLM_JUDGE", source_id="demo-model"),
    )
    assert captured["lm"] is fake_lm
    reasoning_text = str(captured["reasoning_text"])
    assert "***" in reasoning_text
    assert "super-secret" not in reasoning_text


def test_get_default_judge_model_returns_repo_default(clean_runtime_env, monkeypatch) -> None:
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gemini-3-flash-preview")
    module = _import_scorers_module(monkeypatch)

    assert module.get_default_judge_model() == "openai/gemini-3-flash-preview"
