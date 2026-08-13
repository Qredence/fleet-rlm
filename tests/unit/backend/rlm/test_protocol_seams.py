"""Named Fleet Protocol fields: no getattr probes at the recursive, prepare, or bind seams."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
from fleet_rlm.chat.run_preparation import DefaultRunPreparer, PreparedRun, RunEnvironment
from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.files.memory_models import WORKSPACE_MEMORY_INJECTION_TAIL_BYTES
from fleet_rlm.files.models import PreparedAttachments
from fleet_rlm.rlm.context import ExecutionRuntime, RLMExecutionContext, RunIdentity, SessionView
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.inputs import AttachmentContextCapsule, AttachmentContextEntry
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.recursive_calls import RecursiveRLMExecutor, RecursiveRLMOptions
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
from tests.unit.backend.rlm.fakes import (
    EmptyCapabilities,
    FakeChildRuntimeFactory,
    FakeRLMInterpreter,
)


class _RecordingChildRuntimeFactory:
    def __init__(self) -> None:
        self.wait_calls = 0
        self.raise_calls = 0

    def __call__(self, call_index: int) -> Any:
        del call_index
        raise AssertionError("wait_owned must not acquire a child lease")

    def wait_owned(self) -> None:
        self.wait_calls += 1

    def raise_if_cleanup_failed(self) -> None:
        self.raise_calls += 1


class _RecordingInterpreter(FakeRLMInterpreter):
    def __init__(self) -> None:
        self.bind_calls = 0
        self.bound: AttachmentContextCapsule | None = None

    def bind_context_capsule(self, capsule: AttachmentContextCapsule) -> None:
        self.bind_calls += 1
        self.bound = capsule

    def drain_context_accesses(self) -> tuple[str, ...]:
        return ("notes.txt",)


class _RecordingCapabilities(EmptyCapabilities):
    def __init__(self) -> None:
        super().__init__()
        self.recorded: tuple[str, ...] | None = None

    def record_attachment_accesses(self, attachment_ids: tuple[str, ...]) -> None:
        self.recorded = attachment_ids


class _ImmediateAnswerFactory:
    def create(self, **_kwargs: Any) -> Any:
        class Program:
            async def acall(self, **_call_kwargs: Any) -> dspy.Prediction:
                return dspy.Prediction(answer="ok", trajectory=[])

        return Program()


async def _prepare(capabilities: EmptyCapabilities) -> PreparedRun:
    class Sink:
        async def remove_private(self, location: str) -> None:
            del location

    class Attachments:
        async def prepare_run(self, access: Any, ids: Any, run: Any, sink: Any) -> PreparedAttachments:
            del access, ids, run, sink
            return PreparedAttachments((), ())

    class Environments:
        async def acquire(self, turn: Any, *, deadline: float) -> RunEnvironment:
            del turn, deadline

            async def release() -> None:
                return None

            return RunEnvironment(None, Sink(), Sink(), release)

    class CapabilityFactory:
        async def prepare(self, turn: Any, environment: Any, attachments: Any, *, deadline: float) -> EmptyCapabilities:
            del turn, environment, attachments, deadline
            return capabilities

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("next"),
        SessionHistory(()),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    return await DefaultRunPreparer(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=Environments(),
        capabilities=CapabilityFactory(),
    ).prepare(turn, deadline=float("inf"))


def _executor(factory: Any) -> RecursiveRLMExecutor:
    return RecursiveRLMExecutor(
        models=RLMModelBundle(object(), object()),
        options=RecursiveRLMOptions(),
        child_runtime_factory=factory,
        deadline=time.monotonic() + 30,
    )


def _attachment_capsule() -> AttachmentContextCapsule:
    return AttachmentContextCapsule(
        (
            AttachmentContextEntry(
                attachment_id=uuid4(),
                filename="notes.txt",
                content_type="text/plain",
                byte_size=3,
                checksum_sha256="a" * 64,
                sandbox_path="/home/daytona/run/notes.txt",
            ),
        ),
        mount_root="/home/daytona/run",
    )


async def _stream_runner(
    *, interpreter: Any, attachment_context: AttachmentContextCapsule | None
) -> _RecordingCapabilities:
    async def not_cancelled() -> bool:
        return False

    capabilities = _RecordingCapabilities()
    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="use context",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
            attachment_context=attachment_context,
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=interpreter,
            cancellation_requested=not_cancelled,
        ),
        capabilities=capabilities,
    )
    stream = RLMRunner(factory=_ImmediateAnswerFactory()).stream(context)
    _events = [event async for event in stream]
    assert stream.outcome is not None and stream.outcome.succeeded
    return capabilities


def test_fake_child_runtime_factory_owns_noop_wait_and_raise() -> None:
    created: list[int] = []

    def create(call_index: int) -> object:
        created.append(call_index)
        return object()

    factory = FakeChildRuntimeFactory(create)
    factory.wait_owned()
    factory.raise_if_cleanup_failed()
    assert factory(4) is not None
    assert created == [4]


def test_executor_wait_owned_calls_factory_wait_owned() -> None:
    factory = _RecordingChildRuntimeFactory()
    executor = _executor(factory)
    executor.wait_owned()
    executor.raise_if_cleanup_failed()
    assert factory.wait_calls == 1
    assert factory.raise_calls == 1


def test_bare_create_callable_is_not_a_child_runtime_factory() -> None:
    def create(call_index: int) -> object:
        del call_index
        return object()

    with pytest.raises(AttributeError):
        _executor(create).wait_owned()  # type: ignore[arg-type]


def test_executor_wait_owned_accepts_fake_factory_wrapping_a_create_callable() -> None:
    def create(call_index: int) -> object:
        del call_index
        return object()

    executor = _executor(FakeChildRuntimeFactory(create))
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


@pytest.mark.asyncio
async def test_preparation_copies_protocol_workspace_memory_digest() -> None:
    capabilities = EmptyCapabilities()
    capabilities.workspace_memory_digest = "remember the cobalt-orchid code"
    prepared = await _prepare(capabilities)
    try:
        assert prepared.execution.session.workspace_memory_digest == "remember the cobalt-orchid code"
    finally:
        await prepared.aclose()


@pytest.mark.asyncio
async def test_preparation_keeps_digest_at_injection_byte_bound() -> None:
    capabilities = EmptyCapabilities()
    capabilities.workspace_memory_digest = "x" * WORKSPACE_MEMORY_INJECTION_TAIL_BYTES
    prepared = await _prepare(capabilities)
    try:
        assert prepared.execution.session.workspace_memory_digest == capabilities.workspace_memory_digest
    finally:
        await prepared.aclose()


@pytest.mark.asyncio
async def test_preparation_clamps_oversized_workspace_memory_digest() -> None:
    capabilities = EmptyCapabilities()
    capabilities.workspace_memory_digest = "x" * (WORKSPACE_MEMORY_INJECTION_TAIL_BYTES + 1)
    prepared = await _prepare(capabilities)
    try:
        assert prepared.execution.session.workspace_memory_digest == ""
    finally:
        await prepared.aclose()


@pytest.mark.asyncio
async def test_runner_binds_context_capsule_when_present() -> None:
    interpreter = _RecordingInterpreter()
    capsule = _attachment_capsule()
    capabilities = await _stream_runner(interpreter=interpreter, attachment_context=capsule)
    assert interpreter.bind_calls == 1
    assert interpreter.bound is capsule
    assert capabilities.recorded == ("notes.txt",)


@pytest.mark.asyncio
async def test_runner_skips_bind_context_capsule_when_attachment_context_is_none() -> None:
    interpreter = _RecordingInterpreter()
    await _stream_runner(interpreter=interpreter, attachment_context=None)
    assert interpreter.bind_calls == 0
    assert interpreter.bound is None
