"""P42.5 documentation contract for the module-subtraction ledger."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LEDGER = _REPO_ROOT / "docs/reference/p42-module-subtraction-ledger.md"


def test_p42_ledger_tracks_implementation_status_with_complete_phase_register() -> None:
    """The P42 ledger records completed and in-progress phase destinations."""
    ledger = _LEDGER.read_text(encoding="utf-8")

    assert "implementation ledger;" in ledger
    assert "P48 is in progress" in ledger
    assert "## Explicit P42 non-actions" in ledger
    for phase in range(44, 52):
        assert f"P{phase}" in ledger
    for target in (
        "`sessions/history.py`",
        "`rlm/session_runtime.py`",
        "`rlm/program.py`",
        "`rlm/recursion.py`",
        "`daytona/runtime.py`",
        "`chat/turn_runtime.py`",
        "`workspace/{models,paths,storage,workspace,projects,memory,url}.py`",
        "`config/{settings,loader,policy}.py`",
    ):
        assert target in ledger


def test_p42_ledger_records_required_subtraction_fields_and_index_reachability() -> None:
    """Each planned owner is reviewable with its source, adapters, invariants, and fate."""
    ledger = _LEDGER.read_text(encoding="utf-8")
    index = (_REPO_ROOT / "docs/index.md").read_text(encoding="utf-8")
    summary = (_REPO_ROOT / "docs/SUMMARY.md").read_text(encoding="utf-8")
    reference_index = (_REPO_ROOT / "docs/reference/index.md").read_text(encoding="utf-8")

    for column in (
        "Current path and responsibility",
        "Callers; real production adapters",
        "Invariants",
        "Target module; disposition; planned replacement",
    ):
        assert column in ledger
    for disposition in ("**KEEP**", "**DEEPEN**", "**MERGE**", "**MOVE**", "**DELETE**"):
        assert disposition in ledger
    for row in range(1, 46):
        assert f"L-{row:02d}" in ledger
    assert "P36 ownership and deletion contract" in ledger
    assert "P43 must also prove" in ledger
    assert "reference/p42-module-subtraction-ledger.md" in index
    assert "reference/p42-module-subtraction-ledger.md" in summary
    assert "p42-module-subtraction-ledger.md" in reference_index
