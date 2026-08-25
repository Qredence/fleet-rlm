"""Conformance checks for the P36 ownership/deletion contract."""

from __future__ import annotations

import re
from pathlib import Path

DOC_PATH = Path(__file__).resolve().parents[3] / "docs/how-to-guides/p36-ownership-deletion-inventory.md"

REQUIRED_SURFACES = {
    "PreparationAttempt": "P37-ORCH-001",
    "RunExecutionDriver": "P37-ORCH-003",
    "RunOwnership": "P37-ORCH-005",
    "RunLifetimeReceipt": "P37-ORCH-006",
    "_tools_registered": "P38-RLM-013",
    "WorkspaceLikeConfig": "P40-FS-001",
    "workspace_like_tools": "P40-FS-002",
    "workspace_like_event_views": "P40-FS-003",
}

REQUIRED_SECTIONS = (
    "## P37 Turn orchestration inventory",
    "## P38 native DSPy and observability inventory",
    "## P39 recursive child ownership inventory",
    "## P40 Workspace and Project host inventory",
    "## Cross-cutting contract and deletion trace",
)


def _inventory_rows(text: str) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    for line in text.splitlines():
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        cells = tuple(cell.strip() for cell in line.strip().strip("|").split("|"))
        if cells and re.fullmatch(r"`P(?:36|37|38|39|40)-[A-Z0-9-]+`", cells[0]):
            rows.append(cells)
    return rows


def test_p36_inventory_is_reachable_and_has_all_required_sections() -> None:
    assert DOC_PATH.is_file()
    text = DOC_PATH.read_text(encoding="utf-8")
    assert all(section in text for section in REQUIRED_SECTIONS)
    assert "does not delete implementation code" in text
    assert "never private Python" in text
    assert "filenames" in text
    assert "owner" in text
    assert "parity predicate" in text
    assert "Required live evidence" in text


def test_p36_inventory_rows_have_unique_ids_and_complete_evidence_columns() -> None:
    rows = _inventory_rows(DOC_PATH.read_text(encoding="utf-8"))
    assert len(rows) >= 40
    ids = [row[0] for row in rows]
    assert len(ids) == len(set(ids))
    assert all(len(row) in {7, 8} for row in rows)
    for row in rows:
        _id, decision = row[:2]
        downstream = row[-1]
        assert decision
        assert row[2]
        assert row[3]
        assert row[4]
        assert row[5]
        assert downstream
        assert downstream.startswith("`p")


def test_p36_inventory_traces_every_required_deletion_surface() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    rows = _inventory_rows(text)
    row_text = "\n".join("|".join(row) for row in rows)
    for surface, inventory_id in REQUIRED_SURFACES.items():
        assert surface in row_text
        assert inventory_id in row_text
    for row in rows:
        decision = row[1]
        if "DELETE" in decision or decision == "`FORBID`":
            assert row[-1].startswith("`p")


def test_p36_inventory_keeps_callback_shadow_decision_explicit() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "shadow-only" in text
    assert "Shadow evidence alone never authorizes product-path deletion." in text
    assert "P38-RLM-009" in text
    assert "P38-RLM-010" in text
