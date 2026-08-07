#!/usr/bin/env python3
"""Generate the deterministic TUI turn-stream fixture that locks sse.py ↔ sse.ts.

The fixture is a JSONL file (one AI SDK UI chunk per line, `[DONE]` separating
streams) consumed by:

* ``tools/fleet-tui/src/tests/stream-fixture.test.ts`` — validates every chunk
  through the TUI's hand-written ``parseUIChunk`` + ``StreamLifecycle``.
* ``tests/contracts/backend/test_stream_fixture.py`` — regenerates the fixture
  in-memory and fails if the checked-in copy is stale (drift detector), and
  asserts every emitted chunk is fully documented by the OpenAPI hook.

Run ``make stream-sync`` after changing the runtime projector (``api/sse.py``).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FIXTURE = ROOT / "tools" / "fleet-tui" / "src" / "tests" / "fixtures" / "turn-stream.jsonl"

_SESSION_ID = UUID("00000000-0000-0000-0000-000000000000")
_TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)
_RUN_IDS = {
    "happy": UUID("11111111-1111-1111-1111-111111111111"),
    "failure": UUID("22222222-2222-2222-2222-222222222222"),
    "abort": UUID("33333333-3333-3333-3333-333333333333"),
}


def _project(details: tuple[Any, ...], run_id: UUID) -> list[dict[str, Any]]:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import RuntimeEvent

    projector = AISDKUIProjector()
    chunks: list[dict[str, Any]] = []
    for sequence, detail in enumerate(details, start=1):
        event = RuntimeEvent(
            schema_version=1,
            event_id=uuid5(run_id, str(sequence)),
            run_id=run_id,
            session_id=_SESSION_ID,
            sequence=sequence,
            timestamp=_TIMESTAMP,
            detail=detail,
        )
        chunks.extend(projector.project(event))
    return chunks


def _happy_details() -> tuple[Any, ...]:
    from fleet_rlm.rlm.events import (
        ArtifactCreated,
        AttachmentRead,
        RLMCode,
        RLMOutput,
        RLMReasoning,
        RunCompleted,
        RunStarted,
        SkillActivated,
        SkillLoaded,
        Status,
        StepFinished,
        StepStarted,
        StructuredResult,
        TextCompleted,
        TextDelta,
        ToolCompleted,
        ToolFailed,
        ToolStarted,
        Usage,
        WarningEvent,
    )

    return (
        RunStarted("live"),
        Status("execution", "running", "preparing skills"),
        SkillActivated("skill-inspect", "inspect", "1.0.0", "system", ("read",)),
        SkillLoaded("skill-exec", "exec", "1.2.0"),
        StepStarted(1),
        RLMReasoning("Let me think", step=1, is_delta=True, is_final=False),
        RLMReasoning("Let me think through the steps", step=1, is_delta=True, is_final=True),
        RLMCode("print(1)", step=1),
        RLMOutput("1", step=1),
        StepFinished(1),
        StepStarted(2),
        ToolStarted("tool-1", "shell", {"cmd": "ls"}),
        ToolCompleted("tool-1", "shell", {"exit_code": 0}),
        ToolStarted("tool-2", "shell", {"cmd": "missing"}),
        ToolFailed("tool-2", "shell", "command not found"),
        AttachmentRead(
            UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            "input.txt",
            2,
        ),
        ArtifactCreated(
            UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            "markdown",
            "report",
            "text/markdown",
            3,
            "a" * 64,
        ),
        Usage({"iterations": 2, "observed_lm_usage": {}, "duration_ms": 10}),
        StructuredResult("answer", "1", 7),
        WarningEvent("deprecated option", "warn-1"),
        TextDelta("hello"),
        TextCompleted("hello world"),
        StepFinished(2),
        RunCompleted(1, "live", 42),
    )


def _failure_details() -> tuple[Any, ...]:
    from fleet_rlm.rlm.events import RunFailed, RunStarted, TextDelta

    return (
        RunStarted("live"),
        TextDelta("partial answer"),
        RunFailed("execution_failed", "Turn failed"),
    )


def _abort_details() -> tuple[Any, ...]:
    from fleet_rlm.rlm.events import RunCancelled, RunStarted, StepStarted

    return (
        RunStarted("live"),
        StepStarted(1),
        RunCancelled(),
    )


def generate_streams() -> list[list[dict[str, Any]]]:
    """Project all streams to chunks. Deterministic for a given projector."""
    return [
        _project(_happy_details(), _RUN_IDS["happy"]),
        _project(_failure_details(), _RUN_IDS["failure"]),
        _project(_abort_details(), _RUN_IDS["abort"]),
    ]


def _render(streams: list[list[dict[str, Any]]]) -> str:
    from fastapi.encoders import jsonable_encoder

    # Mirror the wire format exactly: FastAPI's SSE path serializes each chunk
    # as json.dumps(jsonable_encoder(chunk)) (fastapi.routing._serialize_sse_item).
    # This keeps the fixture byte-identical to what the TUI receives.
    lines: list[str] = []
    for stream in streams:
        lines.extend(json.dumps(jsonable_encoder(chunk)) for chunk in stream)
        lines.append("[DONE]")
    return "\n".join(lines) + "\n"


def generate(_args: argparse.Namespace) -> int:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(_render(generate_streams()), encoding="utf-8")
    print(f"Wrote TUI turn-stream fixture ({len(list(FIXTURE.open()))} lines)")
    return 0


def check(_args: argparse.Namespace) -> int:
    if not FIXTURE.exists():
        print(f"Missing TUI turn-stream fixture: {FIXTURE}", file=sys.stderr)
        return 1
    expected = _render(generate_streams())
    actual = FIXTURE.read_text(encoding="utf-8")
    if actual != expected:
        print("TUI turn-stream fixture is stale; run `make stream-sync`", file=sys.stderr)
        return 1
    print("TUI turn-stream fixture is current")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate").set_defaults(func=generate)
    commands.add_parser("check").set_defaults(func=check)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
