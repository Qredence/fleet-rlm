"""Cross-Turn Session Workspace behavior through fresh native RLM runs."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.files.workspace_models import DAYTONA_WORKSPACE_CAPABILITY, WorkspaceEntry, WorkspaceListResult
from fleet_rlm.files.workspace_tools import WorkspaceToolError, WorkspaceToolHost
from fleet_rlm.rlm.context import RLMExecutionContext
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.errors import TurnCancelled
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.sessions.models import TurnAccess
from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint


class MemoryWorkspace:
    def __init__(self) -> None:
        self.session_id = uuid4()
        self.files: dict[str, str] = {}

    def list_entries(self, path: str, *, limit: int = 100) -> WorkspaceListResult:
        del path
        items = sorted(self.files.items())
        return WorkspaceListResult(
            entries=tuple(WorkspaceEntry(name, "file", len(content.encode()), None) for name, content in items[:limit]),
            truncated=len(items) > limit,
        )

    def stat(self, path: str) -> WorkspaceEntry | None:
        content = self.files.get(path)
        return None if content is None else WorkspaceEntry(path, "file", len(content.encode()), None)

    def read_text(self, path: str, *, max_bytes: int) -> str:
        content = self.files[path]
        if len(content.encode()) > max_bytes:
            raise ValueError("workspace file exceeds read bound")
        return content

    def write_text(self, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry:
        if path in self.files and not overwrite:
            raise FileExistsError(path)
        self.files[path] = content
        return WorkspaceEntry(path, "file", len(content.encode()), None)


class Capabilities:
    def __init__(self, workspace: MemoryWorkspace) -> None:
        self.blueprint = TurnCapabilityBlueprint(
            tools=WorkspaceToolHost(workspace, max_file_bytes=1024).as_tools(),
            workspace=DAYTONA_WORKSPACE_CAPABILITY,
        )

    def drain_public_details(self):
        return ()

    def drain_artifact_candidates(self):
        return ()


class Interpreter:
    def __init__(self) -> None:
        self.variables: dict[str, object] = {}


class WorkspaceFlowFactory:
    def __init__(self) -> None:
        self.interpreters: list[Interpreter] = []

    def create(self, **kwargs):
        interpreter = kwargs["interpreter"]
        self.interpreters.append(interpreter)
        tools = {str(tool.name): tool for tool in kwargs["tools"]}

        class Program:
            async def acall(self, **call_kwargs):
                request = call_kwargs["request"]
                assert call_kwargs["session_context"]["workspace"]["available"] is True
                if request == "write":
                    interpreter.variables["temporary"] = "not durable"
                    result = tools["write_workspace_text"](
                        path="notes/decision.md",
                        content="durable decision",
                        overwrite=False,
                    )
                    assert result["ok"] is True
                    return dspy.Prediction(answer="written", trajectory=[])
                if request == "read":
                    assert interpreter.variables == {}
                    result = tools["read_workspace_text"](path="notes/decision.md", max_chars=100)
                    return dspy.Prediction(answer=result, trajectory=[])
                if request in {"unresolved", "repaired"}:
                    tools["write_workspace_text"](path="notes/target.md", content="old", overwrite=False)
                    try:
                        tools["write_workspace_text"](path="notes/target.md", content="new", overwrite=False)
                    except WorkspaceToolError:
                        pass
                    if request == "repaired":
                        tools["write_workspace_text"](path="notes/target.md", content="new", overwrite=True)
                    tools["read_workspace_text"](path="notes/target.md", max_chars=100)
                    return dspy.Prediction(answer="submitted", trajectory=[])
                if request == "loop":
                    for _ in range(3):
                        tools["list_workspace_files"](path=".", limit=1)
                    return dspy.Prediction(answer="submitted", trajectory=[])
                result = tools["write_workspace_text"](
                    path=f"notes/{request}.md",
                    content=f"{request} durable",
                    overwrite=False,
                )
                assert result["ok"] is True
                if request == "failed":
                    raise RuntimeError("turn failed after write")
                raise TurnCancelled

        return Program()


async def _run(
    factory: WorkspaceFlowFactory,
    workspace: MemoryWorkspace,
    request: str,
):
    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        run_id=uuid4(),
        session_id=workspace.session_id,
        access=TurnAccess(uuid4(), uuid4()),
        request=request,
        session_context=SessionContextManifest(uuid4(), 0, 0, ()),
        models=SimpleNamespace(root_lm=object(), sub_lm=object()),
        options=RLMOptions(),
        deadline=asyncio.get_running_loop().time() + 10,
        interpreter=Interpreter(),
        attachments=(),
        capabilities=Capabilities(workspace),
        cancellation_requested=not_cancelled,
        preparation_notices=(),
    )
    stream = RLMRunner(factory=factory).stream(context)
    _ = [event async for event in stream]
    return stream.outcome


@pytest.mark.asyncio
async def test_workspace_survives_fresh_turn_context_and_repl_state_does_not() -> None:
    workspace = MemoryWorkspace()
    factory = WorkspaceFlowFactory()

    written = await _run(factory, workspace, "write")
    read = await _run(factory, workspace, "read")

    assert written is not None and written.succeeded
    assert read is not None and read.prediction is not None
    assert read.prediction.display_text == "durable decision"
    assert len(factory.interpreters) == 2
    assert factory.interpreters[0] is not factory.interpreters[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("turn_kind, terminal", [("failed", "failed"), ("cancelled", "cancelled")])
async def test_successful_workspace_write_survives_failed_or_cancelled_turn(
    turn_kind: str,
    terminal: str,
) -> None:
    workspace = MemoryWorkspace()
    outcome = await _run(WorkspaceFlowFactory(), workspace, turn_kind)

    assert outcome is not None and outcome.terminal_status == terminal
    read_tool = {str(tool.name): tool for tool in WorkspaceToolHost(workspace, max_file_bytes=1024).as_tools()}[
        "read_workspace_text"
    ]
    assert read_tool(path=f"notes/{turn_kind}.md", max_chars=100) == (f"{turn_kind} durable")


@pytest.mark.asyncio
async def test_failed_workspace_mutation_blocks_submit_until_same_path_is_repaired() -> None:
    workspace = MemoryWorkspace()

    unresolved = await _run(WorkspaceFlowFactory(), workspace, "unresolved")
    assert unresolved is not None
    assert unresolved.terminal_status == "failed"
    assert unresolved.public_error_message == "Turn failed because a required workspace update was not completed"

    repaired = await _run(WorkspaceFlowFactory(), MemoryWorkspace(), "repaired")
    assert repaired is not None and repaired.succeeded


@pytest.mark.asyncio
async def test_three_identical_tool_results_fail_the_turn_for_no_progress() -> None:
    outcome = await _run(WorkspaceFlowFactory(), MemoryWorkspace(), "loop")

    assert outcome is not None
    assert outcome.terminal_status == "failed"
    assert outcome.public_error_message == "Turn stopped after repeated tool calls made no progress"
