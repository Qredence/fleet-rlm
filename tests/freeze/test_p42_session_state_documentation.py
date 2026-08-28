"""P42 documentation contract for the versioned Session-state change."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_P41_FREEZE = _REPO_ROOT / "docs/reference/behavior-freeze.md"
_P42_FREEZE = _REPO_ROOT / "docs/reference/p42-session-state-behavior-freeze.md"
_LEDGER = _REPO_ROOT / "docs/reference/p42-module-subtraction-ledger.md"
_CODEBASE_MAP = _REPO_ROOT / "docs/reference/codebase-map.md"
_CONTEXT = _REPO_ROOT / "src/fleet_rlm/CONTEXT.md"
_ADR = _REPO_ROOT / "docs/decisions/ADR-session-scoped-rlm-state.md"


def test_p42_documents_version_the_session_state_change_without_rewriting_p41() -> None:
    """The sealed P41 baseline stays historical while P53 certifies the target."""
    p41 = _P41_FREEZE.read_text(encoding="utf-8")
    p42 = _P42_FREEZE.read_text(encoding="utf-8")
    adr = _ADR.read_text(encoding="utf-8")

    assert "Native RLM execution per Turn" in p41
    assert "one native `dspy.RLM` per Turn" in p41
    assert "**Status:** approved behavior contract" in p42
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


def test_p53_closeout_docs_pin_current_owners_and_unsealed_evidence() -> None:
    """Maps describe the current implementation without claiming unrun evidence."""
    ledger = _LEDGER.read_text(encoding="utf-8")
    codebase_map = _CODEBASE_MAP.read_text(encoding="utf-8")
    context = _CONTEXT.read_text(encoding="utf-8")

    assert "**Status:** active P53 close-out ledger." in ledger
    assert "P42\N{EN DASH}P52 implementation is present" in ledger
    assert "P53 certification closes when the P35-E gate verifies the current clean candidate" in ledger
    assert "No certification claim is made for an" in ledger
    assert "TBD" not in ledger
    assert "Planned" not in ledger
    assert "rlm/{factory,runner,context,worker_execution}.py" not in ledger
    assert "files/{workspace_access,workspace_models" not in ledger
    assert "daytona/{broker_source,http_broker,dspy_sync_bridge" not in ledger
    assert "`rlm/{program,result,_dspy_compat,runtime,session_runtime,events,recursion}.py`" in ledger
    assert "canonical-event shadow layer was an X1 wire-or-delete candidate and is deleted" in ledger
    assert "events/canonical.py" not in ledger

    assert "routing evaluation" not in next(line for line in codebase_map.splitlines() if line.startswith("| `rlm/`"))
    assert "evaluation-only routing lives in `optimization/routing.py`" in codebase_map
    assert "wiring only, no Turn behavior" in codebase_map
    assert "cleanup/error/usage policy" in codebase_map

    assert "certified DSPy 3.3.1" in context
    assert "Warm multi-run **Code-Interpreter Context**" not in context
    assert "Cross-process identity of a resident **Code-Interpreter Context**" in context


def test_p42_documents_are_discoverable_from_the_documentation_indexes() -> None:
    """New active docs must remain reachable from the documentation home."""
    index = (_REPO_ROOT / "docs/index.md").read_text(encoding="utf-8")
    summary = (_REPO_ROOT / "docs/SUMMARY.md").read_text(encoding="utf-8")

    assert "reference/p42-session-state-behavior-freeze.md" in index
    assert "decisions/ADR-session-scoped-rlm-state.md" in index
    assert "reference/p42-session-state-behavior-freeze.md" in summary
    assert "decisions/ADR-session-scoped-rlm-state.md" in summary
