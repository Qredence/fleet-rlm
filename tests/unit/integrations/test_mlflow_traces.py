from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class _FakeTrace:
    def __init__(self, assessments: list[dict[str, Any]]) -> None:
        self._assessments = assessments

    def to_dict(self) -> dict[str, Any]:
        return {
            "info": {
                "trace_id": "tr-1",
                "request_preview": "request",
                "response_preview": "response",
                "trace_metadata": {},
            }
        }

    def search_assessments(self) -> list[Any]:
        return [SimpleNamespace(to_dictionary=lambda item=item: item) for item in self._assessments]

    def search_spans(self) -> list[Any]:
        return []


def test_trace_to_dataset_row_skips_disabled_persisted_scorer_feedback(monkeypatch) -> None:
    from fleet_rlm.integrations.observability import mlflow_traces
    from fleet_rlm.integrations.observability.config import MlflowConfig

    monkeypatch.setattr(mlflow_traces, "_disabled_persisted_scorer_names", lambda config: {"Trace Judge"})

    row = mlflow_traces.trace_to_dataset_row(
        _FakeTrace(
            [
                {
                    "assessment_name": "Trace Judge",
                    "source": {"source_type": "LLM_JUDGE", "source_id": "gateway:/gemini"},
                    "feedback": {"value": "yes"},
                    "rationale": "No tools were called.",
                },
                {
                    "assessment_name": "response_is_correct",
                    "source": {"source_type": "HUMAN", "source_id": "user"},
                    "feedback": {"value": True},
                    "rationale": "Looks right.",
                },
            ]
        ),
        config=MlflowConfig(enable_auto_assessment=False),
    )

    assert "Trace Judge" not in row.get("feedback", {})
    assert row["feedback"]["response_is_correct"]["value"] is True
    assert row["skipped_feedback"] == [
        {
            "assessment_name": "Trace Judge",
            "source_type": "LLM_JUDGE",
            "source_id": "gateway:/gemini",
            "reason": "persisted_scorer_while_fleet_auto_assessment_disabled",
        }
    ]


def test_search_traces_uses_locations_when_supported() -> None:
    from fleet_rlm.integrations.observability.mlflow_traces import _search_traces

    captured: dict[str, Any] = {}

    def search_traces(
        *,
        locations: list[str] | None = None,
        experiment_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        kwargs["locations"] = locations
        kwargs["experiment_ids"] = experiment_ids
        captured.update(kwargs)
        return []

    _search_traces(
        SimpleNamespace(search_traces=search_traces),
        experiment_ids=["1"],
        max_results=5,
        return_type="list",
        include_spans=False,
    )

    assert captured["locations"] == ["1"]
    assert captured["experiment_ids"] is None
