from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.integrations.observability.config import MlflowConfig


def test_configure_auto_assessment_registers_scorers(monkeypatch: pytest.MonkeyPatch):
    from fleet_rlm.integrations.observability import auto_assessment

    registered: list[dict[str, object]] = []

    class FakeScorerScheduleConfig:
        def __init__(self, *, scorer, scheduled_scorer_name, sample_rate, filter_string=None):
            registered.append(
                {
                    "scorer_name": scheduled_scorer_name,
                    "sample_rate": sample_rate,
                }
            )

    monkeypatch.setattr(auto_assessment, "_ScorerScheduleConfig", FakeScorerScheduleConfig)
    # Provide fake scorers in the registry
    monkeypatch.setattr(
        auto_assessment,
        "_SCORER_REGISTRY",
        {
            "safety": SimpleNamespace(name="safety"),
            "guidelines": SimpleNamespace(name="guidelines"),
        },
    )

    config = MlflowConfig(
        enabled=True,
        enable_auto_assessment=True,
        auto_assessment_scorers=["safety", "guidelines"],
        auto_assessment_sample_rate=0.5,
    )
    result = auto_assessment.configure_auto_assessment(config)
    assert result is True
    assert len(registered) == 2
    assert registered[0]["sample_rate"] == 0.5
    assert registered[0]["scorer_name"] == "fleet_rlm_safety"
    assert registered[1]["scorer_name"] == "fleet_rlm_guidelines"


def test_configure_auto_assessment_noop_when_disabled():
    from fleet_rlm.integrations.observability import auto_assessment

    config = MlflowConfig(enabled=True, enable_auto_assessment=False)
    result = auto_assessment.configure_auto_assessment(config)
    assert result is False


def test_configure_auto_assessment_handles_missing_scorer_schedule_config(monkeypatch: pytest.MonkeyPatch):
    from fleet_rlm.integrations.observability import auto_assessment

    monkeypatch.setattr(auto_assessment, "_ScorerScheduleConfig", None)

    config = MlflowConfig(enabled=True, enable_auto_assessment=True)
    result = auto_assessment.configure_auto_assessment(config)
    assert result is False


def test_configure_auto_assessment_skips_unknown_scorers(monkeypatch: pytest.MonkeyPatch):
    from fleet_rlm.integrations.observability import auto_assessment

    registered: list[str] = []

    class FakeScorerScheduleConfig:
        def __init__(self, *, scorer, scheduled_scorer_name, sample_rate, filter_string=None):
            registered.append(scheduled_scorer_name)

    monkeypatch.setattr(auto_assessment, "_ScorerScheduleConfig", FakeScorerScheduleConfig)
    monkeypatch.setattr(
        auto_assessment,
        "_SCORER_REGISTRY",
        {
            "safety": SimpleNamespace(name="safety"),
        },
    )

    config = MlflowConfig(
        enabled=True,
        enable_auto_assessment=True,
        auto_assessment_scorers=["safety", "nonexistent"],
        auto_assessment_sample_rate=1.0,
    )
    result = auto_assessment.configure_auto_assessment(config)
    assert result is True
    assert registered == ["fleet_rlm_safety"]
