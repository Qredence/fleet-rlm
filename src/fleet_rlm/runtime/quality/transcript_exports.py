"""Helpers for mapping simple transcripts into structured optimization rows."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from .module_registry import ModuleOptimizationSpec, get_module_spec

_LIST_KEYS = frozenset(
    {
        "working_memory_catalog",
        "recent_sandbox_evidence_catalog",
        "selected_memory_handles",
        "selected_evidence_ids",
        "collected_subquery_outputs",
        "subqueries",
        "missing_evidence",
        "contradictions",
        "repair_steps",
        "repair_subqueries",
    }
)
_INT_KEYS = frozenset({"context_budget", "subquery_budget", "repair_budget"})
_FLOAT_KEYS = frozenset({"confidence"})

_TRANSCRIPT_OUTPUT_DEFAULTS: dict[str, dict[str, Any]] = {
    "reflect-and-revise": {"next_action": "finalize"},
    "context-selection": {
        "selected_memory_handles": [],
        "selected_evidence_ids": [],
    },
    "decomposition": {
        "decomposition_mode": "single_pass",
        "subqueries": [],
    },
    "repair": {"repair_mode": "no_repair"},
    "verification": {"verification_status": "sufficient"},
}

_ASSISTANT_SINKS: dict[str, str] = {
    "reflect-and-revise": "rationale",
    "context-selection": "assembled_context_summary",
    "decomposition": "decomposition_rationale",
    "repair": "repair_rationale",
    "verification": "verified_summary",
}


def _default_value_for_key(key: str) -> Any:
    if key in _LIST_KEYS:
        return []
    if key in _INT_KEYS:
        return 0
    if key in _FLOAT_KEYS:
        return 0.0
    return ""


def _structured_transcript_row(
    *,
    spec: ModuleOptimizationSpec,
    user_message: str,
    assistant_message: str,
) -> dict[str, Any]:
    row = {key: _default_value_for_key(key) for key in spec.required_dataset_keys}
    for key, value in _TRANSCRIPT_OUTPUT_DEFAULTS.get(spec.module_slug, {}).items():
        row[key] = deepcopy(value)

    input_keys = list(spec.input_keys)
    if not input_keys:
        raise ValueError(
            f"Module {spec.module_slug!r} does not declare transcript input keys."
        )
    row[input_keys[0]] = user_message

    assistant_sink = _ASSISTANT_SINKS.get(spec.module_slug)
    if assistant_sink is not None:
        row[assistant_sink] = assistant_message
        return row

    output_keys = [key for key in spec.required_dataset_keys if key not in input_keys]
    if len(output_keys) != 1:
        raise ValueError(
            f"Module {spec.module_slug!r} requires a dedicated transcript export mapping."
        )
    row[output_keys[0]] = assistant_message
    return row


def build_transcript_dataset_rows(
    *,
    module_slug: str,
    turns: Sequence[tuple[str | None, str | None]],
) -> tuple[list[dict[str, Any]], str]:
    """Convert simple user/assistant turns into structured module dataset rows."""

    spec = get_module_spec(module_slug)
    if spec is None:
        raise ValueError(f"Unknown module slug: {module_slug!r}")
    if not spec.required_dataset_keys:
        raise ValueError(
            f"Module {module_slug!r} does not declare any required dataset keys."
        )

    rows: list[dict[str, Any]] = []
    for user_message, assistant_message in turns:
        if not user_message or not assistant_message:
            continue
        rows.append(
            _structured_transcript_row(
                spec=spec,
                user_message=user_message,
                assistant_message=assistant_message,
            )
        )

    if not rows:
        raise ValueError(
            "Transcript has no usable turns "
            "(both user and assistant messages required)."
        )

    return rows, spec.label
