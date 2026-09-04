"""Phase-0 public Runtime Event sequence fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from fleet_rlm.rlm.events import RUNTIME_DETAIL_TYPES

FIXTURES = Path("tests/fixtures/runtime-events")
TERMINAL = {"run.completed", "run.failed", "run.cancelled", "run.timed_out"}


def test_runtime_event_baseline_fixtures_cover_terminal_and_recursive_runs() -> None:
    expected = {"successful-run.json", "failed-run.json", "cancelled-run.json", "recursive-run.json"}
    assert {path.name for path in FIXTURES.glob("*.json")} == expected
    known = {detail.kind for detail in RUNTIME_DETAIL_TYPES}
    for path in FIXTURES.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        events = payload["events"]
        assert payload["schema"] == "fleet.runtime-events/v1"
        assert events[0] == "run.started"
        assert events[-1] in TERMINAL
        assert len(set(events) & TERMINAL) == 1
        assert set(events).issubset(known)


def test_success_fixture_preserves_settlement_order() -> None:
    events = json.loads((FIXTURES / "successful-run.json").read_text(encoding="utf-8"))["events"]
    assert events.index("usage") < events.index("structured.result") < events.index("text.completed")
    assert events[-1] == "run.completed"
