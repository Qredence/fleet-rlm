from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from fleet_rlm.api.routers.optimization import run_details
from fleet_rlm.api.routers.optimization import runs as runs_module
from fleet_rlm.api.routers.optimization.run_details import (
    build_optimization_run_detail,
    create_or_load_promotion_draft,
)
from fleet_rlm.api.schemas.optimization import OptimizationRunResponse
from fleet_rlm.db.enums import OptimizationRunStatus


def _run(tmp_path: Path, *, manifest_path: Path | None, output_path: Path | None) -> OptimizationRunResponse:
    return OptimizationRunResponse(
        id="277",
        status="completed",
        module_slug="skill-optimization",
        program_spec="skill:optimization",
        optimizer="gepa",
        auto="light",
        train_ratio=0.8,
        dataset_path="datasets/example.jsonl",
        distilled_trace_bundle_path=str(tmp_path / "trace.distilled.jsonl"),
        train_examples=1,
        validation_examples=0,
        validation_score=None,
        output_path=str(output_path) if output_path else None,
        manifest_path=str(manifest_path) if manifest_path else None,
        phase="completed",
        started_at="2026-06-11T18:12:19",
        completed_at="2026-06-11T18:14:50",
    )


def test_run_details_parse_manifest_prompt_diff_and_distilled_trace(tmp_path: Path) -> None:
    trace_bundle = tmp_path / "trace.distilled.jsonl"
    trace_bundle.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "trace_bundle_summary",
                        "trace_count": 1,
                        "failure_clusters": [{"category": "bad_tool_use", "count": 1}],
                    }
                ),
                json.dumps(
                    {
                        "kind": "trace_evidence",
                        "trace_id": "tr-1",
                        "session_id": "default:anonymous:session-1",
                        "client_request_id": "chat-1",
                        "span_count": 94,
                        "failure_categories": ["bad_tool_use", "loop_inefficiency"],
                        "prompt_change_recommendations": ["Clarify when to stop."],
                        "spans": [{"raw": "must not leak"}],
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "optimization.optimized.md"
    output_path.write_text("after prompt", encoding="utf-8")
    manifest_path = tmp_path / "optimization.optimized.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "review_bundle": {
                    "holdout": {
                        "baseline_score": 0.2,
                        "optimized_score": 0.4,
                        "score_delta": 0.2,
                        "split_reference": {
                            "train_examples": 2,
                            "validation_examples": 1,
                            "train_ratio": 0.8,
                            "strategy": "single-example",
                        },
                    },
                    "prompt_snapshots": {
                        "matched_predictors": [
                            {
                                "predictor_name": "skill",
                                "before_prompt": "before prompt",
                                "after_prompt": "after prompt",
                            }
                        ]
                    },
                    "trace_bundle_paths": [str(trace_bundle)],
                    "gepa_evidence": {
                        "available": True,
                        "path": str(tmp_path / "optimization.optimized.gepa-evidence.json"),
                        "log_dir": str(tmp_path / "optimization.optimized.gepa"),
                        "candidate_count": 2,
                        "best_candidate_id": "candidate-1",
                    },
                    "feedback_summary": "useful feedback",
                    "insights": {
                        "candidate_decisions": [
                            {
                                "candidate_id": "selected",
                                "status": "selected",
                                "summary": "Selected changed prompt",
                                "artifact_path": str(tmp_path / "optimization.optimized.gepa-evidence.json"),
                                "missing_candidate_artifact": False,
                            }
                        ]
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    detail = build_optimization_run_detail(
        run=_run(tmp_path, manifest_path=manifest_path, output_path=output_path),
        prompt_snapshots=[],
    )

    assert detail.manifest_available is True
    assert detail.score_summary.baseline_score == 0.2
    assert detail.score_summary.optimized_score == 0.4
    assert detail.prompt_diffs[0].changed is True
    assert detail.trace_evidence[1].trace_id == "tr-1"
    assert detail.trace_evidence[1].prompt_change_recommendations == ["Clarify when to stop."]
    assert "spans" not in detail.trace_evidence[1].model_dump()
    assert detail.candidate_decisions[0].summary == "Selected changed prompt"
    assert detail.candidate_decisions[0].artifact_path == str(tmp_path / "optimization.optimized.gepa-evidence.json")
    assert any(ref.kind == "gepa_evidence" for ref in detail.artifact_refs)
    assert any(ref.kind == "gepa_log_dir" for ref in detail.artifact_refs)
    assert detail.optimized_artifact_text == "after prompt"


def test_run_details_missing_manifest_returns_partial_report(tmp_path: Path) -> None:
    detail = build_optimization_run_detail(
        run=_run(tmp_path, manifest_path=tmp_path / "missing.manifest.json", output_path=None),
        prompt_snapshots=[
            SimpleNamespace(predictor_name="skill", prompt_type="before", prompt_text="same"),
            SimpleNamespace(predictor_name="skill", prompt_type="after", prompt_text="same"),
        ],
    )

    assert detail.manifest_available is False
    assert detail.insights.selected_outcome == "unchanged"
    assert detail.candidate_decisions[1].missing_candidate_artifact is True


def test_run_details_marks_trainset_fallback_as_not_promotion_ready(tmp_path: Path) -> None:
    manifest_path = tmp_path / "no-holdout.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "review_bundle": {
                    "holdout": {
                        "external_validation_available": False,
                        "gepa_internal_valset": "trainset_fallback",
                        "promotion_ready": False,
                        "split_reference": {
                            "train_examples": 1,
                            "validation_examples": 0,
                            "train_ratio": 0.8,
                            "strategy": "single-example",
                        },
                    },
                    "prompt_snapshots": {
                        "matched_predictors": [
                            {
                                "predictor_name": "skill",
                                "before_prompt": "same",
                                "after_prompt": "same",
                            }
                        ]
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    detail = build_optimization_run_detail(
        run=_run(tmp_path, manifest_path=manifest_path, output_path=None),
        prompt_snapshots=[],
    )

    assert "holdout validation examples" in detail.insights.next_step
    assert detail.score_summary.validation_examples == 0


def test_promotion_draft_paths_are_isolated_by_tenant(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(run_details, "OPTIMIZATION_DATA_ROOT", tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    run = _run(tmp_path, manifest_path=manifest_path, output_path=None)

    draft_a = create_or_load_promotion_draft(
        run,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )
    draft_b = create_or_load_promotion_draft(
        run,
        tenant_id="tenant-b",
        workspace_id="workspace-a",
    )

    assert draft_a.draft_path != draft_b.draft_path
    assert "tenant-a" in draft_a.draft_path
    assert "tenant-b" in draft_b.draft_path


def test_promotion_draft_is_a_non_mutating_artifact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(run_details, "OPTIMIZATION_DATA_ROOT", tmp_path)
    source_skill = tmp_path / "source" / "SKILL.md"
    source_skill.parent.mkdir(parents=True)
    source_skill.write_text("original skill", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    run = _run(tmp_path, manifest_path=manifest_path, output_path=source_skill)

    draft = create_or_load_promotion_draft(
        run,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )
    loaded = create_or_load_promotion_draft(
        run,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )

    assert draft.status == "draft"
    assert Path(draft.draft_path).is_file()
    assert loaded.draft_path == draft.draft_path
    assert source_skill.read_text(encoding="utf-8") == "original skill"


@pytest.mark.asyncio
async def test_create_run_promotion_draft_rejects_non_completed_run(monkeypatch) -> None:
    run_uuid = uuid.uuid4()

    async def fake_resolve_persisted_identity(**kwargs):
        _ = kwargs
        return SimpleNamespace(
            tenant_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
        )

    monkeypatch.setattr(runs_module, "_resolve_persisted_identity", fake_resolve_persisted_identity)
    persistence = SimpleNamespace(
        get_optimization_run=AsyncMock(
            return_value=SimpleNamespace(
                id=run_uuid,
                status=OptimizationRunStatus.RUNNING,
            )
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await runs_module.create_run_promotion_draft(
            config_deps=SimpleNamespace(),
            identity=SimpleNamespace(),
            persistence=persistence,
            run_id=str(run_uuid),
        )

    assert exc_info.value.status_code == 409
