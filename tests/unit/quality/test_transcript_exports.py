from __future__ import annotations

import pytest

from fleet_rlm.quality import module_registry
from fleet_rlm.quality.module_registry import ModuleOptimizationSpec
from fleet_rlm.quality.transcript_exports import build_transcript_dataset_rows


def _dummy_factory() -> None:
    return None


def _dummy_converter(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return rows


def _dummy_metric() -> None:
    return None


def _register_spec(*, slug: str, label: str, input_keys: list[str], required_keys: list[str]) -> None:
    module_registry.register_module(
        ModuleOptimizationSpec(
            module_slug=slug,
            label=label,
            program_spec=f"{slug}:Program",
            artifact_filename=f"{slug}.json",
            input_keys=input_keys,
            required_dataset_keys=required_keys,
            module_factory=_dummy_factory,
            row_converter=_dummy_converter,
            metric_builder=_dummy_metric,
        )
    )


def test_build_transcript_dataset_rows_maps_user_and_assistant_messages(monkeypatch) -> None:
    monkeypatch.setattr(module_registry, "_MODULE_ENTRYPOINTS", ())
    module_registry._reset_registry()
    _register_spec(
        slug="basic-module",
        label="Basic Module",
        input_keys=["user_request"],
        required_keys=["user_request", "answer"],
    )

    rows, label = build_transcript_dataset_rows(
        module_slug="basic-module",
        turns=[("Investigate the bug", "I found the regression.")],
    )

    assert label == "Basic Module"
    assert rows == [{"user_request": "Investigate the bug", "answer": "I found the regression."}]


def test_build_transcript_dataset_rows_applies_known_output_defaults(monkeypatch) -> None:
    monkeypatch.setattr(module_registry, "_MODULE_ENTRYPOINTS", ())
    module_registry._reset_registry()
    _register_spec(
        slug="context-selection",
        label="Context Selection",
        input_keys=["user_request"],
        required_keys=[
            "user_request",
            "assembled_context_summary",
            "selected_memory_handles",
            "selected_evidence_ids",
        ],
    )

    rows, _label = build_transcript_dataset_rows(
        module_slug="context-selection",
        turns=[("Summarize the repo", "Pulled the latest files.")],
    )

    assert rows == [
        {
            "user_request": "Summarize the repo",
            "assembled_context_summary": "Pulled the latest files.",
            "selected_memory_handles": [],
            "selected_evidence_ids": [],
        }
    ]


def test_build_transcript_dataset_rows_supports_plan_code_change_target() -> None:
    module_registry._reset_registry()

    rows, label = build_transcript_dataset_rows(
        module_slug="plan-code-change",
        turns=[("Add a dashboard filter", "Update the filter state and add focused tests.")],
    )

    assert label == "Plan Code Change"
    assert rows == [
        {
            "task": "Add a dashboard filter",
            "repo_context": "",
            "constraints": "",
            "plan_steps": ["Update the filter state and add focused tests."],
            "files_to_touch": [],
            "validation_commands": [],
            "risks": [],
        }
    ]


@pytest.mark.parametrize(
    ("module_slug", "turns", "message"),
    [
        ("missing-module", [("user", "assistant")], "Unknown module slug"),
        ("basic-module", [("user", None), (None, "assistant")], "Transcript has no usable turns"),
    ],
)
def test_build_transcript_dataset_rows_rejects_invalid_exports(monkeypatch, module_slug, turns, message) -> None:
    monkeypatch.setattr(module_registry, "_MODULE_ENTRYPOINTS", ())
    module_registry._reset_registry()
    _register_spec(
        slug="basic-module",
        label="Basic Module",
        input_keys=["user_request"],
        required_keys=["user_request", "answer"],
    )

    with pytest.raises(ValueError, match=message):
        build_transcript_dataset_rows(module_slug=module_slug, turns=turns)
