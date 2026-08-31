"""P45 Runner integration contracts for resident Session state."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import dspy
import pytest

from fleet_rlm.attachments.models import PreparedAttachments
from fleet_rlm.chat.preparation import DefaultRunPreparer, PreparedTurn, RunEnvironment
from fleet_rlm.chat.run_claim import ClaimFailure, FailClaim
from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim
from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.persistence.repositories.turns import InMemoryRunStateStore
from fleet_rlm.rlm.events import RuntimeEvent
from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
from fleet_rlm.rlm.result import RLMOutcome, empty_rlm_usage
from fleet_rlm.rlm.runtime import (
    ExecutionRuntime,
    RLMExecutionContext,
    RLMExecutionSpec,
    RLMRunner,
    RunEventStream,
    RunIdentity,
    SessionView,
    program_fingerprint_for_context,
)
from fleet_rlm.rlm.session_runtime import SessionKey, SessionRLMRegistry, SessionRLMState
from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
from fleet_rlm.sessions.models import AssistantTurnRecord, TurnAccess, TurnInput, UserTurnRecord
from tests.unit.backend.rlm.fakes import EmptyCapabilities


class _Interpreter:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.output_fields: dict[str, object] = {}
        self.namespace: dict[str, object] = {}
        self.close_calls = 0


class _Program:
    def __init__(self, thread_ids: list[int], interpreter: _Interpreter) -> None:
        self.thread_ids = thread_ids
        self.interpreter = interpreter
        self.calls = 0
        self.histories: list[dspy.History] = []

    async def acall(self, **kwargs: object) -> dspy.Prediction:
        self.calls += 1
        history = kwargs.get("history")
        assert type(history) is dspy.History
        self.histories.append(history)
        if self.calls == 1:
            self.interpreter.namespace["persisted_marker"] = "clean-turn"
        else:
            assert self.interpreter.namespace["persisted_marker"] == "clean-turn"
        self.thread_ids.append(threading.get_ident())
        return dspy.Prediction(answer=f"answer-{self.calls}", trajectory=[])


class _SubAwareProgram(_Program):
    """Resident program double that exposes DSPy's mutable ``sub_lm`` seam."""

    def __init__(self, thread_ids: list[int], interpreter: _Interpreter, sub_lm: object) -> None:
        super().__init__(thread_ids, interpreter)
        self.sub_lm = sub_lm
        self.seen_sub_lms: list[object] = []

    async def acall(self, **kwargs: object) -> dspy.Prediction:
        self.seen_sub_lms.append(self.sub_lm)
        return await super().acall(**kwargs)


class _Factory:
    def __init__(self, thread_ids: list[int], interpreter: _Interpreter) -> None:
        self.thread_ids = thread_ids
        self.interpreter = interpreter
        self.programs: list[_Program] = []

    def create(self, **_kwargs: object) -> _Program:
        program = _Program(self.thread_ids, self.interpreter)
        self.programs.append(program)
        return program


class _SubAwareFactory:
    def __init__(self, thread_ids: list[int], interpreter: _Interpreter) -> None:
        self.thread_ids = thread_ids
        self.interpreter = interpreter
        self.programs: list[_SubAwareProgram] = []

    def create(self, **kwargs: object) -> _SubAwareProgram:
        models = cast(Any, kwargs["models"])
        program = _SubAwareProgram(self.thread_ids, self.interpreter, models.sub_lm)
        self.programs.append(program)
        return program


def _context(
    session_id,
    workspace_id,
    interpreter,
    request: str,
    run_id,
    history: dspy.History,
    *,
    models: RLMModelBundle | None = None,
) -> RLMExecutionContext:
    return RLMExecutionContext(
        identity=RunIdentity(run_id=run_id, session_id=session_id, access=TurnAccess(uuid4(), workspace_id)),
        session=SessionView(
            request=request,
            session_context=SessionContextManifest(session_id, 0, 0, ()),
            attachments=(),
            history=history,
        ),
        execution=ExecutionRuntime(
            models=models or RLMModelBundle(object(), object()),
            options=RLMOptions(),
            interpreter=interpreter,
            cancellation_requested=lambda: _not_cancelled(),
            deadline=10**12,
        ),
        capabilities=cast(Any, EmptyCapabilities()),
    )


async def _not_cancelled() -> bool:
    return False


@pytest.mark.asyncio
async def test_runner_preserves_an_explicit_empty_runtime_registry() -> None:
    registry = SessionRLMRegistry()
    runner = RLMRunner(runtime_registry=registry)
    assert runner._runtime_registry is registry
    await registry.shutdown()


@pytest.mark.asyncio
async def test_successful_sequential_streams_reuse_program_interpreter_and_thread() -> None:
    session_id, workspace_id = uuid4(), uuid4()
    interpreter = _Interpreter()
    thread_ids: list[int] = []
    factory = _Factory(thread_ids, interpreter)
    runner = RLMRunner(factory=factory)
    first_history = dspy.History(messages=[{"request": "prior", "answer": "stored"}])
    second_history = dspy.History(
        messages=[
            {"request": "prior", "answer": "stored"},
            {"request": "first", "answer": "answer-1"},
        ]
    )

    first = runner.stream(_context(session_id, workspace_id, interpreter, "first", uuid4(), first_history))
    _ = [event async for event in first]
    assert first.outcome is not None and first.outcome.succeeded
    first.mark_committed()
    await first.aclose()

    second = runner.stream(_context(session_id, workspace_id, interpreter, "second", uuid4(), second_history))
    _ = [event async for event in second]
    assert second.outcome is not None and second.outcome.succeeded
    second.mark_committed()
    await second.aclose()

    assert len(factory.programs) == 1
    assert factory.programs[0].calls == 2
    assert factory.programs[0].histories == [first_history, second_history]
    assert factory.programs[0].histories[0] is first_history
    assert factory.programs[0].histories[1] is second_history
    assert len(thread_ids) == 2
    assert thread_ids[0] == thread_ids[1]
    session_key = next(iter(runner._session_tool_registries))
    assert runner._session_tool_registries[session_key].active_run_id is None


@pytest.mark.asyncio
async def test_reused_resident_program_receives_each_turn_sub_lm_copy() -> None:
    """A resident RLM must never retain the previous Turn's deadline-bound Sub LM."""
    session_id, workspace_id = uuid4(), uuid4()
    interpreter = _Interpreter()
    thread_ids: list[int] = []
    factory = _SubAwareFactory(thread_ids, interpreter)
    runner = RLMRunner(factory=factory)
    first_sub = SimpleNamespace(model="sub", kwargs={})
    second_sub = SimpleNamespace(model="sub", kwargs={})
    root = SimpleNamespace(model="root", kwargs={})

    first = runner.stream(
        _context(
            session_id,
            workspace_id,
            interpreter,
            "first",
            uuid4(),
            dspy.History(messages=[]),
            models=RLMModelBundle(root, first_sub),
        )
    )
    _ = [event async for event in first]
    assert first.outcome is not None and first.outcome.succeeded
    first.mark_committed()
    await first.aclose()

    second = runner.stream(
        _context(
            session_id,
            workspace_id,
            interpreter,
            "second",
            uuid4(),
            dspy.History(messages=[]),
            models=RLMModelBundle(root, second_sub),
        )
    )
    _ = [event async for event in second]
    assert second.outcome is not None and second.outcome.succeeded
    second.mark_committed()
    await second.aclose()

    assert len(factory.programs) == 1
    program = factory.programs[0]
    assert program.seen_sub_lms == [first_sub, second_sub]
    await runner.aclose()


@pytest.mark.asyncio
async def test_uncommitted_stream_taints_before_next_session_turn() -> None:
    session_id, workspace_id = uuid4(), uuid4()
    interpreter = _Interpreter()
    factory = _Factory([], interpreter)
    runner = RLMRunner(factory=factory)

    first = runner.stream(_context(session_id, workspace_id, interpreter, "first", uuid4(), dspy.History(messages=[])))
    _ = [event async for event in first]
    await first.aclose()

    second = runner.stream(
        _context(session_id, workspace_id, interpreter, "second", uuid4(), dspy.History(messages=[]))
    )
    _ = [event async for event in second]
    second.mark_committed()
    await second.aclose()

    assert len(factory.programs) == 2


@pytest.mark.asyncio
async def test_pruned_registry_generation_restarts_with_fresh_runtime_identities() -> None:
    """A pruned coordination key may restart its local generation counter."""
    key = SessionKey(str(uuid4()), str(uuid4()))
    created: list[SessionRLMState] = []

    async def factory(state_key: SessionKey, fingerprint: str) -> SessionRLMState:
        state = SessionRLMState(state_key, fingerprint, object(), object())
        created.append(state)
        return state

    registry = SessionRLMRegistry(factory)
    try:
        first = await registry.acquire(key, "fingerprint")
        await registry.close(first)
        second = await registry.acquire(key, "fingerprint")

        assert first.generation == 1
        assert second.generation == 1
        assert second is not first
        assert second.rlm is not first.rlm
        assert second.interpreter is not first.interpreter
        assert len(created) == 2
    finally:
        await registry.shutdown()


# ---------------------------------------------------------------------------
# P52 Runner-level taint / fingerprint / history behavior
# ---------------------------------------------------------------------------


class _FreshInterpreter:
    """Closable caller-owned interpreter double with a persistent namespace."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.output_fields: dict[str, object] = {}
        self.namespace: dict[str, object] = {}
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _TurnProgram:
    """Program double recording calls and acting on its paired resident interpreter."""

    def __init__(self, interpreter: _FreshInterpreter, effect: Callable[[_TurnProgram], None] | None) -> None:
        self.interpreter = interpreter
        self.calls = 0
        self.histories: list[dspy.History] = []
        self._effect = effect

    async def acall(self, **kwargs: object) -> dspy.Prediction:
        self.calls += 1
        history = kwargs.get("history")
        assert type(history) is dspy.History
        self.histories.append(history)
        if self._effect is not None:
            self._effect(self)
        return dspy.Prediction(answer=f"answer-{self.calls}", trajectory=[])


class _FreshNamespaceFactory:
    """Factory double: each resident build pairs one fresh interpreter with one program.

    Production shape (``TestingRunEnvironmentProvider``): every Turn carries an
    interpreter and a rotation hands the new resident state a fresh one whose
    namespace is empty.  ``pending_interpreter`` exposes the interpreter the
    NEXT ``create`` will own so the caller can place the same object in the
    Turn context, mirroring how ``SessionRLMState.interpreter`` binds the
    resident program to its caller-owned interpreter.
    """

    def __init__(self, effects: Mapping[int, Callable[[_TurnProgram], None]] | None = None) -> None:
        self._effects = dict(effects) if effects else {}
        self.programs: list[_TurnProgram] = []
        self.interpreters: list[_FreshInterpreter] = []
        self._pending: _FreshInterpreter | None = None

    def pending_interpreter(self) -> _FreshInterpreter:
        """Return the interpreter the next resident build will own."""
        if self._pending is None:
            self._pending = _FreshInterpreter()
        return self._pending

    def create(self, **_kwargs: object) -> _TurnProgram:
        build = len(self.programs)
        interpreter = self.pending_interpreter()
        self._pending = None
        self.interpreters.append(interpreter)
        program = _TurnProgram(interpreter, self._effects.get(build))
        self.programs.append(program)
        return program


class _RecordingStream:
    """Recording stream double implementing BOTH duck-typed settlement methods.

    ``chat.turn_runtime._mark_stream_runtime`` reaches the stream through
    ``getattr(stream, "mark_committed" | "mark_tainted")`` and existing
    coordinator doubles silently satisfy that seam.  This double records each
    mark AND delegates to the real ``RunEventStream`` so the registry lease
    semantics stay live.
    """

    def __init__(self, stream: RunEventStream) -> None:
        self._stream = stream
        self.marks: list[str] = []

    def __aiter__(self) -> _RecordingStream:
        return self

    async def __anext__(self) -> RuntimeEvent:
        return await self._stream.__anext__()

    @property
    def outcome(self) -> RLMOutcome | None:
        return self._stream.outcome

    def mark_committed(self) -> None:
        self.marks.append("committed")
        self._stream.mark_committed()

    def mark_tainted(self) -> None:
        self.marks.append("tainted")
        self._stream.mark_tainted()

    async def aclose(self) -> None:
        await self._stream.aclose()


def _turn_context(
    session_id: UUID,
    workspace_id: UUID,
    interpreter: _FreshInterpreter,
    request: str,
    run_id: UUID,
    history: dspy.History,
    *,
    spec: RLMExecutionSpec | None = None,
    models: RLMModelBundle | None = None,
    options: RLMOptions | None = None,
) -> RLMExecutionContext:
    return RLMExecutionContext(
        identity=RunIdentity(run_id=run_id, session_id=session_id, access=TurnAccess(uuid4(), workspace_id)),
        session=SessionView(
            request=request,
            session_context=SessionContextManifest(session_id, 0, 0, ()),
            attachments=(),
            history=history,
        ),
        execution=ExecutionRuntime(
            models=models if models is not None else RLMModelBundle(object(), object()),
            options=options if options is not None else RLMOptions(),
            interpreter=interpreter,
            cancellation_requested=lambda: _not_cancelled(),
            deadline=10**12,
        ),
        capabilities=cast(Any, EmptyCapabilities(spec=spec)),
    )


class _NullSink:
    """Sink double: staged bytes are accepted and never persisted."""

    async def read(self, location: str, *, max_bytes: int) -> bytes:
        del location, max_bytes
        return b""

    async def write(self, location: str, data: bytes) -> None:
        del location, data
        return None

    async def remove(self, location: str) -> None:
        del location
        return None

    async def write_private(self, location: str, data: bytes) -> None:
        del location, data
        return None

    async def remove_private(self, location: str) -> None:
        del location
        return None


class _NoAttachments:
    async def prepare_run(self, access: object, ids: object, run: object, sink: object) -> PreparedAttachments:
        del access, ids, run, sink
        return PreparedAttachments((), ())


class _SpecCapabilityFactory:
    """Capability factory double returning empty capabilities with a fixed spec."""

    def __init__(self, spec: RLMExecutionSpec) -> None:
        self._spec = spec

    async def prepare(
        self, turn: object, environment: object, attachments: object, *, deadline: float
    ) -> EmptyCapabilities:
        del turn, environment, attachments, deadline
        return EmptyCapabilities(spec=self._spec)


class _PendingInterpreterEnvironments:
    """Environment provider double handing each Turn the interpreter its resident build owns."""

    def __init__(self, factory: _FreshNamespaceFactory, sink: _NullSink) -> None:
        self._factory = factory
        self._sink = sink

    async def acquire(self, turn: ClaimedRun, *, deadline: float) -> RunEnvironment:
        del turn
        assert deadline > 0

        async def release() -> None:
            return None

        # ``release_is_resident=False`` keeps the interpreter Fleet-owned so a
        # rotation closes the rotated-out interpreter through the registry.
        return RunEnvironment(
            self._factory.pending_interpreter(),
            self._sink,
            self._sink,
            release,
            release_is_resident=False,
        )


async def _open_recorded_turn(
    store: InMemoryRunStateStore,
    preparer: DefaultRunPreparer,
    runner: RLMRunner,
    access: TurnAccess,
    session_id: UUID,
    text: str,
    idempotency_key: str,
) -> tuple[ClaimedRun, PreparedTurn, _RecordingStream]:
    """Drive one production claim -> prepare -> stream Turn and drain its events."""
    claim = await store.begin(RunClaim(access, session_id, TurnInput(text), idempotency_key, uuid4()))
    assert isinstance(claim, ClaimedRun)
    prepared = await preparer.prepare(claim, deadline=float("inf"))
    stream = _RecordingStream(runner.stream(prepared.execution))
    _ = [event async for event in stream]
    return claim, prepared, stream


@pytest.mark.asyncio
async def test_failed_turn_rotates_runtime_with_fresh_namespace() -> None:
    """P52.3(a): a failed Turn taints the resident runtime; the next Turn rotates to a fresh namespace."""
    session_id, workspace_id = uuid4(), uuid4()

    def fail_first_turn(program: _TurnProgram) -> None:
        # The failed Turn mutates the resident interpreter before raising.
        program.interpreter.namespace["failed_turn_marker"] = "dirty"
        raise RuntimeError("mid-execution failure")

    factory = _FreshNamespaceFactory({0: fail_first_turn})
    registry = SessionRLMRegistry()
    runner = RLMRunner(factory=factory, runtime_registry=registry)
    key = SessionKey(str(workspace_id), str(session_id))
    try:
        first = _RecordingStream(
            runner.stream(
                _turn_context(
                    session_id, workspace_id, factory.pending_interpreter(), "first", uuid4(), dspy.History(messages=[])
                )
            )
        )
        _ = [event async for event in first]
        assert first.outcome is not None
        assert first.outcome.terminal_status == "failed"
        assert first.outcome.public_error_message == "Turn failed"
        assert len(factory.programs) == 1
        # Settle the stream exactly as TurnRuntime does for a failed outcome.
        first.mark_tainted()
        await first.aclose()

        second = _RecordingStream(
            runner.stream(
                _turn_context(
                    session_id,
                    workspace_id,
                    factory.pending_interpreter(),
                    "second",
                    uuid4(),
                    dspy.History(messages=[]),
                )
            )
        )
        _ = [event async for event in second]
        assert second.outcome is not None and second.outcome.succeeded
        second.mark_committed()
        await second.aclose()

        assert first.marks == ["tainted"]
        assert second.marks == ["committed"]
        # The failed Turn's resident program and interpreter were retired, not reused.
        assert len(factory.programs) == 2
        first_program, second_program = factory.programs
        assert first_program.calls == 1
        assert second_program.calls == 1
        assert second_program is not first_program
        assert second_program.interpreter is not first_program.interpreter
        assert second_program.interpreter.namespace == {}
        # The failed Turn's mutation died with the rotated-out resident interpreter.
        assert first_program.interpreter.namespace == {"failed_turn_marker": "dirty"}
        assert first_program.interpreter.close_calls == 1
        state = registry.get(key)
        assert state is not None
        assert state.rlm is second_program
        assert state.interpreter is second_program.interpreter
        assert state.generation == 2
    finally:
        await runner.aclose()
        await registry.shutdown()


@pytest.mark.asyncio
async def test_next_turn_retains_durable_history_but_not_failed_turn_mutations() -> None:
    """P52.3(e): the next Turn keeps durable History but not the failed Turn's interpreter mutations."""
    session_id, workspace_id = uuid4(), uuid4()
    access = TurnAccess(uuid4(), workspace_id)
    store = InMemoryRunStateStore()
    await store.add_session(session_id, access)

    def reused_program_effect(program: _TurnProgram) -> None:
        if program.calls == 1:
            program.interpreter.namespace["ok_marker"] = "committed-turn"
            return
        # The second (failed) Turn mutates the reused resident namespace, then raises.
        program.interpreter.namespace["bad_marker"] = "failed-turn"
        raise RuntimeError("mid-execution failure")

    def rotated_program_effect(program: _TurnProgram) -> None:
        # A taint-rotated resident state must start from an empty namespace.
        assert program.interpreter.namespace == {}
        program.interpreter.namespace["ok_marker"] = "rotated-turn"

    factory = _FreshNamespaceFactory({0: reused_program_effect, 1: rotated_program_effect})
    registry = SessionRLMRegistry()
    runner = RLMRunner(factory=factory, runtime_registry=registry)
    closed_states: list[SessionRLMState] = []
    registry.add_close_observer(closed_states.append)
    preparer = DefaultRunPreparer(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=_NoAttachments(),
        environments=_PendingInterpreterEnvironments(factory, _NullSink()),
        capabilities=_SpecCapabilityFactory(RLMExecutionSpec()),
        session_runtime_registry=registry,
    )
    key = SessionKey(str(workspace_id), str(session_id))
    try:
        # Turn 1: succeeds and commits durably.
        claim1, prepared1, first = await _open_recorded_turn(
            store, preparer, runner, access, session_id, "turn one", "turn-1"
        )
        assert first.outcome is not None and first.outcome.succeeded
        receipt1 = await store.commit(
            claim1, CommittedTurn(1, (UsagePart(empty_rlm_usage()), TextPart("turn-one-answer"))), ()
        )
        assert receipt1.committed_turn.text == "turn-one-answer"
        first.mark_committed()
        await first.aclose()
        await prepared1.aclose()

        # Turn 2: reuses the resident program, mutates its namespace, then fails mid-execution.
        claim2, prepared2, second = await _open_recorded_turn(
            store, preparer, runner, access, session_id, "turn two", "turn-2"
        )
        assert second.outcome is not None and second.outcome.terminal_status == "failed"
        receipt2 = await store.transition_claim(
            claim2, FailClaim(ClaimFailure("failed", "execution_failed", "Turn failed"), empty_rlm_usage())
        )
        assert receipt2 is not None and receipt2.terminal_status == "failed"
        second.mark_tainted()
        await second.aclose()
        await prepared2.aclose()

        # The failed Turn ran on the SAME resident program and interpreter as Turn 1.
        assert len(factory.programs) == 1
        reused = factory.programs[0]
        assert reused.calls == 2
        assert reused.interpreter.namespace == {"ok_marker": "committed-turn", "bad_marker": "failed-turn"}
        assert len(reused.histories) == 2
        assert reused.histories[1] is prepared2.execution.session.history
        assert list(reused.histories[1].messages) == [{"request": "turn one", "answer": "turn-one-answer"}]

        # Turn 3: rotated resident state, durable History intact, failed mutation gone.
        _claim3, prepared3, third = await _open_recorded_turn(
            store, preparer, runner, access, session_id, "turn three", "turn-3"
        )
        assert third.outcome is not None and third.outcome.succeeded
        third.mark_committed()
        await third.aclose()
        await prepared3.aclose()

        assert first.marks == ["committed"]
        assert second.marks == ["tainted"]
        assert third.marks == ["committed"]

        # (i) The forwarded History contains exactly Turn 1's committed record.
        assert len(factory.programs) == 2
        rotated = factory.programs[1]
        assert rotated.calls == 1
        assert len(rotated.histories) == 1
        assert rotated.histories[0] is prepared3.execution.session.history
        assert list(rotated.histories[0].messages) == [{"request": "turn one", "answer": "turn-one-answer"}]
        # (ii) The rotated interpreter is a fresh object whose namespace lacks the failed mutation.
        assert rotated.interpreter is not reused.interpreter
        assert rotated.interpreter.namespace == {"ok_marker": "rotated-turn"}
        assert "bad_marker" not in rotated.interpreter.namespace
        assert reused.interpreter.close_calls == 1
        state = registry.get(key)
        assert state is not None
        assert state.rlm is rotated
        assert state.interpreter is rotated.interpreter
        # The rotation was taint-driven: the program fingerprint never changed.
        assert len(closed_states) == 1
        assert closed_states[0].tainted
        assert state.program_fingerprint == closed_states[0].program_fingerprint
        # (iii) Only Turn 1 has durable records; the failed Turn left none.
        records = await store.turn_records(session_id, access)
        assert len(records) == 2
        user_record, assistant_record = records
        assert isinstance(user_record, UserTurnRecord)
        assert isinstance(assistant_record, AssistantTurnRecord)
        assert user_record.input.text == "turn one"
        assert assistant_record.committed.text == "turn-one-answer"
    finally:
        await runner.aclose()
        await registry.shutdown()


@pytest.mark.asyncio
async def test_history_remains_after_fingerprint_rotation() -> None:
    """P52.4(f): durable History remains available after a fingerprint-driven rotation."""
    session_id, workspace_id = uuid4(), uuid4()
    access = TurnAccess(uuid4(), workspace_id)
    store = InMemoryRunStateStore()
    await store.add_session(session_id, access)

    factory = _FreshNamespaceFactory()
    registry = SessionRLMRegistry()
    runner = RLMRunner(factory=factory, runtime_registry=registry)
    closed_states: list[SessionRLMState] = []
    registry.add_close_observer(closed_states.append)
    environments = _PendingInterpreterEnvironments(factory, _NullSink())

    def make_preparer(options: RLMOptions) -> DefaultRunPreparer:
        return DefaultRunPreparer(
            models=RLMModelBundle(object(), object()),
            options=options,
            attachments=_NoAttachments(),
            environments=environments,
            capabilities=_SpecCapabilityFactory(RLMExecutionSpec()),
            session_runtime_registry=registry,
        )

    key = SessionKey(str(workspace_id), str(session_id))
    try:
        # Turn 1: commits durably under the first program configuration.
        claim1, prepared1, first = await _open_recorded_turn(
            store, make_preparer(RLMOptions()), runner, access, session_id, "turn one", "turn-1"
        )
        assert first.outcome is not None and first.outcome.succeeded
        await store.commit(claim1, CommittedTurn(1, (UsagePart(empty_rlm_usage()), TextPart("turn-one-answer"))), ())
        first.mark_committed()
        await first.aclose()
        await prepared1.aclose()

        # Turn 2: only the program limits change, so the resident runtime rotates.
        _claim2, prepared2, second = await _open_recorded_turn(
            store, make_preparer(RLMOptions(max_iters=7)), runner, access, session_id, "turn two", "turn-2"
        )
        assert second.outcome is not None and second.outcome.succeeded
        second.mark_committed()
        await second.aclose()
        await prepared2.aclose()

        # The real preparation wiring maps the one-dimension change to a new program identity.
        assert prepared1.program_fingerprint is not None
        assert prepared2.program_fingerprint is not None
        assert prepared1.program_fingerprint != prepared2.program_fingerprint

        # A rotation built a second resident program...
        assert len(factory.programs) == 2
        rotated = factory.programs[1]
        assert rotated is not factory.programs[0]
        assert rotated.interpreter is not factory.programs[0].interpreter
        # ...yet Turn 2 still received Turn 1's canonical durable record.
        assert len(rotated.histories) == 1
        assert rotated.histories[0] is prepared2.execution.session.history
        assert list(rotated.histories[0].messages) == [{"request": "turn one", "answer": "turn-one-answer"}]
        assert first.marks == ["committed"]
        assert second.marks == ["committed"]
        # Rotation was fingerprint-driven: both Turns committed cleanly.
        assert len(closed_states) == 1
        assert not closed_states[0].tainted
        state = registry.get(key)
        assert state is not None
        assert state.program_fingerprint != closed_states[0].program_fingerprint
        assert state.rlm is rotated
    finally:
        await runner.aclose()
        await registry.shutdown()


class _RequestSignatureV1(dspy.Signature):
    request: str = dspy.InputField(desc="The operator request.")
    answer: str = dspy.OutputField()


class _RequestSignatureV2(dspy.Signature):
    request: str = dspy.InputField(desc="The renamed and extended operator request text.")
    answer: str = dspy.OutputField()


def _lm_stub(model: str) -> Any:
    return SimpleNamespace(model=model, kwargs={"model": model})


def _lookup_tool(description: str) -> dspy.Tool:
    def lookup(key: str) -> str:
        return key

    return dspy.Tool(
        lookup,
        name="lookup",
        desc=description,
        args={"key": {"type": "string", "description": "The lookup key."}},
        arg_types={"key": str},
        arg_desc={"key": "The lookup key."},
    )


def _dimension_variants(dimension: str) -> tuple[RLMExecutionSpec, RLMExecutionSpec, RLMModelBundle, RLMModelBundle]:
    """Return (first, second) spec/models differing in exactly the named program dimension."""
    models = RLMModelBundle(object(), object())
    match dimension:
        case "signature_field":
            return (
                RLMExecutionSpec(signature=_RequestSignatureV1),
                RLMExecutionSpec(signature=_RequestSignatureV2),
                models,
                models,
            )
        case "skill_instructions":
            return (
                RLMExecutionSpec(skill_instructions=("Use the baseline skill.",)),
                RLMExecutionSpec(skill_instructions=("Use the upgraded reporting skill pack.",)),
                models,
                models,
            )
        case "model_config":
            spec = RLMExecutionSpec()
            return (
                spec,
                spec,
                RLMModelBundle(_lm_stub("openai/root-a"), object()),
                RLMModelBundle(_lm_stub("openai/root-b"), object()),
            )
        case "tool_schema_description":
            return (
                RLMExecutionSpec(tools=(_lookup_tool("Lookup values."),)),
                RLMExecutionSpec(tools=(_lookup_tool("Lookup values across the whole workspace."),)),
                models,
                models,
            )
        case _:
            raise AssertionError(f"unknown dimension {dimension}")


@pytest.mark.parametrize(
    "dimension",
    ["signature_field", "skill_instructions", "model_config", "tool_schema_description"],
)
@pytest.mark.asyncio
async def test_single_dimension_program_change_rotates_resident_state(dimension: str) -> None:
    """P52.4(b)-(e): a single-dimension program change rotates the resident state."""
    session_id, workspace_id = uuid4(), uuid4()

    def write_first_marker(program: _TurnProgram) -> None:
        program.interpreter.namespace["resident_marker"] = "turn-one"

    def assert_fresh_and_write_second_marker(program: _TurnProgram) -> None:
        # The rotated resident state must start from an empty namespace.
        assert program.interpreter.namespace == {}
        program.interpreter.namespace["resident_marker"] = "turn-two"

    first_spec, second_spec, first_models, second_models = _dimension_variants(dimension)
    factory = _FreshNamespaceFactory({0: write_first_marker, 1: assert_fresh_and_write_second_marker})
    registry = SessionRLMRegistry()
    runner = RLMRunner(factory=factory, runtime_registry=registry)
    closed_states: list[SessionRLMState] = []
    registry.add_close_observer(closed_states.append)
    key = SessionKey(str(workspace_id), str(session_id))
    try:
        first_context = _turn_context(
            session_id,
            workspace_id,
            factory.pending_interpreter(),
            "first",
            uuid4(),
            dspy.History(messages=[]),
            spec=first_spec,
            models=first_models,
        )
        first = _RecordingStream(runner.stream(first_context))
        _ = [event async for event in first]
        assert first.outcome is not None and first.outcome.succeeded
        first.mark_committed()
        await first.aclose()
        assert len(factory.programs) == 1

        second_context = _turn_context(
            session_id,
            workspace_id,
            factory.pending_interpreter(),
            "second",
            uuid4(),
            dspy.History(messages=[]),
            spec=second_spec,
            models=second_models,
        )
        second = _RecordingStream(runner.stream(second_context))
        _ = [event async for event in second]
        assert second.outcome is not None and second.outcome.succeeded
        second.mark_committed()
        await second.aclose()

        assert first.marks == ["committed"]
        assert second.marks == ["committed"]
        # The real context -> fingerprint wiring maps this one dimension to a new identity.
        assert program_fingerprint_for_context(first_context) != program_fingerprint_for_context(second_context)
        # A rotation built a second resident program with a fresh interpreter namespace.
        assert len(factory.programs) == 2
        first_program, second_program = factory.programs
        assert first_program.calls == 1
        assert second_program.calls == 1
        assert second_program is not first_program
        assert second_program.interpreter is not first_program.interpreter
        assert first_program.interpreter.namespace == {"resident_marker": "turn-one"}
        assert second_program.interpreter.namespace == {"resident_marker": "turn-two"}
        assert first_program.interpreter.close_calls == 1
        # Rotation was fingerprint-driven: both Turns committed cleanly.
        assert len(closed_states) == 1
        assert not closed_states[0].tainted
        state = registry.get(key)
        assert state is not None
        assert state.program_fingerprint != closed_states[0].program_fingerprint
        assert state.rlm is second_program
        assert state.interpreter is second_program.interpreter
    finally:
        await runner.aclose()
        await registry.shutdown()
