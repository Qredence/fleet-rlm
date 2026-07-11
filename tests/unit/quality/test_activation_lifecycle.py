"""Unit tests for Phase 8 activation resolve and promotion scorecard helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.quality.activation_resolve import (
    active_artifact_from_version_row,
    resolve_workspace_active_artifact,
)
from fleet_rlm.quality.promotion import PromotionEvidence, PromotionGatePolicy, evaluate_promotion_gate
from fleet_rlm.runtime.active_artifacts import load_module_state, resolve_skill_markdown


def test_active_artifact_from_version_row_missing_file_returns_none(tmp_path: Path) -> None:
    row = SimpleNamespace(
        target_kind="module",
        target_id="longcot",
        artifact_path=str(tmp_path / "missing.json"),
        artifact_sha256="abc",
    )
    assert active_artifact_from_version_row(row) is None


def test_active_artifact_from_version_row_loads_skill(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text("optimized skill", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    row = SimpleNamespace(
        target_kind="skill",
        target_id="long-context",
        artifact_path=str(path),
        artifact_sha256=digest,
    )
    artifact = active_artifact_from_version_row(row)
    assert artifact is not None
    assert resolve_skill_markdown("default", artifact) == "optimized skill"


@pytest.mark.asyncio
async def test_resolve_workspace_active_artifact_none_on_missing() -> None:
    class _Persistence:
        async def get_target_activation(self, **_kwargs: object) -> tuple[None, None]:
            return None, None

    resolved = await resolve_workspace_active_artifact(
        _Persistence(),
        tenant_id="t",
        workspace_id="w",
        target_kind="module",
        target_id="x",
    )
    assert resolved is None


def test_promotion_gate_requires_metric_call_budget() -> None:
    result = evaluate_promotion_gate(
        PromotionEvidence(
            baseline_score=0.1,
            candidate_score=0.5,
            test_examples=3,
            hard_gate_failures=0,
            critical_slice_deltas={},
            baseline_failure_rate=0.5,
            candidate_failure_rate=0.0,
            baseline_cost=1.0,
            candidate_cost=1.0,
            baseline_p95_latency_ms=100.0,
            candidate_p95_latency_ms=100.0,
            artifact_round_trip_passed=True,
            metric_call_budget_used=False,
        ),
        PromotionGatePolicy(),
    )
    assert result.ready is False
    assert "promotion_requires_max_metric_calls" in result.failures


def test_promotion_gate_fails_closed_when_cost_latency_unmeasured() -> None:
    result = evaluate_promotion_gate(
        PromotionEvidence(
            baseline_score=0.1,
            candidate_score=0.5,
            test_examples=3,
            hard_gate_failures=0,
            critical_slice_deltas={},
            baseline_failure_rate=0.5,
            candidate_failure_rate=0.0,
            baseline_cost=None,
            candidate_cost=None,
            baseline_p95_latency_ms=None,
            candidate_p95_latency_ms=None,
            artifact_round_trip_passed=True,
            metric_call_budget_used=True,
        ),
        PromotionGatePolicy(),
    )
    assert result.ready is False
    assert "cost_evidence_missing" in result.failures
    assert "latency_evidence_missing" in result.failures


def test_promotion_gate_passes_with_complete_sealed_evidence() -> None:
    result = evaluate_promotion_gate(
        PromotionEvidence(
            baseline_score=0.1,
            candidate_score=0.5,
            test_examples=3,
            hard_gate_failures=0,
            critical_slice_deltas={},
            baseline_failure_rate=0.5,
            candidate_failure_rate=0.0,
            baseline_cost=1.0,
            candidate_cost=1.0,
            baseline_p95_latency_ms=100.0,
            candidate_p95_latency_ms=100.0,
            artifact_round_trip_passed=True,
            metric_call_budget_used=True,
        ),
        PromotionGatePolicy(),
    )
    assert result.ready is True
    assert result.failures == ()


def test_load_module_state_noop_without_activation() -> None:
    module = object()
    assert load_module_state(module, None) is module


def test_skill_runtime_context_applies_activated_markdown() -> None:
    from fleet_rlm.skills.loader import _read_skill_instructions
    from fleet_rlm.skills.schemas import (
        SkillMetadata,
        SkillPermissionMode,
        SkillRuntimeContext,
        SkillScope,
        SkillTrustLevel,
    )

    metadata = SkillMetadata(
        name="demo-skill",
        description="d",
        scope=SkillScope.SCAFFOLD,
        trust_level=SkillTrustLevel.TRUSTED,
        permission_mode=SkillPermissionMode.READ_ONLY,
        source="scaffold:demo",
        directory_style=True,
    )
    ctx = SkillRuntimeContext(activated_skill_markdown={"demo-skill": "# Activated\n"})
    assert _read_skill_instructions(metadata, context=ctx) == "# Activated\n"
