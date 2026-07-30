from __future__ import annotations

import time
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.context import RLMExecutionContext, RLMExecutionSpec
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.events import ToolCompleted, ToolStarted
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.recursive_calls import RecursiveRLMOptions
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.sessions.models import TurnAccess


@pytest.mark.asyncio
async def test_root_child_root_flow_preserves_parent_repl_and_typed_submit() -> None:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "prepare selected data", "code": "rows = ['alpha', 'beta']"},
            {"reasoning": "delegate selected row", "code": "child = rlm_query('classify: ' + rows[0])"},
            {"reasoning": "child submit", "code": "SUBMIT(answer='child-ok')"},
            {"reasoning": "integrate child answer", "code": "SUBMIT(answer=child)"},
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        run_id=uuid4(),
        session_id=uuid4(),
        access=TurnAccess(uuid4(), uuid4()),
        request="classify the selected row",
        session_context=SessionContextManifest(uuid4(), 0, 0, ()),
        models=RLMModelBundle(root, sub),
        options=RLMOptions(max_iterations=4, max_llm_calls=4),
        deadline=time.monotonic() + 30,
        interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
        attachments=(),
        capabilities=Capabilities(),
        cancellation_requested=not_cancelled,
        preparation_notices=(),
        recursive_options=RecursiveRLMOptions(max_calls=2),
        child_interpreter_factory=lambda: DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
    )

    stream = RLMRunner().stream(context)
    events = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.display_text == "child-ok"
    assert stream.outcome.usage["iterations"] == 3
    tool_started = [event for event in events if event.kind == "tool.started"]
    tool_completed = [event for event in events if event.kind == "tool.completed"]
    assert len(tool_started) == len(tool_completed) == 1
    assert isinstance(tool_started[0].detail, ToolStarted)
    assert isinstance(tool_completed[0].detail, ToolCompleted)
    assert tool_started[0].detail.input == {"prompt_count": 1, "prompt_chars": len("classify: alpha")}
    assert tool_completed[0].detail.output == {"status": "completed"}
