from __future__ import annotations

from fleet_rlm.runtime.quality.module_registry import (
    ModuleOptimizationSpec,
    _reset_registry,
    register_module,
)
from fleet_rlm.runtime.quality.transcript_exports import (
    build_transcript_dataset_rows,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _dummy_factory() -> None:
    return None


def _dummy_converter(rows: list[dict]) -> list[dict]:
    return rows


def _dummy_metric() -> None:
    return None


def _make_spec(
    slug: str,
    label: str,
    input_keys: list[str],
    required_dataset_keys: list[str],
) -> ModuleOptimizationSpec:
    return ModuleOptimizationSpec(
        module_slug=slug,
        label=label,
        program_spec=f"{slug} -> answer",
        artifact_filename=f"{slug}.json",
        input_keys=input_keys,
        required_dataset_keys=required_dataset_keys,
        module_factory=_dummy_factory,
        row_converter=_dummy_converter,
        metric_builder=_dummy_metric,
    )


# ── Tests ────────────────────────────────────────────────────────────


def test_build_transcript_dataset_rows_basic():
    _reset_registry()
    register_module(
        _make_spec(
            "basic-module",
            "Basic Module",
            input_keys=["user_request"],
            required_dataset_keys=["user_request", "answer"],
        )
    )

    rows, label = build_transcript_dataset_rows(
        module_slug="basic-module",
        turns=[("Investigate the bug", "I found the failing path.")],
    )

    assert label == "Basic Module"
    assert rows == [
        {
            "user_request": "Investigate the bug",
            "answer": "I found the failing path.",
        }
    ]


def test_build_transcript_dataset_rows_list_and_int_defaults():
    """Verify that list/int keys get proper default values via assistant sink."""
    from unittest.mock import patch

    _reset_registry()
    register_module(
        _make_spec(
            "defaulted-module",
            "Defaulted Module",
            input_keys=["user_request"],
            required_dataset_keys=[
                "user_request",
                "assembled_context_summary",
                "working_memory_catalog",
                "context_budget",
            ],
        )
    )

    with patch.dict(
        "fleet_rlm.runtime.quality.transcript_exports._ASSISTANT_SINKS",
        {"defaulted-module": "assembled_context_summary"},
    ):
        rows, _label = build_transcript_dataset_rows(
            module_slug="defaulted-module",
            turns=[
                ("Summarize the repo state", "The latest change touched the router.")
            ],
        )

    row = rows[0]
    assert row["user_request"] == "Summarize the repo state"
    assert row["assembled_context_summary"] == "The latest change touched the router."
    assert row["working_memory_catalog"] == []
    assert row["context_budget"] == 0
