"""Contracts for Fleet's JSON-in-SSE boundary and native consumer framing."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.sse import EventSourceResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError

from fleet_rlm.api.stream_contracts import FleetEnvelope, frame_envelope
from fleet_rlm.tui.commands import SessionState
from fleet_rlm.tui.render import TurnTracker
from fleet_rlm.tui.stream import _turn_events


def wire(event: str, **payload: object) -> dict[str, object]:
    """Build the exact nested-and-flattened shape produced by Envelope.wrap."""
    return {
        "version": 1,
        "event": event,
        "session_id": "session-1",
        "turn_id": "turn-1",
        "agent_id": None,
        "parent_agent_id": None,
        "sandbox_id": None,
        "seq": 9,
        "timestamp": 1_798_000_000.25,
        "payload": payload,
        **payload,
    }


@pytest.mark.parametrize("event", ["turn_complete", "error", "turn_cancelled"])
def test_terminal_contract_accepts_integer_deadline_provenance(event: str) -> None:
    framed = frame_envelope(wire(event, effective_seconds=900))
    assert framed.data["payload"]["effective_seconds"] == 900


def test_progress_mirrors_nan_values() -> None:
    # Distinct NaN objects exercise the JSON mirror comparison, not identity.
    event = wire("progress", nested={"value": float("nan")})
    event["nested"] = {"value": float("nan")}
    assert FleetEnvelope.model_validate(event).event == "progress"


def test_malformed_completion_is_rejected_by_its_terminal_contract() -> None:
    malformed = wire("turn_complete", turn_id="turn-1", answer={"unexpected": "object"})

    with pytest.raises(ValidationError, match="answer"):
        FleetEnvelope.model_validate(malformed)


def test_error_done_and_progress_contracts_are_distinct() -> None:
    malformed_error = wire("error", request_id={"unexpected": "object"})
    with pytest.raises(ValidationError, match="request_id"):
        FleetEnvelope.model_validate(malformed_error)
    assert FleetEnvelope.model_validate(wire("progress", request_id={"extension": True})).event == "progress"

    with pytest.raises(ValidationError):
        FleetEnvelope.model_validate(wire("done", reason="not part of the done marker"))

    dynamic = FleetEnvelope.model_validate(
        wire("progress", phase="checkpoint", scope="scan", nested={"extension": [None, 4]})
    )
    assert dynamic.payload["nested"] == {"extension": [None, 4]}


def test_terminal_history_preserves_null_absence_and_unknown_values() -> None:
    legacy = wire("turn_complete", answer=None, route="rlm", historical_extension={"keep": [1, None]})
    validated = FleetEnvelope.model_validate(legacy)
    dumped = validated.model_dump(mode="json", exclude_unset=True)

    assert dumped["payload"] == legacy["payload"]
    assert dumped["answer"] is None
    assert "artifacts" not in dumped
    assert dumped["historical_extension"] == {"keep": [1, None]}


def test_native_sse_consumer_receives_existing_id_and_json_data_framing() -> None:
    app = FastAPI()

    @app.get("/events", response_class=EventSourceResponse)
    async def events():
        yield frame_envelope(wire("progress", phase="checkpoint", checkpoint=3))

    with TestClient(app) as client:
        response = client.get("/events")

    assert response.headers["content-type"].startswith("text/event-stream")
    lines = response.text.splitlines()
    assert "id: 9" in lines
    assert not any(line.startswith("event:") for line in lines)
    data_line = next(line for line in lines if line.startswith("data: "))
    assert lines[-1] == ""
    data = json.loads(data_line[6:])
    assert data["seq"] == 9
    assert data["event"] == "progress"
    assert data["payload"] == {"phase": "checkpoint", "checkpoint": 3}


def test_native_terminal_frame_is_consumed_by_the_tui_parser() -> None:
    app = FastAPI()

    @app.get("/events", response_class=EventSourceResponse)
    async def events():
        yield frame_envelope(
            wire(
                "turn_complete",
                turn_id="turn-1",
                answer="Done",
                route="rlm",
                artifacts=[],
                outcome="completed",
            )
        )

    with TestClient(app) as client:
        native_response = client.get("/events")

    response = httpx.Response(200, content=native_response.content)
    state = SessionState()
    tracker = TurnTracker()
    consumed = list(_turn_events(response, state, tracker))

    assert len(consumed) == 1
    assert consumed[0]["payload"] == {
        "turn_id": "turn-1",
        "answer": "Done",
        "route": "rlm",
        "artifacts": [],
        "outcome": "completed",
    }
    assert state.last_turn_id == "turn-1"
    assert tracker.terminal_event["answer"] == "Done"


def test_framing_validation_error_does_not_expose_rejected_payload() -> None:
    rejected = wire("turn_complete", answer={"private": "not-for-logs"})

    with pytest.raises(RuntimeError, match="Turn event contract validation failed") as error:
        frame_envelope(rejected)

    assert "not-for-logs" not in str(error.value)
