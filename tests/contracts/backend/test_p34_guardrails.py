"""P34 final maintainability-freeze guardrails.

These checks pin ownership seams and bounded vocabularies without asserting
private line-by-line structure. They make the final architecture/documentation
contract fail closed if a canonical owner or P31/P32 regression-proof path is
removed or silently duplicated.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_p34_freeze_guide_is_indexed_and_names_the_final_gate() -> None:
    guide = REPO_ROOT / "docs/how-to-guides/maintainability-freeze.md"
    assert guide.is_file()
    content = guide.read_text(encoding="utf-8")
    assert "# Maintainability freeze" in content
    assert "P26-P33" in content.replace(chr(0x2013), "-")
    assert "make check-security" in content
    assert "FLEET_LIVE=1" in content
    assert "how-to-guides/maintainability-freeze.md" in (REPO_ROOT / "docs/index.md").read_text(encoding="utf-8")
    assert "how-to-guides/maintainability-freeze.md" in (REPO_ROOT / "docs/SUMMARY.md").read_text(encoding="utf-8")


def test_p34_canonical_ownership_seams_remain_available() -> None:
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeLeaseState
    from fleet_rlm.runtime.owned_effect import OwnedEffect
    from fleet_rlm.workspace.memory import MemoryFailureCategory

    assert callable(RunLifecycleService.finish)
    assert callable(TurnCoordinator.open)
    assert callable(OwnedEffect.settle)
    assert {state.name for state in ChildRuntimeLeaseState} == {"OPEN", "CLOSING", "CLOSED", "FAILED"}
    assert {category.value for category in MemoryFailureCategory} == {
        "normalization",
        "provider_unavailable",
        "corrupt_record_set",
        "invariant_violation",
        "search_failure",
        "legacy_migration",
        "unexpected_internal",
    }


def test_p34_regression_proof_paths_remain_committed() -> None:
    paths = (
        "tests/contracts/backend/test_p33_guardrails.py",
        "tools/fleet-tui/src/tui/tests/turn-reducer-invariants.test.ts",
        "tools/fleet-tui/src/tui/tests/reducer-sequence-gen.ts",
        "src/fleet_rlm/workspace/memory.py",
        "src/fleet_rlm/daytona/workspace_agent/runtime.py",
    )
    for relative_path in paths:
        assert (REPO_ROOT / relative_path).is_file(), relative_path
