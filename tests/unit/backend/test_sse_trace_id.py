"""SSE projection of optional operator-facing MLflow trace ids."""

from __future__ import annotations

from uuid import uuid4

from fleet_rlm.api.sse import AISDKUIProjector
from fleet_rlm.rlm.events import EventRecorder, RunCompleted, RunStarted


def test_start_and_finish_include_trace_id_when_present() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    projector = AISDKUIProjector()
    start = projector.project(recorder.record(RunStarted(delivery="live", trace_id="tr-abc")))
    finish = projector.project(recorder.record(RunCompleted(checkpoint_version=1, delivery="live", trace_id="tr-abc")))
    assert start[0]["messageMetadata"]["traceId"] == "tr-abc"
    assert finish[-1]["messageMetadata"]["traceId"] == "tr-abc"


def test_start_omits_trace_id_when_absent() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    payloads = AISDKUIProjector().project(recorder.record(RunStarted(delivery="live")))
    assert "traceId" not in payloads[0]["messageMetadata"]
