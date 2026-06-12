from __future__ import annotations

from pathlib import Path

import dspy

from fleet_rlm.quality.skill_optimization import spec_for_skill


def test_spec_for_skill_uses_markdown_source_without_overwriting_it(tmp_path: Path) -> None:
    skill_path = tmp_path / "spreadsheet.SKILL.md"
    skill_path.write_text("---\nname: spreadsheet\n---\n# Spreadsheet skill\n", encoding="utf-8")

    spec = spec_for_skill(skill_path=skill_path, trace_bundle_paths=["trace.jsonl"])
    program = spec.module_factory()
    output_path = tmp_path / "artifacts" / "spreadsheet.optimized.md"
    metadata = spec.artifact_writer(program, str(output_path))  # type: ignore[misc]

    assert spec.module_slug == "skill-spreadsheet"
    assert isinstance(program, dspy.Module)
    assert [name for name, _ in program.named_predictors()] == ["skill"]
    assert output_path.read_text(encoding="utf-8").strip() == skill_path.read_text(encoding="utf-8").strip()
    assert skill_path.read_text(encoding="utf-8").startswith("---")
    assert metadata["artifact_type"] == "skill"


def test_skill_row_converter_accepts_trace_export_shape(tmp_path: Path) -> None:
    skill_path = tmp_path / "debug.SKILL.md"
    skill_path.write_text("# Debug skill\n", encoding="utf-8")
    spec = spec_for_skill(skill_path=skill_path)

    examples = spec.row_converter(
        [
            {
                "inputs": {"user_request": "fix it", "context": "trace"},
                "expectations": {"expected_response": "fixed"},
            },
            {"inputs": {"user_request": "skip"}, "expectations": {}},
        ]
    )

    assert len(examples) == 1
    assert examples[0].user_request == "fix it"
    assert examples[0].context == "trace"
    assert examples[0].response == "fixed"


def test_skill_row_converter_accepts_fleet_session_dataset_shape(tmp_path: Path) -> None:
    skill_path = tmp_path / "optimization.SKILL.md"
    skill_path.write_text("# Optimization skill\n", encoding="utf-8")
    spec = spec_for_skill(skill_path=skill_path)

    examples = spec.row_converter(
        [
            {
                "user_request": "inspect the workspace",
                "working_memory_summary": "Found the optimization page and explained GEPA.",
            }
        ]
    )

    assert len(examples) == 1
    assert examples[0].user_request == "inspect the workspace"
    assert examples[0].response == "Found the optimization page and explained GEPA."
