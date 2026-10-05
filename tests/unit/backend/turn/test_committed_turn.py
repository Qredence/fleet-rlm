"""Committed Turn domain contract."""

from __future__ import annotations

from uuid import uuid4

import pytest


def test_committed_turn_codec_round_trips_the_closed_v1_aggregate() -> None:
    from fleet_rlm.sessions.committed_turn import (
        ArtifactPart,
        CommittedTurn,
        CommittedTurnCodec,
        ReasoningPart,
        StructuredResultPart,
        TextPart,
        UsagePart,
    )

    artifact_id = uuid4()
    committed = CommittedTurn(
        schema_version=1,
        parts=(
            ReasoningPart(text="Inspected the bounded corpus", step=1),
            ArtifactPart(
                artifact_id=artifact_id,
                kind="json",
                title="result",
                media_type="application/json",
                byte_size=12,
                checksum_sha256="a" * 64,
            ),
            UsagePart(
                value={
                    "iterations": 1,
                    "observed_lm_usage": {"root": {"prompt_tokens": 3}},
                    "duration_ms": 8,
                }
            ),
            StructuredResultPart(schema_id="analysis", schema_version="1", value={"total": 42}),
            TextPart(text="42"),
        ),
    )

    encoded = CommittedTurnCodec.encode(committed)
    decoded = CommittedTurnCodec.decode(encoded)

    assert decoded == committed
    assert decoded.text == "42"
    assert decoded.structured_result == {"total": 42}
    assert encoded == {
        "schema_version": 1,
        "parts": [
            {"type": "reasoning", "text": "Inspected the bounded corpus", "step": 1},
            {
                "type": "artifact",
                "artifact_id": str(artifact_id),
                "kind": "json",
                "title": "result",
                "media_type": "application/json",
                "byte_size": 12,
                "checksum_sha256": "a" * 64,
            },
            {
                "type": "usage",
                "value": {
                    "iterations": 1,
                    "observed_lm_usage": {"root": {"prompt_tokens": 3}},
                    "duration_ms": 8,
                },
            },
            {
                "type": "structured_result",
                "schema_id": "analysis",
                "schema_version": "1",
                "value": {"total": 42},
            },
            {"type": "text", "text": "42"},
        ],
    }


def test_committed_turn_codec_handles_every_execution_part_variant() -> None:
    from fleet_rlm.sessions.committed_turn import (
        AttachmentPart,
        CodePart,
        CommittedTurn,
        CommittedTurnCodec,
        OutputPart,
        SkillPart,
        StatusPart,
        StepPart,
        TextPart,
        ToolCallPart,
        UsagePart,
        WarningPart,
    )

    committed = CommittedTurn(
        schema_version=1,
        parts=(
            StepPart(state="started", step=1),
            CodePart(code="print(42)", step=1),
            OutputPart(output="42", step=1),
            ToolCallPart(
                tool_call_id="call-1",
                tool_name="read_attachment",
                state="completed",
                input={"attachment_id": "bounded"},
                output={"ok": True},
            ),
            ToolCallPart(
                tool_call_id="call-2",
                tool_name="lookup",
                state="failed",
                input={},
                error="Tool failed",
            ),
            SkillPart(
                skill_id="skill-1",
                name="analysis",
                phase="activated",
                version="1",
                trust="trusted",
                affordances=("search",),
            ),
            AttachmentPart(attachment_id=uuid4(), phase="read", filename="data.txt", byte_size=2),
            WarningPart(message="Some details were omitted", code="detail_overflow"),
            StatusPart(phase="cancelled", status="cancelled"),
            StepPart(state="finished", step=1, duration_ms=8),
            UsagePart(value={"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}),
            TextPart(text="42"),
        ),
    )

    assert CommittedTurnCodec.decode(CommittedTurnCodec.encode(committed)) == committed


@pytest.mark.parametrize("invented_key", ["llm_calls"])
def test_usage_part_rejects_invented_call_counts(invented_key: str) -> None:
    from fleet_rlm.sessions.committed_turn import CommittedTurnValidationError, UsagePart

    with pytest.raises(CommittedTurnValidationError):
        UsagePart(
            value={
                "iterations": 1,
                "observed_lm_usage": {},
                "duration_ms": 1,
                invented_key: 1,
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 2, "parts": []},
        {"schema_version": 1, "parts": [{"type": "unknown"}]},
        {"schema_version": 1, "parts": [{"type": "text", "text": "missing usage"}]},
    ],
)
def test_committed_turn_codec_rejects_unknown_or_noncanonical_values(payload: object) -> None:
    from fleet_rlm.sessions.committed_turn import CommittedTurnCodec, CommittedTurnValidationError

    with pytest.raises(CommittedTurnValidationError):
        CommittedTurnCodec.decode(payload)


@pytest.mark.parametrize("text", ["", "   \n"])
def test_committed_turn_codec_rejects_blank_display_text(text: str) -> None:
    from fleet_rlm.sessions.committed_turn import CommittedTurnCodec, CommittedTurnValidationError

    with pytest.raises(CommittedTurnValidationError, match="final text"):
        CommittedTurnCodec.decode(
            {
                "schema_version": 1,
                "parts": [
                    {
                        "type": "usage",
                        "value": {"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0},
                    },
                    {"type": "text", "text": text},
                ],
            }
        )


_EMPTY_USAGE = {"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}


def _tombstone_wrap(part: object) -> object:
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart

    return CommittedTurn(
        schema_version=1,
        parts=(part, UsagePart(value=_EMPTY_USAGE), TextPart(text="Turn cancelled")),
    )


def test_status_part_codec_round_trips_the_bounded_cancelled_marker() -> None:
    from fleet_rlm.sessions.committed_turn import CommittedTurnCodec, StatusPart

    part = StatusPart(phase="cancelled", status="cancelled")

    encoded = CommittedTurnCodec.encode(_tombstone_wrap(part))
    assert encoded["parts"][0] == {"type": "status", "phase": "cancelled", "status": "cancelled", "message": None}
    assert CommittedTurnCodec.decode(encoded) == _tombstone_wrap(part)


@pytest.mark.parametrize(
    "part_kwargs",
    [{"phase": "", "status": "cancelled"}],
)
def test_status_part_rejects_blank_phase_or_status(part_kwargs: dict[str, str]) -> None:
    from fleet_rlm.sessions.committed_turn import CommittedTurnValidationError, StatusPart

    with pytest.raises(CommittedTurnValidationError):
        StatusPart(**part_kwargs)


def test_status_part_follows_the_execution_band_order_rule() -> None:
    from fleet_rlm.sessions.committed_turn import (
        CommittedTurn,
        CommittedTurnValidationError,
        StatusPart,
        TextPart,
        UsagePart,
    )

    with pytest.raises(CommittedTurnValidationError):
        CommittedTurn(
            schema_version=1,
            parts=(
                StatusPart(phase="cancelled", status="cancelled"),
                TextPart(text="Turn cancelled"),
                UsagePart(value=_EMPTY_USAGE),
            ),
        )
    in_band = _tombstone_wrap(StatusPart(phase="cancelled", status="cancelled"))
    assert [type(part).__name__ for part in in_band.parts] == ["StatusPart", "UsagePart", "TextPart"]


def test_projector_replays_terminal_child_progress_with_the_same_payload() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import EventRecorder
    from fleet_rlm.sessions.committed_turn import (
        ChildProgressPart,
        CommittedTurn,
        CommittedTurnEventProjector,
        TextPart,
        UsagePart,
    )

    turn = CommittedTurn(
        schema_version=1,
        parts=(
            ChildProgressPart(
                "root:call-3",
                "Check references",
                "failed",
                930,
                "No reliable source",
                "complete",
                "run-17",
                result_file_count=1,
            ),
            ChildProgressPart(
                "root:call-4",
                "Inspect large input",
                "not_started",
                0,
                "Child admission budget exhausted",
                "not_required",
                "run-17",
            ),
            UsagePart({"iterations": 1, "observed_lm_usage": {}, "duration_ms": 930}),
            TextPart("Done"),
        ),
    )
    events = CommittedTurnEventProjector().project(turn, EventRecorder(uuid4(), uuid4()), mode="replay")
    child = next(event for event in events if event.kind == "child.progress")
    assert AISDKUIProjector().project(child) == [
        {
            "type": "data-child-progress",
            "id": "root:call-3",
            "data": {
                "child_id": "root:call-3",
                "task_label": "Check references",
                "state": "failed",
                "elapsed_ms": 930,
                "outcome": "No reliable source",
                "evidence": [],
                "gaps": [],
                "result_file_count": 1,
                "cleanup_state": "complete",
                "parent_run_id": "run-17",
            },
        }
    ]
    refused = next(event for event in events if getattr(event.detail, "child_id", None) == "root:call-4")
    assert AISDKUIProjector().project(refused) == [
        {
            "type": "data-child-progress",
            "id": "root:call-4",
            "data": {
                "child_id": "root:call-4",
                "task_label": "Inspect large input",
                "state": "not_started",
                "elapsed_ms": 0,
                "outcome": "Child admission budget exhausted",
                "evidence": [],
                "gaps": [],
                "result_file_count": 0,
                "cleanup_state": "not_required",
                "parent_run_id": "run-17",
            },
        }
    ]


def test_projector_replays_every_semantic_part_in_order() -> None:
    from fleet_rlm.rlm.events import EventRecorder
    from fleet_rlm.sessions.committed_turn import (
        ArtifactPart,
        AttachmentPart,
        CommittedTurn,
        CommittedTurnEventProjector,
        ReasoningPart,
        StepPart,
        StructuredResultPart,
        TextPart,
        ToolCallPart,
        UsagePart,
    )

    turn = CommittedTurn(
        schema_version=1,
        parts=(
            StepPart(state="started", step=1),
            ReasoningPart(text="think", step=1),
            ToolCallPart("c1", "lookup", "completed", {"q": "x"}, {"ok": True}),
            AttachmentPart(uuid4(), "read", "a.txt", 3),
            ArtifactPart(uuid4(), "json", None, "application/json", 2, "a" * 64),
            UsagePart({"iterations": 1, "observed_lm_usage": {}, "duration_ms": 3}),
            StructuredResultPart("answer", "1", {"value": 42}),
            TextPart("42"),
        ),
    )
    recorder = EventRecorder(uuid4(), uuid4())

    events = CommittedTurnEventProjector().project(turn, recorder, mode="replay")

    assert [event.kind for event in events] == [
        "step.started",
        "rlm.reasoning",
        "tool.started",
        "tool.completed",
        "attachment.read",
        "artifact.created",
        "usage",
        "structured.result",
        "text.delta",
        "text.completed",
    ]
    assert [event.sequence for event in events] == list(range(1, 11))


def test_live_suffix_excludes_execution_parts() -> None:
    from fleet_rlm.rlm.events import EventRecorder
    from fleet_rlm.sessions.committed_turn import (
        CommittedTurn,
        CommittedTurnEventProjector,
        ReasoningPart,
        TextPart,
        UsagePart,
    )

    turn = CommittedTurn(
        schema_version=1,
        parts=(
            ReasoningPart("think"),
            UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}),
            TextPart("done"),
        ),
    )

    events = CommittedTurnEventProjector().project(
        turn,
        EventRecorder(uuid4(), uuid4()),
        mode="live_suffix",
    )

    assert [event.kind for event in events] == ["usage", "text.delta", "text.completed"]


def test_projector_maps_status_parts_back_to_transient_status_events() -> None:
    from fleet_rlm.rlm.events import EventRecorder, Status
    from fleet_rlm.rlm.result import empty_rlm_usage
    from fleet_rlm.sessions.committed_turn import (
        CommittedTurnEventProjector,
        commit_cancelled_tombstone,
    )

    turn = commit_cancelled_tombstone(empty_rlm_usage())

    events = CommittedTurnEventProjector().project(turn, EventRecorder(uuid4(), uuid4()), mode="replay")

    status = events[0].detail
    assert isinstance(status, Status)
    assert (status.phase, status.status, status.message) == ("cancelled", "cancelled", None)
    assert [event.kind for event in events] == ["status", "usage", "text.delta", "text.completed"]

    suffix = CommittedTurnEventProjector().project(turn, EventRecorder(uuid4(), uuid4()), mode="live_suffix")
    assert [event.kind for event in suffix] == ["usage", "text.delta", "text.completed"]


def test_commit_success_normalizes_details_and_appends_the_canonical_suffix() -> None:
    from fleet_rlm.rlm.events import RLMReasoning, StepFinished, StepStarted, ToolCompleted, ToolStarted
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.committed_turn import commit_success
    from fleet_rlm.workspace.artifacts import ArtifactRef

    artifact = ArtifactRef(
        uuid4(),
        uuid4(),
        uuid4(),
        "json",
        "result",
        "application/json",
        2,
        "a" * 64,
    )
    outcome = RLMOutcome(
        terminal_status="completed",
        prediction=PredictionResult("42", {"answer": "42", "total": 42}, "analysis", "1"),
        usage={"iterations": 1, "observed_lm_usage": {}, "duration_ms": 3},
        execution_details=(
            StepStarted(step=1),
            RLMReasoning(text="bounded", step=1),
            ToolStarted(tool_call_id="call-1", tool_name="lookup", input={"q": "x"}),
            ToolCompleted(tool_call_id="call-1", tool_name="lookup", output={"ok": True}),
            StepFinished(step=1, duration_ms=3),
        ),
    )

    committed = commit_success(outcome, (artifact,))

    assert [part.type for part in committed.parts] == [
        "step",
        "reasoning",
        "tool_call",
        "step",
        "artifact",
        "usage",
        "structured_result",
        "text",
    ]
    assert committed.text == "42"
    assert committed.structured_result == {"answer": "42", "total": 42}


def test_commit_success_coalesces_incremental_output_before_durable_commit() -> None:
    from fleet_rlm.rlm.events import RLMOutput
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.committed_turn import OutputPart, commit_success

    committed = commit_success(
        RLMOutcome(
            terminal_status="completed",
            prediction=PredictionResult("done", {"answer": "done"}, "default", "1"),
            execution_details=(
                RLMOutput("first", 1, "output-1", True, False),
                RLMOutput(" second", 1, "output-1", True, False),
                RLMOutput("first second", 1, "output-1", False, True),
            ),
        ),
        (),
    )

    outputs = [part for part in committed.parts if isinstance(part, OutputPart)]
    assert outputs == [OutputPart(output="first second", step=1)]


def test_commit_persists_only_the_latest_terminal_child_progress() -> None:
    from fleet_rlm.rlm.events import ChildProgress
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.committed_turn import ChildProgressPart, commit_success

    committed = commit_success(
        RLMOutcome(
            terminal_status="completed",
            prediction=PredictionResult("done", {"answer": "done"}, "default", "1"),
            execution_details=(
                ChildProgress("root:call-1", "Inspect code", "running", 10),
                ChildProgress(
                    "root:call-1",
                    "Inspect code",
                    "completed",
                    42,
                    "Reviewed two files",
                    "complete",
                    "run-9",
                    evidence=("src/api.py:42",),
                    gaps=("Caller not checked",),
                    result_file_count=2,
                ),
            ),
        ),
        (),
    )

    children = [part for part in committed.parts if isinstance(part, ChildProgressPart)]
    assert children == [
        ChildProgressPart(
            "root:call-1",
            "Inspect code",
            "completed",
            42,
            "Reviewed two files",
            "complete",
            "run-9",
            evidence=("src/api.py:42",),
            gaps=("Caller not checked",),
            result_file_count=2,
        )
    ]


def test_commit_persists_not_started_child_admission_refusal() -> None:
    from fleet_rlm.rlm.events import ChildProgress
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.committed_turn import ChildProgressPart, commit_success

    committed = commit_success(
        RLMOutcome(
            terminal_status="completed",
            prediction=PredictionResult("done", {"answer": "done"}, "default", "1"),
            execution_details=(
                ChildProgress(
                    "root:call-2",
                    "Inspect large input",
                    "not_started",
                    0,
                    "Child admission budget exhausted",
                    "not_required",
                ),
            ),
        ),
        (),
    )

    assert (
        ChildProgressPart(
            "root:call-2",
            "Inspect large input",
            "not_started",
            0,
            "Child admission budget exhausted",
            "not_required",
        )
        in committed.parts
    )


def test_commit_omits_structured_duplicate_for_single_output_prediction() -> None:
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.committed_turn import commit_success

    committed = commit_success(
        RLMOutcome(
            terminal_status="completed",
            prediction=PredictionResult("done", {"answer": "done"}, "default", "1"),
        ),
        (),
    )

    assert [part.type for part in committed.parts] == ["usage", "text"]
    assert committed.structured_result is None


def test_commit_success_rejects_failed_outcomes_or_unmatched_tool_calls() -> None:
    from fleet_rlm.rlm.events import ToolStarted
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.committed_turn import TurnDetailPolicyError, commit_success

    with pytest.raises(TurnDetailPolicyError):
        commit_success(RLMOutcome(terminal_status="failed"), ())
    with pytest.raises(TurnDetailPolicyError, match="tool call start has no terminal observation"):
        commit_success(
            RLMOutcome(
                terminal_status="completed",
                prediction=PredictionResult("done", {"answer": "done"}, "default", "1"),
                execution_details=(ToolStarted(tool_call_id="call-1", tool_name="lookup", input={}),),
            ),
            (),
        )


def test_commit_success_normalizes_guard_closed_no_progress_tool_call() -> None:
    """RC-2: ToolStarted closed by the guard's ToolFailed commits as failed."""
    import dspy

    from fleet_rlm.rlm.events import (
        ToolCompleted,
        ToolEventView,
        ToolFailed,
        ToolStarted,
        observe_tool,
    )
    from fleet_rlm.rlm.execution import RunToolGuards
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome, RunNoProgressError
    from fleet_rlm.sessions.committed_turn import ToolCallPart, commit_success

    observed: list[object] = []
    wrapped = observe_tool(
        dspy.Tool(lambda query: f"result for {query}", name="lookup"),
        observed.append,
        ToolEventView.metadata_only(),
        guards=RunToolGuards(),
    )
    assert wrapped(query="repeat") == "result for repeat"
    with pytest.raises(RunNoProgressError):
        wrapped(query="repeat")

    execution_details = tuple(item for item in observed if isinstance(item, (ToolStarted, ToolCompleted, ToolFailed)))
    committed = commit_success(
        RLMOutcome(
            terminal_status="completed",
            prediction=PredictionResult("done", {"answer": "done"}, "default", "1"),
            execution_details=execution_details,
        ),
        (),
    )

    tools = [part for part in committed.parts if isinstance(part, ToolCallPart)]
    assert [part.state for part in tools] == ["completed", "failed"]
    assert tools[1].tool_name == "lookup"
    assert tools[1].tool_call_id != tools[0].tool_call_id
    assert tools[1].error == "repeated tool call produced no progress"
