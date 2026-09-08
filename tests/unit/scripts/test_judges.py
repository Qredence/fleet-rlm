from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from scripts.benchmarks.judges import (
    CORRECTNESS_INSTRUCTIONS,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_REFLECTION_MODEL,
    EVIDENCE_COVERAGE_INSTRUCTIONS,
    JUDGE_INFERENCE_PARAMS,
    JUDGE_NAMES,
    build_judge,
    ensure_registered,
    normalized_judge_policy,
)


def _install_fake_genai(monkeypatch: pytest.MonkeyPatch, *, registered: list | None = None) -> SimpleNamespace:
    calls = SimpleNamespace(registered=[])

    def make_judge(**kwargs: Any) -> Any:
        scorer = SimpleNamespace(**kwargs)

        def register(*, experiment_id: str) -> None:
            calls.registered.append((scorer.name, experiment_id))

        scorer.register = register
        return scorer

    judges_mod = ModuleType("mlflow.genai.judges")
    judges_mod.make_judge = make_judge  # type: ignore[attr-defined]
    scorers_mod = ModuleType("mlflow.genai.scorers")
    scorers_mod.list_scorers = lambda **_kwargs: list(registered or [])  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "mlflow.genai.judges", judges_mod)
    monkeypatch.setitem(sys.modules, "mlflow.genai.scorers", scorers_mod)
    return calls


def test_judge_registry_contract_is_two_boolean_gateway_judges() -> None:
    assert JUDGE_NAMES == ("correctness", "evidence_coverage")
    assert DEFAULT_JUDGE_MODEL == "databricks:/databricks-qwen35-122b-a10b"
    assert DEFAULT_REFLECTION_MODEL.startswith("databricks:/")
    assert DEFAULT_EMBEDDING_MODEL == "databricks:/databricks-gte-large-en"
    assert JUDGE_INFERENCE_PARAMS == {"temperature": 0, "reasoning_effort": "low"}
    assert "expected_response" in CORRECTNESS_INSTRUCTIONS
    assert "required_evidence" in EVIDENCE_COVERAGE_INSTRUCTIONS


def test_build_judge_wires_fleet_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_genai(monkeypatch)
    judge = build_judge("correctness", "databricks:/databricks-qwen35-122b-a10b")
    assert judge.name == "correctness"
    assert judge.model == "databricks:/databricks-qwen35-122b-a10b"
    assert judge.feedback_value_type is bool

    with pytest.raises(ValueError, match="unknown Fleet judge"):
        build_judge("not-a-judge", "databricks:/databricks-qwen35-122b-a10b")


def test_build_judge_supports_rationale_first_without_changing_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_genai(monkeypatch)
    baseline = build_judge("correctness", "databricks:/judge")
    rationale_first = build_judge("correctness", "databricks:/judge", generate_rationale_first=True)

    assert baseline.generate_rationale_first is False
    assert rationale_first.generate_rationale_first is True
    assert normalized_judge_policy(baseline)["generate_rationale_first"] is False
    assert normalized_judge_policy(rationale_first)["generate_rationale_first"] is True


def test_normalized_real_mlflow_judge_uses_behavior_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "mlflow.genai.judges", raising=False)
    judge = build_judge("correctness", "databricks:/judge", generate_rationale_first=True)

    policy = normalized_judge_policy(judge)

    assert policy["model"] == "databricks:/judge"
    assert policy["instructions"] == CORRECTNESS_INSTRUCTIONS
    assert policy["inference_params"] == JUDGE_INFERENCE_PARAMS
    assert policy["generate_rationale_first"] is True


def test_ensure_registered_registers_only_on_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_genai(monkeypatch)

    assert ensure_registered("correctness", "databricks:/databricks-qwen35-122b-a10b", experiment_id="42") is True
    assert calls.registered == [("correctness", "42")]

    matching = SimpleNamespace(
        name="correctness",
        model="databricks:/databricks-qwen35-122b-a10b",
        description=build_judge("correctness", "databricks:/databricks-qwen35-122b-a10b").description,
        instructions=build_judge("correctness", "databricks:/databricks-qwen35-122b-a10b").instructions,
        feedback_value_type=bool,
        inference_params=JUDGE_INFERENCE_PARAMS,
    )
    calls.registered.clear()
    monkeypatch.setitem(
        sys.modules,
        "mlflow.genai.scorers",
        SimpleNamespace(list_scorers=lambda **_kwargs: [matching]),
    )
    assert ensure_registered("correctness", "databricks:/databricks-qwen35-122b-a10b", experiment_id="42") is False
    assert calls.registered == []

    matching.model = "gateway:/other-endpoint"
    assert ensure_registered("correctness", "databricks:/databricks-qwen35-122b-a10b", experiment_id="42") is True


def test_registry_drift_includes_the_rationale_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_genai(monkeypatch)
    matching = build_judge("correctness", "databricks:/databricks-qwen35-122b-a10b")
    matching.generate_rationale_first = False
    monkeypatch.setitem(
        sys.modules,
        "mlflow.genai.scorers",
        SimpleNamespace(list_scorers=lambda **_kwargs: [matching]),
    )

    assert (
        ensure_registered(
            "correctness",
            "databricks:/databricks-qwen35-122b-a10b",
            experiment_id="42",
            generate_rationale_first=True,
        )
        is True
    )
    assert calls.registered == [("correctness", "42")]
