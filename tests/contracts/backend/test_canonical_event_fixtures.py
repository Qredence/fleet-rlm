"""P24/QRE-168: canonical semantic event fixtures are adapter-consistent.

Every committed entry pins one wire item (live SSE chunk and/or durable
reload part) and its exact canonical JSON. Both adapters must agree with the
pin; coverage of the closed canonical kind set is asserted across the suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from fleet_rlm.api.canonical_wire import canonical_from_live_chunk, canonical_from_reload_part
from fleet_rlm.api.ui_stream import FleetUIMessageChunk
from fleet_rlm.events.canonical import (
    CANONICAL_EVENT_KINDS,
    canonical_event_from_json,
    canonical_event_to_json,
)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "canonical-events"
_CHUNK_ADAPTER = TypeAdapter(FleetUIMessageChunk)


def _entries() -> list[tuple[str, dict[str, object]]]:
    entries: list[tuple[str, dict[str, object]]] = []
    for path in sorted(FIXTURE_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append((path.stem, json.loads(line)))
    return entries


ENTRIES = _entries()


@pytest.mark.parametrize(
    ("scenario", "entry"),
    ENTRIES,
    ids=[f"{name}#{entry['scenario_index']}" for name, entry in ENTRIES],
)
def test_canonical_fixture_entry_is_adapter_consistent(scenario: str, entry: dict[str, object]) -> None:
    canonical = entry["canonical"]
    assert isinstance(canonical, dict)
    # The fixture itself validates against the authoritative schema.
    canonical_event_from_json(dict(canonical))

    live_chunk = entry.get("live_chunk")
    if live_chunk is not None:
        chunk = _CHUNK_ADAPTER.validate_python(live_chunk)
        produced = [canonical_event_to_json(event) for event in canonical_from_live_chunk(chunk)]
        assert produced == [canonical], f"{scenario}: live adapter diverged from the canonical pin"

    reload_part = entry.get("reload_part")
    if reload_part is not None:
        produced = [
            canonical_event_to_json(event)
            for event in canonical_from_reload_part(reload_part, stream_id=str(entry["stream_id"]))
        ]
        assert canonical in produced, (
            f"{scenario}: reload adapter produced {produced}, canonical pin {canonical} not among them"
        )


def test_canonical_fixture_suite_covers_every_kind() -> None:
    covered = {entry["canonical"]["type"] for _name, entry in ENTRIES}  # type: ignore[index]
    # Live-only vs durable-only edges still pin canonical shapes; every closed
    # kind is exercised at least once across the suite.
    assert frozenset(covered) == frozenset(CANONICAL_EVENT_KINDS)


def test_canonical_schema_is_closed_to_unknown_types() -> None:
    with pytest.raises(ValueError, match="unknown canonical event type"):
        canonical_event_from_json({"type": "not-a-kind"})
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        canonical_event_from_json({"type": "text", "stream_id": "x", "surprise": True})
