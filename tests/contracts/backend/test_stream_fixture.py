"""K-003: the TUI turn-stream fixture stays in sync with the runtime projector.

The fixture at ``tools/fleet-tui/src/tests/fixtures/turn-stream.jsonl`` is the
single golden stream consumed by the TUI's cross-validation test
(``tools/fleet-tui/src/tests/stream-fixture.test.ts``). These tests keep it
deterministically in sync with ``api/sse.py`` and with the OpenAPI chunk
contract in ``api/openapi.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tools" / "fleet-tui" / "src" / "tests" / "fixtures" / "turn-stream.jsonl"


def _generate_streams():
    from scripts.generate_stream_fixture import _render, generate_streams

    return generate_streams(), _render


def _chunks() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line != "[DONE]"]


def test_turn_stream_fixture_is_current() -> None:
    streams, render = _generate_streams()
    assert FIXTURE.read_text(encoding="utf-8") == render(streams), (
        "TUI turn-stream fixture is stale; run `make stream-sync`"
    )


def test_emitted_chunks_are_fully_documented_by_openapi() -> None:
    from fleet_rlm.api.sse import FLEET_UI_CHUNK_TYPES
    from fleet_rlm.composition.testing import create_testing_app

    variants = create_testing_app().openapi()["components"]["schemas"]["FleetUIMessageChunk"]["oneOf"]
    by_type = {variant["properties"]["type"]["const"]: variant for variant in variants}

    chunks = _chunks()
    assert chunks, "fixture must contain at least one chunk"
    for chunk in chunks:
        assert chunk["type"] in FLEET_UI_CHUNK_TYPES, f"undocumented chunk type {chunk['type']!r}"
        variant = by_type[chunk["type"]]
        properties = variant["properties"]
        # Every emitted field must be declared by the OpenAPI hook, and every
        # field the hook marks required must actually be emitted.
        assert set(chunk).issubset(properties), (
            f"{chunk['type']} emits fields not documented: {set(chunk) - set(properties)}"
        )
        for field in variant.get("required", []):
            assert field in chunk, f"{chunk['type']} is missing required field {field!r}"
        if "data" not in chunk:
            continue
        data_schema = properties["data"]
        assert set(chunk["data"]).issubset(data_schema.get("properties", {})), (
            f"{chunk['type']} emits data fields not documented: "
            f"{set(chunk['data']) - set(data_schema.get('properties', {}))}"
        )
        for field in data_schema.get("required", []):
            assert field in chunk["data"], f"{chunk['type']} data is missing required field {field!r}"
