"""P42 documentation contract for the versioned Session-state change."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_P41_FREEZE = _REPO_ROOT / "docs/reference/behavior-freeze.md"
_P42_FREEZE = _REPO_ROOT / "docs/reference/p42-session-state-behavior-freeze.md"
_ADR = _REPO_ROOT / "docs/decisions/ADR-session-scoped-rlm-state.md"


def test_p42_documents_version_the_session_state_change_without_rewriting_p41() -> None:
    """The sealed P41 baseline stays historical while the sealed P53 contract owns the target."""
    p41 = _P41_FREEZE.read_text(encoding="utf-8")
    p42 = _P42_FREEZE.read_text(encoding="utf-8")
    adr = _ADR.read_text(encoding="utf-8")

    assert "Native RLM execution per Turn" in p41
    assert "one native `dspy.RLM` per Turn" in p41
    assert "sealed" in p42
    assert "One compatible native Root `dspy.RLM`" in p42
    assert "`REPLHistory`" in p42
    assert "fresh each Turn" in p42
    assert "caller-owned interpreter" in p42
    assert "Complete committed `dspy.History`" in p42
    assert "Native child RLMs" in adr


def test_p42_documents_the_certified_dspy_baseline_and_public_compatibility() -> None:
    """The target is evidence-based and retains the current FastAPI/SSE surface."""
    p42 = _P42_FREEZE.read_text(encoding="utf-8")
    adr = _ADR.read_text(encoding="utf-8")

    assert "`dspy==3.3.1`" in p42
    assert "FastAPI routes, response schemas, OpenAPI" in p42
    assert "Runtime Event vocabulary, identity, ordering" in p42
    assert "SSE chunk vocabulary" in p42
    assert "This decision does not change routes," in adr
    assert "OpenAPI, Runtime Event vocabulary/order, or SSE projection" in adr


def test_p42_documents_are_discoverable_from_the_documentation_indexes() -> None:
    """New active docs must remain reachable from the documentation home."""
    index = (_REPO_ROOT / "docs/index.md").read_text(encoding="utf-8")
    summary = (_REPO_ROOT / "docs/SUMMARY.md").read_text(encoding="utf-8")

    assert "reference/p42-session-state-behavior-freeze.md" in index
    assert "decisions/ADR-session-scoped-rlm-state.md" in index
    assert "reference/p42-session-state-behavior-freeze.md" in summary
    assert "decisions/ADR-session-scoped-rlm-state.md" in summary
