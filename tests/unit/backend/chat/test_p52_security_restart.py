"""P52.5/P52.6 two-Turn tool-security and runtime-plane restart behavior.

The tests exercise, in the exact order below:

1.  Stale Skill authorization fails closed (P52.5(c)): a ``load_skill`` proxy
    retained from Turn 1 re-resolves through Turn 2's binding and is rejected
    by the new Turn's allow-list, while a still-authorized Skill loads.
2.  Stale Memory proposal capability fails closed (P52.5(d)): a
    ``propose_memory`` proxy retained from a finished Run raises
    ``SessionToolAuthorizationError`` once the next Turn drops the tool.
3.  Name-kept variant (P52.5(d)): when Turn 2 keeps ``propose_memory`` bound
    to a NEW run-scoped collector, the retained proxy records into the CURRENT
    Run's collector only — never back into the finished Run's collector.
4.  Runtime-plane restart recovery (P52.6(b)): a full restart over the same
    DB/volume restores durable History/Workspace/Memory through freshly
    prepared tools, while arbitrary Python globals are gone.

Every stream driven through ``TurnRuntime`` is wrapped by ``_RecordingStream``
implementing BOTH ``mark_tainted`` and ``mark_committed`` so the duck-typed
``chat/turn_runtime.py::_mark_stream_runtime`` seam is recorded, not silently
satisfied.
"""

from __future__ import annotations

import hashlib
from contextlib import redirect_stdout, suppress
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.attachments.models import PreparedAttachments
from fleet_rlm.chat.capability_preparation import skill_event
from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.chat.preparation import DefaultRunPreparer, RunEnvironment
from fleet_rlm.chat.run_lifecycle import RunLifecycleService
from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.chat.turn_runtime import TurnRuntime
from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow
from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
from fleet_rlm.rlm.events import RunCompleted, SkillActivated, SkillLoaded, ToolCompleted
from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
from fleet_rlm.rlm.runtime import (
    ExecutionRuntime,
    RLMExecutionContext,
    RLMExecutionSpec,
    RLMRunner,
    RunIdentity,
    SessionView,
)
from fleet_rlm.rlm.session_runtime import SessionRLMRegistry, SessionToolAuthorizationError
from fleet_rlm.sessions.models import TurnAccess, TurnInput
from fleet_rlm.skills.catalog import build_bundled_skill_catalog, stable_skill_id
from fleet_rlm.skills.tools import SkillToolHost
from fleet_rlm.workspace.memory import (
    MemoryCandidateCollector,
    MemoryCandidateToolHost,
    WorkspaceMemoryToolHost,
    build_workspace_memory_store,
)
from fleet_rlm.workspace.models import DAYTONA_WORKSPACE_CAPABILITY
from fleet_rlm.workspace.storage import AgentStorageSession, DaytonaSessionWorkspaceFS, WorkspaceMemoryStorage
from fleet_rlm.workspace.workspace import WorkspaceToolHost


class _Interpreter:
    """Minimal resident interpreter double with a persistent namespace."""

    def __init__(self) -> None:
        self.namespace: dict[str, object] = {}


async def _not_cancelled() -> bool:
    return False


def _context(
    *,
    run_id,
    session_id,
    workspace_id,
    request: str,
    capabilities: object,
    interpreter: _Interpreter | None = None,
) -> RLMExecutionContext:
    return RLMExecutionContext(
        identity=RunIdentity(run_id=run_id, session_id=session_id, access=TurnAccess(uuid4(), workspace_id)),
        session=SessionView(
            request=request,
            session_context=SessionContextManifest(session_id, 0, 0, ()),
            attachments=(),
            history=dspy.History(messages=[]),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(object(), object()),
            options=RLMOptions(),
            interpreter=interpreter if interpreter is not None else _Interpreter(),
            cancellation_requested=_not_cancelled,
            deadline=10**12,
        ),
        capabilities=cast(Any, capabilities),
    )


@pytest.mark.asyncio
async def test_stale_skill_proxy_resolves_through_current_turn_and_fails_closed() -> None:
    """A Turn-1 ``load_skill`` proxy is re-authorized by Turn 2's allow-list (P52.5(c))."""

    class _SkillCapabilities:
        def __init__(self, host: SkillToolHost) -> None:
            self._host = host
            self.spec = RLMExecutionSpec(tools=host.as_tools(), tool_event_views=dict(host.event_views()))

        def drain_public_details(self):
            return tuple(skill_event(item) for item in self._host.drain_public_events())

        def drain_artifact_candidates(self):
            return ()

        def drain_memory_candidates(self):
            return ()

        async def aclose(self):
            return None

    catalog = build_bundled_skill_catalog()
    skill_a = catalog.require(stable_skill_id("long-context"))
    skill_b = catalog.require(stable_skill_id("workspace-files"))
    host_turn_1 = SkillToolHost(catalog, allowed_skill_ids=frozenset({skill_a.card.id}))
    host_turn_2 = SkillToolHost(catalog, allowed_skill_ids=frozenset({skill_b.card.id}))

    results: dict[str, object] = {}

    class Factory:
        def __init__(self) -> None:
            self.creates = 0
            self.retained: dict[str, object] | None = None

        def create(self, **kwargs):
            self.creates += 1
            if self.retained is None and kwargs.get("tools"):
                # Proxies installed for Turn 1; the interpreter keeps them as
                # Python globals across Turns (resident namespace).
                self.retained = {str(tool.name): tool for tool in kwargs["tools"]}
            factory = self

            class Program:
                async def acall(self, **call_kwargs):
                    retained = factory.retained
                    assert retained is not None
                    load_skill = retained["load_skill"]
                    if call_kwargs["request"] == "first":
                        results["turn_1"] = load_skill(skill_id=str(skill_a.card.id))
                        return dspy.Prediction(answer="loaded", trajectory=[])
                    # Turn 2: the RETAINED Turn-1 proxy resolves through the
                    # CURRENT binding (Turn 2's host and allow-list).
                    results["turn_2_stale"] = load_skill(skill_id=str(skill_a.card.id))
                    results["turn_2_allowed"] = load_skill(skill_id=str(skill_b.card.id))
                    return dspy.Prediction(answer=results["turn_2_stale"]["error"], trajectory=[])

            return Program()

    session_id, workspace_id = uuid4(), uuid4()
    factory = Factory()
    runner = RLMRunner(factory=factory)
    try:
        first = runner.stream(
            _context(
                run_id=uuid4(),
                session_id=session_id,
                workspace_id=workspace_id,
                request="first",
                capabilities=_SkillCapabilities(host_turn_1),
            )
        )
        events_1 = [event async for event in first]
        assert first.outcome is not None and first.outcome.succeeded
        first.mark_committed()
        await first.aclose()

        second = runner.stream(
            _context(
                run_id=uuid4(),
                session_id=session_id,
                workspace_id=workspace_id,
                request="second",
                capabilities=_SkillCapabilities(host_turn_2),
            )
        )
        events_2 = [event async for event in second]
        assert second.outcome is not None and second.outcome.succeeded
        second.mark_committed()
        await second.aclose()
    finally:
        await runner.aclose()

    # Turn 1 loaded the authorized Skill through the new proxy.
    assert results["turn_1"]["ok"] is True
    assert results["turn_1"]["name"] == "long-context"
    loaded_1 = [event.detail for event in events_1 if isinstance(event.detail, SkillLoaded)]
    assert [str(detail.skill_id) for detail in loaded_1] == [str(skill_a.card.id)]

    # Turn 2: the retained proxy fails closed for the no-longer-authorized
    # Skill, and loads the currently authorized one through the SAME proxy.
    assert results["turn_2_stale"] == {"ok": False, "error": "skill_not_found"}
    assert results["turn_2_allowed"]["ok"] is True
    assert results["turn_2_allowed"]["name"] == "workspace-files"
    assert second.outcome.prediction is not None
    assert second.outcome.prediction.display_text == "skill_not_found"

    # One resident program served both Turns (identical Tool shape): the stale
    # proxy provably re-resolved through the CURRENT binding.
    assert factory.creates == 1

    # Turn 2 emitted load telemetry for the authorized Skill only; the stale
    # call produced no Skill activation/loading.
    loaded_2 = [event.detail for event in events_2 if isinstance(event.detail, SkillLoaded | SkillActivated)]
    assert {str(detail.skill_id) for detail in loaded_2} == {str(skill_b.card.id)}
    load_completed = [
        event.detail
        for event in events_2
        if isinstance(event.detail, ToolCompleted) and event.detail.tool_name == "load_skill"
    ]
    assert [detail.output["ok"] for detail in load_completed] == [False, True]
    assert load_completed[0].output["error"] == "skill_not_found"


@pytest.mark.asyncio
async def test_stale_propose_memory_proxy_fails_closed_when_next_turn_drops_the_tool() -> None:
    """A finished Run's ``propose_memory`` surface dies with its binding (P52.5(d))."""

    class _MemoryCapabilities:
        def __init__(self, collector: MemoryCandidateCollector | None) -> None:
            self._collector = collector
            if collector is None:
                self.spec = RLMExecutionSpec()
            else:
                host = MemoryCandidateToolHost(collector)
                self.spec = RLMExecutionSpec(tools=host.as_tools(), tool_event_views=dict(host.event_views()))

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        def drain_memory_candidates(self):
            return () if self._collector is None else self._collector.drain()

        async def aclose(self):
            return None

    results: dict[str, object] = {}
    run_1, run_2 = uuid4(), uuid4()

    class Factory:
        def __init__(self) -> None:
            self.creates = 0
            self.retained: dict[str, object] | None = None

        def create(self, **kwargs):
            self.creates += 1
            if self.retained is None and kwargs.get("tools"):
                self.retained = {str(tool.name): tool for tool in kwargs["tools"]}
            factory = self

            class Program:
                async def acall(self, **call_kwargs):
                    retained = factory.retained
                    assert retained is not None
                    propose_memory = retained["propose_memory"]
                    if call_kwargs["request"] == "first":
                        results["turn_1"] = propose_memory(
                            key_learning="Prefer concise release notes.", category="project"
                        )
                        return dspy.Prediction(answer="proposed", trajectory=[])
                    try:
                        results["turn_2"] = propose_memory(key_learning="stale write attempt", category="project")
                    except SessionToolAuthorizationError as exc:
                        results["turn_2"] = {"raised": type(exc).__name__}
                    return dspy.Prediction(answer="done-2", trajectory=[])

            return Program()

    session_id, workspace_id = uuid4(), uuid4()
    factory = Factory()
    runner = RLMRunner(factory=factory)
    collector_1 = MemoryCandidateCollector(run_id=run_1, allowed_categories=["project"])
    try:
        first = runner.stream(
            _context(
                run_id=run_1,
                session_id=session_id,
                workspace_id=workspace_id,
                request="first",
                capabilities=_MemoryCapabilities(collector_1),
            )
        )
        _events_1 = [event async for event in first]
        assert first.outcome is not None and first.outcome.succeeded
        first.mark_committed()
        await first.aclose()

        # Turn 1's proposal was drained into the Run-1 outcome (promotion path).
        assert results["turn_1"]["ok"] is True
        assert [candidate.candidate_id for candidate in first.outcome.memory_candidates] == [
            hashlib.sha256(f"{run_1}:memory-candidate:1".encode()).hexdigest()[:12]
        ]
        assert collector_1.drain() == ()

        second = runner.stream(
            _context(
                run_id=run_2,
                session_id=session_id,
                workspace_id=workspace_id,
                request="second",
                capabilities=_MemoryCapabilities(None),
            )
        )
        _events_2 = [event async for event in second]
        assert second.outcome is not None and second.outcome.succeeded
        second.mark_committed()
        await second.aclose()
    finally:
        await runner.aclose()

    # The retained finished-Run surface fails closed: no candidate is recorded
    # anywhere — not into Turn 2 (which exposes no collector) and never back
    # into the finished Run-1 collector.
    assert results["turn_2"] == {"raised": "SessionToolAuthorizationError"}
    assert second.outcome is not None and second.outcome.memory_candidates == ()
    assert collector_1.drain() == ()
    # The Tool set changed across Turns, so the resident program rotated; the
    # stale proxy cannot even resolve a binding from the retired registry.
    assert factory.creates == 2


@pytest.mark.asyncio
async def test_retained_propose_memory_proxy_rebinds_to_current_run_scoped_collector() -> None:
    """A name-kept retained proxy records into the CURRENT Run's collector only (P52.5(d))."""

    class _MemoryCapabilities:
        def __init__(self, collector: MemoryCandidateCollector) -> None:
            self._collector = collector
            host = MemoryCandidateToolHost(collector)
            self.spec = RLMExecutionSpec(tools=host.as_tools(), tool_event_views=dict(host.event_views()))

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        def drain_memory_candidates(self):
            return self._collector.drain()

        async def aclose(self):
            return None

    results: dict[str, object] = {}
    run_1, run_2 = uuid4(), uuid4()

    class Factory:
        def __init__(self) -> None:
            self.creates = 0
            self.retained: dict[str, object] | None = None

        def create(self, **kwargs):
            self.creates += 1
            if self.retained is None and kwargs.get("tools"):
                self.retained = {str(tool.name): tool for tool in kwargs["tools"]}
            factory = self

            class Program:
                async def acall(self, **call_kwargs):
                    retained = factory.retained
                    assert retained is not None
                    propose_memory = retained["propose_memory"]
                    if call_kwargs["request"] == "first":
                        results["turn_1"] = propose_memory(key_learning="Turn one learning.", category="project")
                        return dspy.Prediction(answer="one", trajectory=[])
                    results["turn_2"] = propose_memory(key_learning="Turn two learning.", category="project")
                    return dspy.Prediction(answer="two", trajectory=[])

            return Program()

    session_id, workspace_id = uuid4(), uuid4()
    factory = Factory()
    runner = RLMRunner(factory=factory)
    collector_1 = MemoryCandidateCollector(run_id=run_1, allowed_categories=["project"])
    collector_2 = MemoryCandidateCollector(run_id=run_2, allowed_categories=["project"])
    try:
        first = runner.stream(
            _context(
                run_id=run_1,
                session_id=session_id,
                workspace_id=workspace_id,
                request="first",
                capabilities=_MemoryCapabilities(collector_1),
            )
        )
        _events_1 = [event async for event in first]
        assert first.outcome is not None and first.outcome.succeeded
        first.mark_committed()
        await first.aclose()

        second = runner.stream(
            _context(
                run_id=run_2,
                session_id=session_id,
                workspace_id=workspace_id,
                request="second",
                capabilities=_MemoryCapabilities(collector_2),
            )
        )
        _events_2 = [event async for event in second]
        assert second.outcome is not None and second.outcome.succeeded
        second.mark_committed()
        await second.aclose()
    finally:
        await runner.aclose()

    # Identical Tool shape across Turns: the resident program is reused, so
    # the proxy object from Turn 1 is exactly what Turn 2's program calls.
    assert factory.creates == 1
    assert results["turn_1"]["ok"] is True
    assert results["turn_2"]["ok"] is True

    # The retained Turn-1 surface can never record into the finished Run
    # again: the proposal lands in Turn 2's collector, pinned to Run 2's id.
    run_1_candidate = hashlib.sha256(f"{run_1}:memory-candidate:1".encode()).hexdigest()[:12]
    run_2_candidate = hashlib.sha256(f"{run_2}:memory-candidate:1".encode()).hexdigest()[:12]
    assert run_1_candidate != run_2_candidate
    assert [candidate.candidate_id for candidate in first.outcome.memory_candidates] == [run_1_candidate]
    assert [candidate.candidate_id for candidate in second.outcome.memory_candidates] == [run_2_candidate]
    assert results["turn_2"]["candidate_id"] == run_2_candidate
    # Both collectors were drained by their own Turn's outcome; nothing lingers.
    assert collector_1.drain() == ()
    assert collector_2.drain() == ()


class _LocalProcess:
    """In-process ``sandbox.process`` shim executing the workspace agent locally."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def code_run(self, code: str, **_kwargs):
        self.calls.append(code)
        output = StringIO()
        with redirect_stdout(output), suppress(SystemExit):
            exec(code, {})
        return SimpleNamespace(exit_code=0, result=output.getvalue().strip())


class _Sink:
    async def read(self, location, *, max_bytes):
        del location, max_bytes
        return b""

    async def write(self, location, data):
        del location, data
        return None

    async def remove(self, location):
        del location
        return None

    async def write_private(self, location, data):
        del location, data
        return None

    async def remove_private(self, location):
        del location
        return None


class _Attachments:
    async def prepare_run(self, access, ids, run, sink):
        del access, ids, run, sink
        return PreparedAttachments((), ())


class _RecordingStream:
    """Recording ``RunEventStream`` wrapper implementing BOTH taint/committed marks.

    ``chat/turn_runtime.py::_mark_stream_runtime`` duck-types
    ``getattr(stream, "mark_tainted"|"mark_committed")``; this double records
    each mark and forwards to the real stream so the seam can never be
    silently satisfied.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.committed_marks = 0
        self.taint_marks = 0

    @property
    def outcome(self):
        return self._inner.outcome

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._inner.__anext__()

    def defer_runtime_release(self) -> None:
        self._inner.defer_runtime_release()

    def mark_committed(self) -> None:
        self.committed_marks += 1
        self._inner.mark_committed()

    def mark_tainted(self) -> None:
        self.taint_marks += 1
        self._inner.mark_tainted()

    async def release_runtime(self) -> None:
        await self._inner.release_runtime()

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def wait_owned(self) -> None:
        await self._inner.wait_owned()


class _RecordingRunner:
    """Runner wrapper recording every stream it hands to ``TurnRuntime``."""

    def __init__(self, inner: RLMRunner) -> None:
        self._inner = inner
        self.streams: list[_RecordingStream] = []

    def stream(self, execution) -> _RecordingStream:
        stream = _RecordingStream(self._inner.stream(execution))
        self.streams.append(stream)
        return stream

    async def aclose(self) -> None:
        await self._inner.aclose()


@pytest.mark.asyncio
async def test_runtime_plane_restart_restores_history_workspace_and_memory(tmp_path: Path) -> None:
    """Full runtime-plane restart over the same DB/volume (P52.6(b)).

    Process 1 commits Turn 1 through the full stack (``TurnRuntime.open`` →
    ``DefaultRunPreparer`` → ``RLMRunner`` → SQL store), writing a Session
    Workspace file, a Workspace Memory record, and one arbitrary interpreter
    global. The whole runtime plane (engine, store, registry, runner,
    preparer, coordinator) is then closed and dropped.

    Process 2 rebuilds every object over the SAME SQLite file and host volume
    and opens Turn 2: the restored History must contain Turn 1's record, the
    workspace file and memory record must be readable through freshly prepared
    tools, and the interpreter must be a NEW object with an empty namespace.
    """

    def build_volume_faces():
        sandbox = SimpleNamespace(process=_LocalProcess())
        workspace_fs = DaytonaSessionWorkspaceFS(
            sandbox,
            volume_root=str(volume_root),
            root=str(workspace_root),
            max_file_bytes=4096,
        )
        memory_session = AgentStorageSession(
            sandbox,
            volume_root=str(volume_root),
            root=str(volume_root),
            max_file_bytes=262_144,
            allow_volume_root=True,
        )
        memory_store = build_workspace_memory_store(WorkspaceMemoryStorage(memory_session), max_upload_bytes=262_144)
        return workspace_fs, memory_store

    class _Capabilities:
        def __init__(self, workspace_fs, memory_store) -> None:
            workspace_host = WorkspaceToolHost(workspace_fs, max_file_bytes=4096)
            memory_host = WorkspaceMemoryToolHost(memory_store)
            self.spec = RLMExecutionSpec(
                tools=(*workspace_host.as_tools(), *memory_host.as_tools()),
                tool_event_views={**workspace_host.event_views(), **memory_host.event_views()},
                workspace=DAYTONA_WORKSPACE_CAPABILITY,
            )
            self._memory_store = memory_store

        @property
        def workspace_memory_digest(self) -> str:
            return self._memory_store.read_tail(byte_budget=4096).content

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        def drain_memory_candidates(self):
            return ()

        async def aclose(self):
            return None

    class _Factory:
        def __init__(self, interpreter_box: list[_Interpreter]) -> None:
            self._interpreter_box = interpreter_box
            self.tools: dict[str, object] = {}
            self.calls: list[dict[str, object]] = []
            self.answers: list[str] = []

        def create(self, **kwargs):
            self.tools = {str(tool.name): tool for tool in kwargs.get("tools") or ()}
            factory = self

            class Program:
                async def acall(self, **call_kwargs):
                    factory.calls.append(call_kwargs)
                    interpreter = factory._interpreter_box[0]
                    tools = factory.tools
                    if call_kwargs["request"] == "remember the release rule":
                        # Process-local REPL global: must NOT survive restart.
                        interpreter.namespace["ephemeral_global"] = "process-one-only"
                        written = tools["write_workspace_text"](
                            path="notes/decision.md", content="durable decision v1", overwrite=False
                        )
                        assert written["ok"] is True
                        remembered = tools["update_workspace_memory"](
                            key_learning="Keep release notes concise.", category="General"
                        )
                        assert remembered["ok"] is True
                        factory.answers.append("turn-one-answer")
                        return dspy.Prediction(answer="turn-one-answer", trajectory=[])
                    # Turn 2 (post-restart): arbitrary Python globals are gone.
                    assert interpreter.namespace == {}
                    history = call_kwargs["history"]
                    assert type(history) is dspy.History
                    read = tools["read_workspace_text"](path="notes/decision.md", max_chars=100)
                    memory = tools["read_workspace_memory"]()
                    digest = call_kwargs["session_context"]["workspace_memory"]["tail"]
                    answer = "|".join(
                        (
                            str(history.messages[-1]["answer"]),
                            str(read["content"]),
                            str(memory["content"]),
                            digest,
                        )
                    )
                    factory.answers.append(answer)
                    return dspy.Prediction(answer=answer, trajectory=[])

            return Program()

    access = TurnAccess(uuid4(), uuid4())
    session_id = uuid4()
    db_path = tmp_path / "state.db"
    volume_root = tmp_path / "volume"
    workspace_root = volume_root / "sessions" / str(session_id) / "workspace"
    workspace_root.mkdir(parents=True)

    async def seed_schema(engine) -> None:
        await create_tables(engine)
        factory = create_session_factory(engine)
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                    SessionRow(
                        id=session_id,
                        user_id=access.user_id,
                        workspace_id=access.workspace_id,
                        title="restart",
                    ),
                )
            )

    async def open_and_drain(coordinator: TurnRuntime, command: OpenTurnCommand):
        opened = await coordinator.open(command)
        return [event async for event in opened]

    # ------------------------------ process 1 ------------------------------
    interpreter_box_1 = [_Interpreter()]
    engine_1 = create_async_engine_from_url(f"sqlite+aiosqlite:///{db_path}")
    await seed_schema(engine_1)
    store_1 = SqlAlchemyRunStateStore(create_session_factory(engine_1))
    workspace_fs_1, memory_store_1 = build_volume_faces()

    class _Environments:
        def __init__(self, interpreter_box: list[_Interpreter]) -> None:
            self._interpreter_box = interpreter_box

        async def acquire(self, run, *, deadline):
            del run, deadline

            async def release():
                return None

            return RunEnvironment(self._interpreter_box[0], _Sink(), _Sink(), release)

    class _CapabilityFactory:
        def __init__(self, workspace_fs, memory_store) -> None:
            self._workspace_fs = workspace_fs
            self._memory_store = memory_store

        async def prepare(self, run, environment, attachments, *, deadline):
            del run, environment, attachments, deadline
            return _Capabilities(self._workspace_fs, self._memory_store)

    registry_1 = SessionRLMRegistry()
    runner_1 = _RecordingRunner(RLMRunner(factory=_Factory(interpreter_box_1), runtime_registry=registry_1))
    coordinator_1 = TurnRuntime(
        lifecycle=RunLifecycleService(store_1, max_artifact_bytes=4096),
        preparation=DefaultRunPreparer(
            models=RLMModelBundle(object(), object()),
            options=RLMOptions(),
            attachments=_Attachments(),
            environments=_Environments(interpreter_box_1),
            capabilities=_CapabilityFactory(workspace_fs_1, memory_store_1),
            session_runtime_registry=registry_1,
        ),
        runner=runner_1,
    )
    events_1 = await open_and_drain(
        coordinator_1,
        OpenTurnCommand(access, session_id, TurnInput("remember the release rule"), "key-1", uuid4()),
    )
    assert any(isinstance(event.detail, RunCompleted) for event in events_1)
    assert (workspace_root / "notes" / "decision.md").read_text(encoding="utf-8") == "durable decision v1"
    assert "Keep release notes concise." in (volume_root / "memory" / "MEMORIES.md").read_text(encoding="utf-8")
    # The committed Turn marked the resident runtime committed, never tainted.
    assert [(stream.committed_marks, stream.taint_marks) for stream in runner_1.streams] == [(1, 0)]

    # Simulate process exit: close the whole runtime plane and the DB engine.
    await runner_1.aclose()
    await registry_1.shutdown()
    await engine_1.dispose()
    del store_1, workspace_fs_1, memory_store_1, registry_1, coordinator_1

    # ------------------------------ process 2 ------------------------------
    interpreter_box_2 = [_Interpreter()]
    engine_2 = create_async_engine_from_url(f"sqlite+aiosqlite:///{db_path}")
    store_2 = SqlAlchemyRunStateStore(create_session_factory(engine_2))
    workspace_fs_2, memory_store_2 = build_volume_faces()
    registry_2 = SessionRLMRegistry()
    factory_2 = _Factory(interpreter_box_2)
    runner_2 = _RecordingRunner(RLMRunner(factory=factory_2, runtime_registry=registry_2))
    coordinator_2 = TurnRuntime(
        lifecycle=RunLifecycleService(store_2, max_artifact_bytes=4096),
        preparation=DefaultRunPreparer(
            models=RLMModelBundle(object(), object()),
            options=RLMOptions(),
            attachments=_Attachments(),
            environments=_Environments(interpreter_box_2),
            capabilities=_CapabilityFactory(workspace_fs_2, memory_store_2),
            session_runtime_registry=registry_2,
        ),
        runner=runner_2,
    )
    try:
        events_2 = await open_and_drain(
            coordinator_2,
            OpenTurnCommand(access, session_id, TurnInput("recall the rule"), "key-2", uuid4()),
        )
    finally:
        await runner_2.aclose()
        await registry_2.shutdown()
        await engine_2.dispose()

    assert any(isinstance(event.detail, RunCompleted) for event in events_2)
    assert [(stream.committed_marks, stream.taint_marks) for stream in runner_2.streams] == [(1, 0)]

    # Durable History restored from the SQL store: Turn 2's program received
    # Turn 1's record through the fresh preparation.
    assert len(factory_2.calls) == 1
    restored_history = factory_2.calls[0]["history"]
    assert type(restored_history) is dspy.History
    assert list(restored_history.messages) == [{"request": "remember the release rule", "answer": "turn-one-answer"}]

    # Workspace Memory digest restored from the durable volume store.
    digest = factory_2.calls[0]["session_context"]["workspace_memory"]["tail"]
    assert "Keep release notes concise." in digest

    # Arbitrary Python globals are gone: a NEW interpreter object served
    # Turn 2 (the in-program assertion pinned an empty namespace).
    assert interpreter_box_2[0] is not interpreter_box_1[0]
    assert interpreter_box_2[0].namespace == {}
    assert interpreter_box_1[0].namespace == {"ephemeral_global": "process-one-only"}

    # The Turn-2 answer derives from the RESTORED durable state (history +
    # workspace file + memory record), proving content-level restoration.
    assert factory_2.calls[0]["request"] == "recall the rule"
    assert len(factory_2.answers) == 1
    restored_answer = factory_2.answers[0]
    assert restored_answer.startswith("turn-one-answer|durable decision v1|")
    assert "Keep release notes concise." in restored_answer
