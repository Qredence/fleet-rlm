"""P41 public failure taxonomy golden.

The public failure contract is deliberately assembled from the same transport
adapters used by the API.  This test is a closed snapshot, not a list of
implementation exception names: changing a public category, message, phase,
terminal shape, or cancellation tombstone must update the golden only through
an explicit behavior decision.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from fleet_rlm.api.errors import _STATUS_DEFAULTS
from fleet_rlm.api.routes.turns import _open_failure_frames, _open_failure_message
from fleet_rlm.api.sse import AISDKUIProjector
from fleet_rlm.chat.run_lifecycle import (
    FailedRunReceipt,
    RunIdempotencyMismatchError,
    RunInProgressError,
    RunLifecycleUnavailableError,
    RunNotFoundError,
)
from fleet_rlm.chat.run_preparation import (
    RunPreparationTimeoutError,
    RunPreparationUnavailableError,
)
from fleet_rlm.chat.turn_coordinator import terminal
from fleet_rlm.rlm.events import EventRecorder
from fleet_rlm.skills.errors import InvalidSkillSelectionError

_GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "failure-taxonomy.json"


def _project_terminal(receipt: FailedRunReceipt) -> dict[str, Any]:
    recorder = EventRecorder(receipt.run_id, uuid4())
    event = terminal(recorder, receipt)
    return {
        "event": {
            "kind": event.kind,
            "code": getattr(event.detail, "code", None),
            "message": getattr(event.detail, "message", None),
        },
        "chunks": AISDKUIProjector().project(event),
    }


def _taxonomy() -> dict[str, Any]:
    structural = [
        {
            "category": f"http_{status}",
            "phase": "request",
            "status": status,
            "code": error.code,
            "message": error.message,
        }
        for status, error in sorted(_STATUS_DEFAULTS.items())
    ]
    structural.append(
        {
            "category": "invalid_skill_selection",
            "phase": "request",
            "status": 422,
            "code": "invalid_skill_selection",
            "message": "Invalid Skill selection",
        }
    )

    open_cases: tuple[tuple[str, BaseException], ...] = (
        ("session_not_found", RunNotFoundError()),
        ("turn_conflict", RunInProgressError()),
        ("idempotency_mismatch", RunIdempotencyMismatchError()),
        ("invalid_skill_selection", InvalidSkillSelectionError()),
        ("preparation_timeout", RunPreparationTimeoutError()),
        ("turn_unavailable", RunLifecycleUnavailableError()),
        ("preparation_unavailable", RunPreparationUnavailableError()),
        ("invalid_request", ValueError()),
    )
    opened = []
    for category, error in open_cases:
        message = _open_failure_message(error)
        assert message is not None
        frames = _open_failure_frames(message)
        opened.append(
            {
                "category": category,
                "phase": "preparation",
                "message": message,
                "chunks": [*frames, "[DONE]"],
                "started": False,
            }
        )

    terminal_cases = (
        (
            "runtime_failure",
            FailedRunReceipt(uuid4(), "failed", "execution_failed", "provider detail", False),
        ),
        (
            "invalid_output",
            FailedRunReceipt(uuid4(), "failed", "execution_failed", "Turn output is invalid", False),
        ),
        (
            "output_too_large",
            FailedRunReceipt(uuid4(), "failed", "execution_failed", "Turn output is too large", False),
        ),
        (
            "preparation_failure",
            FailedRunReceipt(uuid4(), "failed", "preparation_failed", "private preparation detail", False),
        ),
        (
            "commit_failure",
            FailedRunReceipt(uuid4(), "failed", "commit_failed", "private commit detail", False),
        ),
        (
            "claim_loss",
            FailedRunReceipt(uuid4(), "failed", "stale_claim", "private claim detail", False),
        ),
        ("timeout", FailedRunReceipt(uuid4(), "timeout", "timeout", "private timeout detail", False)),
        ("cancellation", FailedRunReceipt(uuid4(), "cancelled", "cancelled", "private cancel detail", True)),
    )
    terminals = []
    for category, receipt in terminal_cases:
        projected = _project_terminal(receipt)
        event = projected["event"]
        chunks = projected["chunks"]
        terminals.append(
            {
                "category": category,
                "phase": "execution",
                "event": event,
                "chunks": chunks,
                "durable_parts": ["status", "usage", "text"] if category == "cancellation" else [],
            }
        )

    return {
        "schema": "fleet.public-failure-taxonomy/v1",
        "structural": structural,
        "open_path": opened,
        "terminal": terminals,
        "terminal_shape": {
            "success": {
                "runtime_event": "run.completed",
                "chunks": ["finish", "[DONE]"],
                "finish_reason": "stop",
            },
            "failure": {
                "runtime_events": ["run.failed", "run.timed_out"],
                "chunks": ["error", "finish", "[DONE]"],
                "finish_reason": "error",
            },
            "cancellation": {
                "runtime_event": "run.cancelled",
                "chunks": ["abort", "[DONE]"],
                "finish": False,
            },
        },
    }


def test_public_failure_taxonomy_matches_golden() -> None:
    expected = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert _taxonomy() == expected


def test_public_failure_taxonomy_has_no_private_diagnostics() -> None:
    serialized = json.dumps(_taxonomy(), sort_keys=True)
    forbidden = (
        "provider detail",
        "private preparation detail",
        "private commit detail",
        "private claim detail",
        "private timeout detail",
        "private cancel detail",
    )
    assert all(value not in serialized for value in forbidden)
    assert "traceback" not in serialized.lower()
    assert "sandbox" not in serialized.lower()
