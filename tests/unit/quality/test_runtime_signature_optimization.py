from __future__ import annotations

from types import SimpleNamespace

from fleet_rlm.quality.runtime_signature_optimization import TARGETS, spec_for_runtime_signature


def test_runtime_signature_specs_declare_dataset_contracts() -> None:
    specs = {target.slug: spec_for_runtime_signature(target) for target in TARGETS}

    assert specs["summarize-long-document"].input_keys == ["document", "focus"]
    assert specs["summarize-long-document"].output_keys == ["summary", "key_points", "coverage_pct"]
    assert specs["plan-code-change"].required_dataset_keys == [
        "task",
        "repo_context",
        "constraints",
        "plan_steps",
        "files_to_touch",
        "validation_commands",
        "risks",
    ]
    assert specs["plan-code-change"].optimization_target_kind == "runtime-signature"


def test_runtime_signature_row_converter_builds_dspy_examples() -> None:
    target = next(item for item in TARGETS if item.slug == "plan-code-change")
    spec = spec_for_runtime_signature(target)

    examples = spec.row_converter(
        [
            {
                "task": "Add a filter",
                "repo_context": "React app",
                "constraints": "No new dependencies",
                "plan_steps": ["Update state"],
                "files_to_touch": ["src/app.tsx"],
                "validation_commands": ["pnpm test"],
                "risks": ["Stale UI"],
            }
        ]
    )

    assert len(examples) == 1
    assert dict(examples[0].inputs()) == {
        "task": "Add a filter",
        "repo_context": "React app",
        "constraints": "No new dependencies",
    }
    assert examples[0].plan_steps == ["Update state"]


def test_runtime_signature_metric_scores_text_lists_and_literals() -> None:
    target = next(item for item in TARGETS if item.slug == "clarification-questions")
    spec = spec_for_runtime_signature(target)
    metric = spec.metric_builder()
    gold = SimpleNamespace(
        questions=["Which branch should I use?"],
        blocking_unknowns=["branch"],
        safe_default="Do not mutate files",
        proceed_without_answer=False,
    )
    pred = SimpleNamespace(
        questions=["Which branch should I use?"],
        blocking_unknowns=["branch"],
        safe_default="Do not mutate files",
        proceed_without_answer=False,
    )

    result = metric(gold, pred, trace=[], pred_name="predict", pred_trace=[])

    assert result.score == 1.0
    assert "questions:" in result.feedback
    assert "proceed_without_answer:" in result.feedback
