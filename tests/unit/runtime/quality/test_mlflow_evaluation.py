from __future__ import annotations

import fleet_rlm.runtime.quality.mlflow_evaluation as mlflow_evaluation
import fleet_rlm.runtime.quality.scorers as scorer_module


def test_dataset_supports_retrieval_groundedness_requires_retrievers_for_all_rows() -> (
    None
):
    assert (
        mlflow_evaluation._dataset_supports_retrieval_groundedness(
            [
                {"trace_id": "trace-1", "span_types": ["LLM", "RETRIEVER"]},
                {"trace_id": "trace-2", "span_types": ["RETRIEVER", "TOOL"]},
            ]
        )
        is True
    )
    assert (
        mlflow_evaluation._dataset_supports_retrieval_groundedness(
            [
                {"trace_id": "trace-1", "span_types": ["LLM", "RETRIEVER"]},
                {"trace_id": "trace-2", "span_types": ["LLM", "TOOL"]},
            ]
        )
        is False
    )


def test_build_rlm_scorers_skips_retrieval_groundedness_when_disabled(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        scorer_module, "RelevanceToQuery", lambda model: f"relevance:{model}"
    )
    monkeypatch.setattr(
        scorer_module, "ToolCallCorrectness", lambda model: f"correctness:{model}"
    )
    monkeypatch.setattr(
        scorer_module, "ToolCallEfficiency", lambda model: f"efficiency:{model}"
    )
    monkeypatch.setattr(
        scorer_module, "RetrievalGroundedness", lambda model: f"retrieval:{model}"
    )

    scorers = scorer_module.build_rlm_scorers(
        model="judge-model",
        include_retrieval_groundedness=False,
    )

    assert scorers == [
        "relevance:judge-model",
        "correctness:judge-model",
        "efficiency:judge-model",
    ]
