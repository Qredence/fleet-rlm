"""P41 guarantee: public behavior does not freeze private structure.

Covers VAL-TURN-055, VAL-FILES-038 (structural half), and the test-binding
half of VAL-CROSS-014: every Turn/behavior validation lane must bind to
public commands, HTTP/SSE, Runtime Events, durable repositories, cleanup
receipts, and committed data — never to the deleted orchestration helpers,
process-local ownership state machines, pseudo-resource receipts, retired
compatibility seams, or synthetic workspace-like hosts.

The complete behavioral matrix remaining green at the same SHA is proven by
`make check`; this lane is the regression guard that keeps the suite bound
to public surfaces only. The contract inventory lanes that NAME the deleted
symbols to assert their absence
(``tests/contracts/backend/test_p3{3,7,8}_*.py`` and
``test_p40_explicit_hosts.py``) are the only excluded files.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_ROOT = _REPO_ROOT / "tests"

# Files whose JOB is to name the deleted symbols (inventory and
# absence-guard lanes). Everything else must bind to public surfaces.
_INVENTORY_OR_ABSENCE_LANES = frozenset(
    {
        "contracts/backend/test_p33_guardrails.py",
        "contracts/backend/test_p37_coordinator_ownership.py",
        "contracts/backend/test_p38_native_contraction.py",
        "contracts/backend/test_p40_explicit_hosts.py",
    }
)

_FORBIDDEN_SYMBOL_TOKENS = (
    "PreparationAttempt",
    "RunExecutionDriver",
    "RunLifetimeReceipt",
    "OwnershipComponentReceipt",
    "PreparedResourcesReceipt",
    "RunOwnership",
    "WorkspaceLikeConfig",
    "workspace_like_tools",
    "workspace_like_event_views",
)
_FORBIDDEN_MODULE_TOKENS = (
    "fleet_rlm.chat.preparation_attempt",
    "fleet_rlm.chat.run_execution",
    "fleet_rlm.chat.run_runtime_owner",
)
# Whole-symbol match so a benign substring cannot false-fire: the p33 guard
# lane legitimately references ``initial_tools_registered``. JSON evidence
# field names (e.g. a lane's ``cleanup_receipt`` payload key) are evidence
# metadata, not bindings to the deleted pseudo-resource receipt type, and
# stay unflagged by design.
_FORBIDDEN_REGEXES = (re.compile(r"(?<![A-Za-z0-9_])_tools_registered(?![A-Za-z0-9_])"),)


def _iter_suite_files() -> list[Path]:
    return sorted(
        path for path in _TESTS_ROOT.rglob("*.py") if "__pycache__" not in path.parts and path.parent.name != "freeze"
    )


def test_turn_validation_suite_avoids_deleted_private_structure() -> None:
    files = _iter_suite_files()
    assert len(files) > 250, f"guard scanned only {len(files)} files; the suite root is wrong"

    offenders: list[str] = []
    for path in files:
        relative = path.relative_to(_TESTS_ROOT).as_posix()
        if relative in _INVENTORY_OR_ABSENCE_LANES:
            continue
        text = path.read_text(encoding="utf-8")
        for token in _FORBIDDEN_SYMBOL_TOKENS + _FORBIDDEN_MODULE_TOKENS:
            if token in text:
                line = text.count("\n", 0, text.index(token)) + 1
                offenders.append(f"{relative}:{line}: {token}")
        for pattern in _FORBIDDEN_REGEXES:
            match = pattern.search(text)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{relative}:{line}: {match.group(0)} ({pattern.pattern})")
    assert offenders == [], "acceptance lanes must not bind deleted private structure"


def test_behavioral_freeze_lanes_bind_only_public_artifacts() -> None:
    """The freeze lanes themselves bind public/durable artifacts only."""
    freeze_dir = _TESTS_ROOT / "freeze"
    offenders: list[str] = []
    for path in sorted(freeze_dir.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        for module_token in _FORBIDDEN_MODULE_TOKENS:
            if f"import {module_token}" in text or f"from {module_token}" in text:
                offenders.append(f"{path.name}: imports deleted module {module_token}")
    assert offenders == []
