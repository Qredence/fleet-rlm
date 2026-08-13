"""Cross-Turn Session Workspace behavior through fresh native RLM runs."""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from uuid import UUID, uuid4

import dspy
import pytest

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.files.memory_models import (
    WorkspaceMemoryAppendResult,
    WorkspaceMemoryEntryNotFoundError,
    WorkspaceMemoryListResult,
    WorkspaceMemoryReadResult,
    parse_workspace_memory_lines,
    reformat_workspace_memory_record,
)
from fleet_rlm.files.memory_tools import WorkspaceMemoryToolHost
from fleet_rlm.files.workspace_models import (
    DAYTONA_WORKSPACE_CAPABILITY,
    WorkspaceEntry,
    WorkspaceListResult,
    WorkspaceTextPage,
)
from fleet_rlm.files.workspace_tools import WorkspaceToolError, WorkspaceToolHost
from fleet_rlm.rlm.context import (
    ExecutionRuntime,
    RLMExecutionContext,
    RLMExecutionSpec,
    RunIdentity,
    SessionView,
)
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.errors import RunCancelledError
from fleet_rlm.rlm.events import RuntimeEvent
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.sessions.models import TurnAccess
from tests.unit.backend.rlm.fakes import FakeRLMInterpreter, HostCapabilityDefaults


class MemoryStore:
    def __init__(self) -> None:
        self.content = ""

    def read_tail(self, *, byte_budget: int) -> WorkspaceMemoryReadResult:
        data = self.content.encode("utf-8")
        assert len(data) <= byte_budget
        return WorkspaceMemoryReadResult(
            content=self.content,
            truncated=False,
            bytes_returned=len(data),
            byte_budget=byte_budget,
            total_bytes=len(data),
        )

    def append_record(self, record: str) -> WorkspaceMemoryAppendResult:
        self.content += record
        total_bytes = len(self.content.encode("utf-8"))
        return WorkspaceMemoryAppendResult(
            entry_bytes=len(record.encode("utf-8")),
            total_bytes=total_bytes,
        )

    def list_entries(
        self,
        *,
        after: str | None = None,
        limit: int,
        category: str | None = None,
    ) -> WorkspaceMemoryListResult:
        lines = parse_workspace_memory_lines(self.content)
        entries = [line.entry for line in lines if line.entry is not None]
        if after is not None:
            matches = [index for index, entry in enumerate(entries) if entry.memory_id == after]
            if not matches:
                raise WorkspaceMemoryEntryNotFoundError(after)
            entries = entries[matches[-1] + 1 :]
        if category is not None:
            entries = [entry for entry in entries if entry.category == category]
        page = tuple(entries[:limit])
        return WorkspaceMemoryListResult(
            entries=page,
            truncated=len(entries) > limit,
            next_cursor=page[-1].memory_id if len(entries) > limit and page else None,
            warnings=0,
        )

    def delete_entry(self, memory_id: str) -> bool:
        lines = list(parse_workspace_memory_lines(self.content))
        targets = [
            index for index, line in enumerate(lines) if line.entry is not None and line.entry.memory_id == memory_id
        ]
        if not targets:
            return False
        del lines[targets[-1]]
        self.content = "".join(line.raw for line in lines)
        return True

    def edit_entry(self, memory_id: str, key_learning: str, *, category: str | None = None) -> str:
        lines = list(parse_workspace_memory_lines(self.content))
        targets = [
            index for index, line in enumerate(lines) if line.entry is not None and line.entry.memory_id == memory_id
        ]
        if not targets:
            raise WorkspaceMemoryEntryNotFoundError(memory_id)
        target = targets[-1]
        entry = lines[target].entry
        assert entry is not None
        record, _normalized = reformat_workspace_memory_record(
            timestamp=entry.timestamp,
            memory_id=entry.memory_id,
            category=entry.category if category is None else category,
            key_learning=key_learning,
        )
        lines[target] = parse_workspace_memory_lines(record)[0]
        self.content = "".join(line.raw for line in lines)
        return record


class MemoryStoreRegistry:
    def __init__(self) -> None:
        self._stores: dict[UUID, MemoryStore] = {}

    def resolve(self, workspace_id: UUID) -> MemoryStore:
        return self._stores.setdefault(workspace_id, MemoryStore())


class MemoryWorkspace:
    def __init__(
        self,
        *,
        memory_registry: MemoryStoreRegistry | None = None,
        access: TurnAccess | None = None,
    ) -> None:
        self.session_id = uuid4()
        self.memory_registry = memory_registry or MemoryStoreRegistry()
        self.access = access or TurnAccess(uuid4(), uuid4())
        self.files: dict[str, str] = {}

    def list_entries(self, path: str, *, limit: int = 100, after: str | None = None) -> WorkspaceListResult:
        del path
        items = [(name, content) for name, content in sorted(self.files.items()) if after is None or name > after]
        selected = items[:limit]
        return WorkspaceListResult(
            entries=tuple(WorkspaceEntry(name, "file", len(content.encode()), None) for name, content in selected),
            truncated=len(items) > limit,
            next_cursor=selected[-1][0] if len(items) > limit else None,
        )

    def stat(self, path: str) -> WorkspaceEntry | None:
        content = self.files.get(path)
        return None if content is None else WorkspaceEntry(path, "file", len(content.encode()), None)

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int,
    ) -> WorkspaceTextPage:
        content = self.files[path]
        if len(content.encode()) > max_bytes:
            raise ValueError("workspace file exceeds read bound")
        if cursor is not None:
            raise ValueError("workspace cursor is invalid")
        return WorkspaceTextPage(content[:max_chars], None, len(content.encode()), len(content) <= max_chars)

    def write_text(self, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry:
        if path in self.files and not overwrite:
            raise FileExistsError(path)
        self.files[path] = content
        return WorkspaceEntry(path, "file", len(content.encode()), None)

    def append_text(self, path: str, content: str) -> WorkspaceEntry:
        self.files[path] = self.files.get(path, "") + content
        return WorkspaceEntry(path, "file", len(self.files[path].encode()), None)


class Capabilities(HostCapabilityDefaults):
    def __init__(self, workspace: MemoryWorkspace) -> None:
        workspace_host = WorkspaceToolHost(workspace, max_file_bytes=1024)
        memory_host = WorkspaceMemoryToolHost(workspace.memory_registry.resolve(workspace.access.workspace_id))
        self.spec = RLMExecutionSpec(
            tools=(*workspace_host.as_tools(), *memory_host.as_tools()),
            tool_event_views={
                **workspace_host.event_views(),
                **memory_host.event_views(),
            },
            workspace=DAYTONA_WORKSPACE_CAPABILITY,
        )

    def drain_public_details(self):
        return ()

    def drain_artifact_candidates(self):
        return ()

    def drain_memory_candidates(self):
        return ()


class Interpreter(FakeRLMInterpreter):
    def __init__(self) -> None:
        self.variables: dict[str, object] = {}


class WorkspaceFlowFactory:
    def __init__(self) -> None:
        self.interpreters: list[Interpreter] = []

    def create(self, **kwargs):
        interpreter = Interpreter()
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
                    return dspy.Prediction(answer=result["content"], trajectory=[])
                if request.startswith("remember"):
                    learning = {
                        "remember": "Keep release notes concise.",
                        "remember_failed": "Failed-turn memory remains durable.",
                        "remember_cancelled": "Cancelled-turn memory remains durable.",
                    }[request]
                    result = tools["update_workspace_memory"](
                        key_learning=learning,
                        category="Preference",
                    )
                    assert result["ok"] is True
                    if request == "remember_failed":
                        raise RuntimeError("turn failed after memory append")
                    if request == "remember_cancelled":
                        raise RunCancelledError
                    return dspy.Prediction(answer="remembered", trajectory=[])
                if request == "recall":
                    result = tools["read_workspace_memory"]()
                    return dspy.Prediction(answer=result["content"] or "NO_MEMORY", trajectory=[])
                if request.startswith("roundtrip"):
                    today = "2026-07-21"
                    tools["write_workspace_text"](
                        path="workspace/date.txt",
                        content=today,
                        overwrite=True,
                    )
                    first = tools["read_workspace_text"](path="workspace/date.txt", max_chars=100)
                    second = tools["read_workspace_text"](path="workspace/date.txt", max_chars=100)
                    assert first["content"] == second["content"] == today
                    tools["write_workspace_text"](
                        path="workspace/date.txt",
                        content="verified",
                        overwrite=True,
                    )
                    final = tools["read_workspace_text"](path="workspace/date.txt", max_chars=100)
                    assert final["content"] == "verified"
                    return dspy.Prediction(answer=final["content"], trajectory=[])
                if request.startswith(("unresolved", "repaired")):
                    tools["write_workspace_text"](path="notes/target.md", content="old", overwrite=False)
                    with contextlib.suppress(WorkspaceToolError):
                        tools["write_workspace_text"](path="notes/target.md", content="new", overwrite=False)
                    if request.startswith("repaired"):
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
                raise RunCancelledError

        return Program()


async def _run(
    factory: WorkspaceFlowFactory,
    workspace: MemoryWorkspace,
    request: str,
    *,
    events: list[RuntimeEvent] | None = None,
):
    async def not_cancelled() -> bool:
        return False

    if request in {"unresolved", "repaired"}:
        task_request = f"{request} notes/target.md"
    elif request == "roundtrip":
        task_request = "roundtrip workspace/date.txt"
    else:
        task_request = request
    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=workspace.session_id, access=workspace.access),
        session=SessionView(
            request=task_request,
            session_context=SessionContextManifest(workspace.session_id, 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=Interpreter(),
            cancellation_requested=not_cancelled,
        ),
        capabilities=Capabilities(workspace),
    )
    stream = RLMRunner(factory=factory).stream(context)
    observed = [event async for event in stream]
    if events is not None:
        events.extend(observed)
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
    assert read_tool(path=f"notes/{turn_kind}.md", max_chars=100)["content"] == (f"{turn_kind} durable")


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


@pytest.mark.asyncio
async def test_workspace_date_roundtrip_allows_repeated_reads_and_overwrite() -> None:
    outcome = await _run(WorkspaceFlowFactory(), MemoryWorkspace(), "roundtrip")

    assert outcome is not None and outcome.succeeded
    assert outcome.prediction is not None
    assert outcome.prediction.display_text == "verified"


@pytest.mark.asyncio
async def test_workspace_memory_is_shared_across_sessions_without_exposing_its_body_in_tool_events() -> None:
    registry = MemoryStoreRegistry()
    workspace_id = uuid4()
    first_session = MemoryWorkspace(
        memory_registry=registry,
        access=TurnAccess(uuid4(), workspace_id),
    )
    second_session = MemoryWorkspace(
        memory_registry=registry,
        access=TurnAccess(uuid4(), workspace_id),
    )
    events: list[RuntimeEvent] = []

    written = await _run(WorkspaceFlowFactory(), first_session, "remember", events=events)
    recalled = await _run(WorkspaceFlowFactory(), second_session, "recall", events=events)

    assert first_session.session_id != second_session.session_id
    assert first_session.access.user_id != second_session.access.user_id
    assert first_session.access.workspace_id == second_session.access.workspace_id
    assert written is not None and written.succeeded
    assert recalled is not None and recalled.prediction is not None
    assert "Keep release notes concise." in recalled.prediction.display_text
    memory_details = [
        event.detail
        for event in events
        if getattr(event.detail, "tool_name", None) in {"read_workspace_memory", "update_workspace_memory"}
    ]
    assert {detail.kind for detail in memory_details} == {"tool.started", "tool.completed"}
    assert "Keep release notes concise." not in repr(memory_details)


@pytest.mark.asyncio
async def test_workspace_memory_is_isolated_between_workspace_ids() -> None:
    registry = MemoryStoreRegistry()
    first_workspace = MemoryWorkspace(memory_registry=registry)
    second_workspace = MemoryWorkspace(memory_registry=registry)

    written = await _run(WorkspaceFlowFactory(), first_workspace, "remember")
    isolated_read = await _run(WorkspaceFlowFactory(), second_workspace, "recall")

    assert first_workspace.access.workspace_id != second_workspace.access.workspace_id
    assert written is not None and written.succeeded
    assert isolated_read is not None and isolated_read.prediction is not None
    assert isolated_read.prediction.display_text == "NO_MEMORY"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("turn_request", "terminal", "learning"),
    [
        ("remember_failed", "failed", "Failed-turn memory remains durable."),
        ("remember_cancelled", "cancelled", "Cancelled-turn memory remains durable."),
    ],
)
async def test_successful_memory_append_survives_failed_or_cancelled_turn(
    turn_request: str,
    terminal: str,
    learning: str,
) -> None:
    registry = MemoryStoreRegistry()
    workspace_id = uuid4()
    writing_session = MemoryWorkspace(
        memory_registry=registry,
        access=TurnAccess(uuid4(), workspace_id),
    )
    reading_session = MemoryWorkspace(
        memory_registry=registry,
        access=TurnAccess(uuid4(), workspace_id),
    )
    events: list[RuntimeEvent] = []

    outcome = await _run(WorkspaceFlowFactory(), writing_session, turn_request, events=events)
    recalled = await _run(WorkspaceFlowFactory(), reading_session, "recall")

    assert outcome is not None and outcome.terminal_status == terminal
    assert any(
        event.kind == "tool.completed" and getattr(event.detail, "tool_name", None) == "update_workspace_memory"
        for event in events
    )
    assert recalled is not None and recalled.prediction is not None
    assert learning in recalled.prediction.display_text
