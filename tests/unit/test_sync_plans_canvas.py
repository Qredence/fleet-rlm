"""Tests for the dossier-backed plans-roadmap canvas generator."""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest


def _load_sync_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "sync_plans_canvas.py"
    spec = importlib.util.spec_from_file_location("sync_plans_canvas", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_dossier(root: Path, dirname: str, content: str) -> None:
    dossier = root / dirname
    dossier.mkdir(parents=True)
    (dossier / "README.md").write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def test_build_phases_reads_dossiers_and_preserves_metadata(tmp_path: Path) -> None:
    sync = _load_sync_module()
    _write_dossier(
        tmp_path,
        "02-direct-rlm-runtime",
        """
        # Direct RLM runtime

        ## Phase 2A — Execution backend seam

        - **Order:** `1`
        - **Status:** `complete`
        - **Track:** `Runtime`
        - **Summary:** Select the runtime behind `stream_turn()`.
        - **Commit:** `ee79f77e,322d3623`

        ### Acceptance criteria

        - [x] Legacy remains the default.

        ### Non-goals

        - Expose backend selection on `ChatRequest`.

        ### Validation

        ```bash
        uv run pytest tests/unit/runtime_services/
        ```
        """,
    )
    _write_dossier(
        tmp_path,
        "06-observability",
        """
        # Observability

        ## Phase 6 — Trace, transcript, performance, and MLflow

        - **Order:** `16`
        - **Status:** `in_progress_uncommitted`
        - **Track:** `Observability`
        - **Summary:** Record both runtime backends through one seam.

        ### Acceptance criteria

        - [x] Default tests do not require MLflow.
        - [ ] Live parity evidence is recorded.

        ## Deferred gaps

        - Live `TurnProgressRelay` parity for direct RLM.
        """,
    )

    phases, deferred = sync.build_phases(tmp_path, required_dossiers=())

    assert [phase.code for phase in phases] == ["2A", "6"]
    assert phases[0].commit == "ee79f77e,322d3623"
    assert phases[0].acceptance == [{"text": "Legacy remains the default.", "done": True}]
    assert phases[0].non_goals == ["Expose backend selection on `ChatRequest`."]
    assert phases[0].validation_cmd == "uv run pytest tests/unit/runtime_services/"
    assert phases[1].status == "in_progress_uncommitted"
    assert deferred == ["Live `TurnProgressRelay` parity for direct RLM."]


def test_build_phases_rejects_duplicate_phase_codes(tmp_path: Path) -> None:
    sync = _load_sync_module()
    content = """
    # Dossier

    ## Phase 4 — Daytona facade

    - **Order:** `14`
    - **Status:** `complete`
    - **Track:** `Daytona`
    - **Summary:** Own Daytona integration details.
    """
    _write_dossier(tmp_path, "04-daytona-a", content)
    _write_dossier(tmp_path, "04-daytona-b", content)

    with pytest.raises(ValueError, match="duplicate phase code: 4"):
        sync.build_phases(tmp_path, required_dossiers=())


def test_build_phases_orders_records_by_metadata_not_directory_name(tmp_path: Path) -> None:
    sync = _load_sync_module()
    for dirname, code, order in (("01-first-directory", "6", 16), ("10-last-directory", "3A", 3)):
        _write_dossier(
            tmp_path,
            dirname,
            f"""
            # Dossier

            ## Phase {code} — Example

            - **Order:** `{order}`
            - **Status:** `planned`
            - **Track:** `Runtime`
            - **Summary:** Example phase.
            """,
        )

    phases, _ = sync.build_phases(tmp_path, required_dossiers=())

    assert [phase.code for phase in phases] == ["3A", "6"]


def test_build_phases_rejects_unknown_status(tmp_path: Path) -> None:
    sync = _load_sync_module()
    _write_dossier(
        tmp_path,
        "09-promotion",
        """
        # Promotion

        ## Phase 9 — Direct RLM promotion

        - **Order:** `19`
        - **Status:** `blocked`
        - **Track:** `Runtime`
        - **Summary:** Promote direct RLM after parity.
        """,
    )

    with pytest.raises(ValueError, match="unsupported status.*blocked"):
        sync.build_phases(tmp_path, required_dossiers=())


def test_build_phases_requires_a_dossier_directory(tmp_path: Path) -> None:
    sync = _load_sync_module()

    with pytest.raises(FileNotFoundError, match="phase dossier directory"):
        sync.build_phases(tmp_path / "missing")


def test_build_phases_reports_missing_canonical_dossier(tmp_path: Path) -> None:
    sync = _load_sync_module()
    tmp_path.mkdir(exist_ok=True)
    for dirname in sync.EXPECTED_DOSSIERS:
        if dirname != "04-daytona-facade":
            dossier = tmp_path / dirname
            dossier.mkdir()
            (dossier / "README.md").write_text("# placeholder\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing phase dossiers: 04-daytona-facade"):
        sync.build_phases(tmp_path)


def test_render_maps_document_statuses_to_canvas_statuses_and_selects_active_phase() -> None:
    sync = _load_sync_module()
    phases = [
        sync.PhaseRecord(11, "3F", "Trusted script execution", "Partial", status="partial"),
        sync.PhaseRecord(
            16,
            "6",
            "Observability",
            "Recorder foundation",
            status="in_progress_uncommitted",
        ),
        sync.PhaseRecord(17, "7", "Typed config", "Audit first", status="planned"),
        sync.PhaseRecord(19, "9", "Direct RLM promotion", "Promotion gate", status="promotion_gated"),
    ]

    rendered = sync.render_phases_ts(phases, [])

    assert 'id: "3f"' in rendered and 'status: "pending"' in rendered
    assert 'id: "6"' in rendered and 'status: "in_progress"' in rendered
    assert 'id: "7"' in rendered and 'status: "pending"' in rendered
    assert 'id: "9"' in rendered and 'status: "pending"' in rendered
    assert 'const NEXT_PHASE_ID = "6";' in rendered


def test_select_next_phase_returns_reopened_phase_when_no_phase_is_active() -> None:
    sync = _load_sync_module()
    phases = [
        sync.PhaseRecord(1, "1", "SSE transport", "Historical work", status="complete"),
        sync.PhaseRecord(2, "2", "Direct RLM", "Reopened contract gap", status="partial"),
        sync.PhaseRecord(3, "3", "Skills", "Later planned work", status="planned"),
    ]

    assert sync.select_next_phase(phases).code == "2"
