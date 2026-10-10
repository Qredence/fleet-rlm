"""Prepared native-RLM program inputs, child workspace paths, and bounded Session context.

* ``test_program_inputs.py``: behavior contracts for program inputs.
* Child inputs are selected by relative Workspace path and materialized by the Turn host.
* ``test_session_context.py``: bounded Session context at the prepared native-RLM input seam.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import dspy
import pytest
from pydantic import SecretStr

import fleet_rlm.rlm.program as factory
from fleet_rlm.config.settings import Settings
from fleet_rlm.rlm.program import (
    TOOL_RLM_INSTRUCTIONS,
    WORKSPACE_MUTATION_RLM_INSTRUCTIONS,
    AttachmentContextCapsule,
    AttachmentContextEntry,
    AttachmentInput,
    FleetRLMSignature,
    RLMModelBundle,
    RLMOptions,
    SessionContextInput,
    SkillCardInput,
    build_rlm_input_kwargs,
    root_signature_for_recursion,
)
from fleet_rlm.rlm.result import RLMConfigError
from fleet_rlm.sessions.context import SessionContextManifest
from fleet_rlm.sessions.models import SessionHistory, TurnInput
from fleet_rlm.workspace.models import DAYTONA_WORKSPACE_CAPABILITY, UNAVAILABLE_WORKSPACE_CAPABILITY
from tests.support.native_rlm import build_native_rlm_for_test
from tests.support.rlm_inputs import ATTACHMENT_ID, SESSION_ID, SKILL_ID, _payload
from tests.support.role_lm import placeholder_bundle
from tests.support.turn_preparation import TestingRunPreparer


# --- from test_program_inputs.py --------------------------------------
def test_default_input_payload_contains_only_bounded_metadata() -> None:
    payload = _payload()

    assert set(payload) == {"request", "history", "session_context", "skill_cards", "attachments"}
    context = payload["session_context"]
    assert isinstance(context, dict)
    assert set(context) == {
        "session_id",
        "checkpoint_version",
        "message_count",
        "recent",
        "workspace",
    }
    assert set(context["recent"][0]) == {"ordinal", "role", "preview"}  # type: ignore[index]
    assert set(context["workspace"]) == {"available", "root", "instructions"}  # type: ignore[arg-type]
    assert set(payload["skill_cards"][0]) == {  # type: ignore[index]
        "id",
        "name",
        "description",
        "scope",
        "version",
        "trust",
        "affordances",
        "resources_available",
    }
    assert set(payload["attachments"][0]) == {  # type: ignore[index]
        "id",
        "filename",
        "content_type",
        "byte_size",
        "checksum_sha256",
    }


def test_strict_models_accept_the_authorized_metadata_shape() -> None:
    payload = _payload()
    context = payload["session_context"]
    assert isinstance(context, dict)

    validated_context = SessionContextInput.model_validate(
        {
            **context,
            "session_id": SESSION_ID,
            "recent": tuple(
                {
                    **item,
                }
                for item in context["recent"]  # type: ignore[index]
            ),
        },
        strict=True,
    )
    assert validated_context.session_id == SESSION_ID
    assert validated_context.workspace.root == "."

    card = SkillCardInput.model_validate(
        {
            **payload["skill_cards"][0],  # type: ignore[index]
            "id": SKILL_ID,
            "affordances": (),
        },
        strict=True,
    )
    attachment = AttachmentInput.model_validate(
        {
            **payload["attachments"][0],  # type: ignore[index]
            "id": ATTACHMENT_ID,
        },
        strict=True,
    )
    assert card.resources_available is True
    assert attachment.byte_size == 128


def test_invalid_request_fails_at_the_input_boundary() -> None:
    with pytest.raises(RLMConfigError, match="Turn input metadata is invalid"):
        build_rlm_input_kwargs(
            request="   ",
            session_context=SessionContextManifest(SESSION_ID, 0, 0, ()),
        )


@pytest.mark.asyncio
async def test_volume_attachment_context_round_trips_inside_the_interpreter(tmp_path: Path) -> None:
    import hashlib

    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
    from fleet_rlm.sessions.context import SessionContextManifest

    body = b"Fleet context"
    context_file = tmp_path / "report.txt"
    context_file.write_bytes(body)
    payload = AttachmentContextCapsule(
        (
            AttachmentContextEntry(
                ATTACHMENT_ID,
                "report.txt",
                "text/plain",
                len(body),
                hashlib.sha256(body).hexdigest(),
                str(context_file),
            ),
        ),
        mount_root=str(tmp_path),
    )
    kwargs = build_rlm_input_kwargs(
        request="inspect the prepared payload",
        history=dspy.History(messages=[]),
        session_context=SessionContextManifest(SESSION_ID, 0, 0, ()),
        attachment_context=payload,
    )
    lm = dspy.utils.DummyLM(
        [{"reasoning": "submit the context", "code": "SUBMIT(answer=context)"}],
        adapter=dspy.JSONAdapter(),
    )
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interpreter.bind_context_capsule(payload)
    rlm = dspy.RLM(
        FleetRLMSignature,
        max_iters=1,
    )

    with dspy.context(lm=lm, adapter=dspy.JSONAdapter()):
        prediction = await rlm.acall(interpreter_factory=lambda: interpreter, **kwargs)

    interpreter.shutdown()

    assert prediction.answer == "Fleet context"
    assert payload.rlm_preview(10) == "prepared i"
    assert "/home/daytona" not in payload.rlm_preview()
    assert body not in payload.to_sandbox()


def test_attachment_context_rejects_paths_outside_mount(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside"):
        AttachmentContextCapsule(
            (
                AttachmentContextEntry(
                    ATTACHMENT_ID,
                    "report.txt",
                    "text/plain",
                    1,
                    "a" * 64,
                    "/outside/report.txt",
                ),
            ),
            mount_root=str(tmp_path),
        )


def test_attachment_context_manifest_requires_the_host_bound_digest(tmp_path: Path) -> None:
    import hashlib

    from fleet_rlm.daytona.interpreter import InProcessInterpreterBackend
    from fleet_rlm.rlm.program import _materialize_context_manifest

    body = b"bound context"
    context_file = tmp_path / "report.txt"
    context_file.write_bytes(body)
    capsule = AttachmentContextCapsule(
        (
            AttachmentContextEntry(
                ATTACHMENT_ID,
                "report.txt",
                "text/plain",
                len(body),
                hashlib.sha256(body).hexdigest(),
                str(context_file),
            ),
        ),
        mount_root=str(tmp_path),
    )
    raw = capsule.to_sandbox()
    manifest_sha256 = hashlib.sha256(raw).hexdigest()

    values, accesses = _materialize_context_manifest(
        raw,
        trusted_mount_root=str(tmp_path),
        expected_manifest_sha256=manifest_sha256,
    )
    assert values[0]["data"] == "bound context"
    assert accesses == (str(ATTACHMENT_ID),)

    forged = json.loads(raw)
    forged["mount_root"] = "/"
    with pytest.raises(ValueError, match="context manifest is invalid"):
        _materialize_context_manifest(
            json.dumps(forged).encode(),
            trusted_mount_root=str(tmp_path),
            expected_manifest_sha256=manifest_sha256,
        )

    backend = InProcessInterpreterBackend()
    backend.bind_context_manifest(
        trusted_mount_root=str(tmp_path),
        expected_manifest_sha256=manifest_sha256,
    )
    forged_raw = json.dumps({**forged, "mount_root": "/"}).encode()
    forged_result = backend.run(
        "attachments = _fleet_load_context_manifest(_raw_attachments)",
        {"_raw_attachments": forged_raw},
    )
    assert forged_result.error == "context manifest is invalid"

    assignment = capsule.sandbox_assignment("attachments", "_raw_attachments")
    assert manifest_sha256 not in assignment
    assert str(tmp_path) not in assignment
    assert "del _fleet_load_context_manifest" in assignment


@pytest.mark.asyncio
async def test_attachment_context_integrity_failure_aborts_before_reasoning(tmp_path: Path) -> None:
    from fleet_rlm.daytona.errors import DaytonaAdapterError
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    context_file = tmp_path / "report.txt"
    context_file.write_text("changed", encoding="utf-8")
    capsule = AttachmentContextCapsule(
        (
            AttachmentContextEntry(
                ATTACHMENT_ID,
                "report.txt",
                "text/plain",
                7,
                "a" * 64,
                str(context_file),
            ),
        ),
        mount_root=str(tmp_path),
    )
    lm = dspy.utils.DummyLM(
        [{"reasoning": "must not run", "code": "SUBMIT(answer='bad')"}],
        adapter=dspy.JSONAdapter(),
    )
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interpreter.bind_context_capsule(capsule)
    rlm = dspy.RLM(
        "request, attachments -> answer: str",
        max_iters=1,
    )

    with (
        dspy.context(lm=lm, adapter=dspy.JSONAdapter()),
        pytest.raises(DaytonaAdapterError, match="prepared context failed integrity verification"),
    ):
        await rlm.acall(
            interpreter_factory=lambda: interpreter,
            request="inspect",
            attachments=capsule,
        )

    interpreter.shutdown()
    assert lm.history == []


_SESSION_ID = "00000000-0000-0000-0000-000000000001"


def _manifest():
    from fleet_rlm.sessions.context import SessionContextManifest

    return SessionContextManifest(
        session_id=__import__("uuid").UUID(_SESSION_ID),
        checkpoint_version=0,
        message_count=0,
        recent=(),
    )


# --- child input request validation -----------------------------------


# --- from test_session_context.py -------------------------------------
@pytest.mark.asyncio
async def test_prepared_rlm_kwargs_bound_a_large_session_to_recent_previews() -> None:
    from fleet_rlm.rlm.execution import RLMExecutionSpec, RLMRunner
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.sessions.models import HistoryMessage, TurnAccess
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )
    from fleet_rlm.turns.preparation import RunEnvironment
    from fleet_rlm.workspace.attachments import PreparedAttachments

    session_id = uuid4()
    messages = tuple(
        HistoryMessage(
            "user" if index % 2 == 0 else "assistant",
            f"message-{index + 1:03d}:" + chr(65 + index % 26) * 9_988,
        )
        for index in range(100)
    )

    class Sink:
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

    class Attachments:
        async def prepare_run(self, access, ids, run, sink):
            del access, ids, run, sink
            return PreparedAttachments((), ())

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        def drain_memory_candidates(self):
            return ()

        async def aclose(self):
            return None

    class CapabilityFactory:
        async def prepare(self, turn, environment, attachments, *, deadline):
            del turn, environment, attachments
            assert deadline > 0
            return Capabilities()

    sink = Sink()

    class Environments:
        async def acquire(self, turn, *, deadline):
            del turn, deadline

            async def release():
                return None

            return RunEnvironment(SimpleNamespace(), sink, sink, release)

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        uuid4(),
        session_id,
        TurnAccess(uuid4(), uuid4()),
        TurnInput("continue"),
        SessionHistory(messages),
        not_cancelled,
        _RunClaimToken(uuid4(), 7),
    )
    prepared = await TestingRunPreparer(
        models=placeholder_bundle(),
        options=RLMOptions(),
        attachments=Attachments(),
        acquire_environment=Environments().acquire,
        capabilities=CapabilityFactory(),
    ).prepare(turn, deadline=asyncio.get_running_loop().time() + 3600)

    class Factory:
        kwargs: dict[str, object] | None = None

        def create(self, **_kwargs):
            factory = self

            class Program:
                async def acall(self, **kwargs):
                    factory.kwargs = kwargs
                    return dspy.Prediction(answer="done")

            return Program()

    factory = Factory()
    stream = RLMRunner(program_builder=factory.create).stream(prepared.execution)
    async for _ in stream:
        pass

    assert factory.kwargs is not None
    # P44.3 production wiring: ``history`` is now a first-class RLM input
    # alongside the existing common fields. The set assertion still names
    # every input the Runner forwards; the new ``history`` key carries the
    # canonical committed Session conversation as a ``dspy.History``.
    assert set(factory.kwargs) == {
        "request",
        "session_context",
        "skill_cards",
        "attachments",
        "history",
    }
    manifest = factory.kwargs["session_context"]
    assert manifest == {
        "session_id": str(session_id),
        "checkpoint_version": 7,
        "message_count": 100,
        "recent": [
            {
                "ordinal": index + 1,
                "role": messages[index].role,
                "preview": messages[index].content[:320],
            }
            for index in range(94, 100)
        ],
        "workspace": {
            "available": False,
            "root": ".",
            "instructions": (
                "Session Workspace is unavailable. REPL variables and sandbox-local files are "
                "temporary to the Run; no durable Workspace or Turn Commit artifact workflow is available."
            ),
        },
    }
    assert all(len(item["preview"]) <= 320 for item in manifest["recent"])
    # The bounded payload surface (``session_context``) still does not
    # embed the full message bodies. The full bodies now live behind the
    # ``history`` key as a ``dspy.History`` instance, which is the
    # P44.1 first-class durable conversation and is expected to contain
    # them by design.
    bounded_subset = {key: factory.kwargs[key] for key in ("session_context", "skill_cards", "attachments")}
    encoded = json.dumps(bounded_subset, default=str)
    assert messages[0].content not in encoded
    assert messages[-1].content not in encoded
    # The canonical committed Session conversation IS the full bodies.
    history = factory.kwargs["history"]
    assert type(history) is dspy.History
    history_messages = list(history.messages)
    assert history_messages[0]["request"] == messages[0].content
    # The last paired record is the final user request and its assistant answer.
    # The test's 100 messages alternate user/assistant; only user→assistant
    # pairs enter the canonical conversation, so the last request corresponds
    # to the second-to-last message and the last answer to the last message.
    assert history_messages[-1]["request"] == messages[-2].content
    assert history_messages[-1]["answer"] == messages[-1].content
    assert prepared.execution.session.session_context.message_count == 100
    assert not hasattr(prepared.execution, "history")

    await prepared.aclose()


# --- from test_program_instructions.py --------------------------------
def test_workspace_mutation_instruction_requires_a_registered_write_tool() -> None:
    absent = root_signature_for_recursion(FleetRLMSignature, recursion_enabled=False).instructions
    present = root_signature_for_recursion(
        FleetRLMSignature,
        recursion_enabled=False,
        tool_names=frozenset({"append_workspace_text"}),
    ).instructions
    publish_only = root_signature_for_recursion(
        FleetRLMSignature,
        recursion_enabled=False,
        tool_names=frozenset({"publish_workspace_artifact"}),
    ).instructions

    assert WORKSPACE_MUTATION_RLM_INSTRUCTIONS not in absent
    assert WORKSPACE_MUTATION_RLM_INSTRUCTIONS in present
    assert WORKSPACE_MUTATION_RLM_INSTRUCTIONS in publish_only
    assert "named write or publish remains" in present
    assert "Files outside the mounted ``/workspace`` are not Session Workspace" in present


def test_tool_instructions_require_sandbox_research_and_bounded_precision() -> None:
    assert "SHA-256" in TOOL_RLM_INSTRUCTIONS
    assert "sys.executable -m pip" in TOOL_RLM_INSTRUCTIONS
    assert "smallest" in TOOL_RLM_INSTRUCTIONS and "guard band" in TOOL_RLM_INSTRUCTIONS
    assert "never recompute a cached prefix" in TOOL_RLM_INSTRUCTIONS
    assert "pass that string unchanged" in TOOL_RLM_INSTRUCTIONS
    assert "pass them unchanged and in the given order" in TOOL_RLM_INSTRUCTIONS
    assert "do not omit listed accumulator updates" in TOOL_RLM_INSTRUCTIONS
    assert "request as unused text" in TOOL_RLM_INSTRUCTIONS


def test_default_signature_orders_capabilities_before_semantic_calls() -> None:
    instructions = FleetRLMSignature.instructions
    ordered_markers = (
        "Python standard library",
        "Load Session History, Skills, Attachments, URL content, or Session Workspace content only",
        "llm_query(prompt)",
        "llm_query_batched(prompts)",
        "exactly one typed ``SUBMIT``",
    )
    positions = tuple(instructions.index(marker) for marker in ordered_markers)
    assert positions == tuple(sorted(positions))


def test_workspace_capability_declares_temporary_durable_and_commit_gated_state() -> None:
    daytona = DAYTONA_WORKSPACE_CAPABILITY.instructions
    unavailable = UNAVAILABLE_WORKSPACE_CAPABILITY.instructions

    for marker in ("REPL variables", "sandbox-local files", "immediately durable", "Turn Commit"):
        assert marker in daytona
    assert "unavailable" in unavailable
    assert "REPL variables" in unavailable


def test_native_builder_threads_host_tool_dispatch_into_the_signature() -> None:
    options = RLMOptions(max_iters=1, max_llm_calls=1)
    without = build_native_rlm_for_test(signature=FleetRLMSignature, options=options, host_tool_dispatch=False)
    with_dispatch = build_native_rlm_for_test(signature=FleetRLMSignature, options=options, host_tool_dispatch=True)

    assert "Fleet recursion or Workspace host tool" in without.signature.instructions
    assert "Fleet recursion or Workspace host tool" not in with_dispatch.signature.instructions


def test_nondefault_observation_budget_preserves_the_original_signature() -> None:
    program = build_native_rlm_for_test(
        signature=FleetRLMSignature,
        options=RLMOptions(max_iters=1, max_llm_calls=1, max_output_chars=6_000),
    )

    assert program.signature is FleetRLMSignature


# --- from test_program_factory.py -------------------------------------
class _CopyableLM:
    """Minimal copyable role-LM double: isolates history and records call kwargs."""

    def __init__(self) -> None:
        self.history: list[object] = []
        self.calls: list[dict[str, object]] = []

    def copy(self) -> _CopyableLM:
        return _CopyableLM()

    def forward(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return object()


def test_model_bundle_forks_isolated_child_lms() -> None:
    root = _CopyableLM()
    sub = _CopyableLM()
    bundle = RLMModelBundle(root, sub)

    first = bundle.fork_for_child()
    second = bundle.fork_for_child()

    assert first.root_lm is not root
    assert first.sub_lm is not sub
    assert first.root_lm is not second.root_lm
    assert first.sub_lm is not second.sub_lm
    assert first.root_lm.history is not second.root_lm.history
    assert not hasattr(root, "_fleet_can_finalize")
    assert not hasattr(sub, "_fleet_can_finalize")


@pytest.mark.asyncio
async def test_turn_bound_lm_preserves_dspy_sync_async_usage_and_callbacks():
    from collections.abc import AsyncIterator, Iterator
    from typing import Any

    from dspy.clients.engines.base import validate_request
    from dspy.lm15 import Request, Response, response_from_openai_chat, response_to_events
    from dspy.utils.callback import BaseCallback

    class Callback(BaseCallback):
        def __init__(self):
            self.starts = []

        def on_lm_start(self, call_id, instance, inputs):
            del call_id, inputs
            self.starts.append(instance.model)

    class OkEngine:
        def complete(self, request: Request) -> Response:
            validate_request(request)
            return response_from_openai_chat(
                {
                    "model": "test/script",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                }
            )

        def stream(self, request: Request) -> Iterator[Any]:
            return response_to_events(self.complete(request))

        def close(self) -> None:
            pass

    class AsyncOkEngine:
        def __init__(self, sync: OkEngine) -> None:
            self.sync = sync

        async def complete(self, request: Request) -> Response:
            return self.sync.complete(request)

        async def stream(self, request: Request) -> AsyncIterator[Any]:
            for event in self.sync.stream(request):
                yield event

        async def aclose(self) -> None:
            pass

    sync_engine = OkEngine()
    callback = Callback()
    source = dspy.LM(
        "test/script",
        model_type="chat",
        cache=False,
        engine=sync_engine,
        async_engine=AsyncOkEngine(sync_engine),
        callbacks=[callback],
    )
    bound = RLMModelBundle(source, source).bind_turn().root_lm
    assert bound("sync") == ["ok"]
    assert await bound.acall("async") == ["ok"]
    assert len(bound.history) == 2
    assert bound.history[-1]["usage"]["total_tokens"] == 3
    assert source.history == []
    assert callback.starts == ["test/script", "test/script"]


def test_turn_binding_isolates_role_copies_without_lm_policy_attributes() -> None:
    from fleet_rlm.rlm.budget import TurnBudget

    root = _CopyableLM()
    sub = _CopyableLM()
    source = RLMModelBundle(root, sub)
    budget = TurnBudget(deadline=None)
    bound = source.bind_turn(budget=budget)

    bound.root_lm.forward(prompt="root")
    bound.sub_lm.forward(prompt="sub")

    assert bound is not source
    assert bound.root_lm is not root
    assert bound.sub_lm is not sub
    assert root.calls == []
    assert sub.calls == []
    assert bound.budget is budget
    assert source.budget is None


def test_turn_binding_without_budget_inherits_the_source_budget() -> None:
    from fleet_rlm.rlm.budget import TurnBudget

    budget = TurnBudget(deadline=None)
    source = RLMModelBundle(_CopyableLM(), _CopyableLM(), budget=budget)

    assert source.bind_turn().budget is budget


def test_sequential_and_concurrent_turn_bindings_return_fresh_copies() -> None:
    source = RLMModelBundle(_CopyableLM(), _CopyableLM())

    def call(_index: int) -> RLMModelBundle:
        return source.bind_turn()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(call, (0, 1)))

    assert first.root_lm is not second.root_lm
    assert first.sub_lm is not second.sub_lm
    assert first.root_lm.history is not second.root_lm.history
    assert not hasattr(source.root_lm, "_fleet_can_finalize")
    assert not hasattr(source.sub_lm, "_fleet_can_finalize")


def _tool(name):
    return dspy.Tool(lambda: "ok", name=name)


@pytest.mark.parametrize("name", ["llm_query"])
def test_native_builder_rejects_namespace_collisions(name):
    with pytest.raises(RLMConfigError):
        build_native_rlm_for_test(signature="question -> answer", options=RLMOptions(), tools=[_tool(name)])


def test_native_builder_rejects_duplicate_names_and_keeps_authorized_tools():
    rlm = build_native_rlm_for_test(signature="question -> answer", options=RLMOptions(), tools=[_tool("read_data")])

    assert set(rlm.tools) == {"read_data"}
    with pytest.raises(RLMConfigError, match="duplicate"):
        build_native_rlm_for_test(
            signature="question -> answer",
            options=RLMOptions(),
            tools=[_tool("read_data"), _tool("read_data")],
        )


# --- Normalized dspy.LM Construction Contracts ---


def test_build_lm_stays_native_in_a_fresh_process() -> None:
    script = """
import dspy
import sys
from fleet_rlm.rlm.program import build_lm

lm = build_lm("openai/test", api_key=None)
assert lm.engine == "lm15"
assert "litellm" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_build_lm_uses_dspy_aggregated_completion_path(monkeypatch: pytest.MonkeyPatch) -> None:
    lm = MagicMock(return_value="lm")
    monkeypatch.setattr(factory.dspy, "LM", lm)

    factory.build_lm("openai/model", api_key=None)

    kwargs = lm.call_args.kwargs
    # DSPy's native RLM path consumes the provider's completed response rather
    # than a raw streaming wrapper.
    assert "stream" not in kwargs
    assert "stream_options" not in kwargs
    assert kwargs["engine"] == "lm15"


@pytest.mark.parametrize("reasoning_effort", [None, "none"])
@pytest.mark.asyncio
async def test_build_lm_async_call_processes_an_aggregated_completion(
    monkeypatch: pytest.MonkeyPatch,
    reasoning_effort: str | None,
) -> None:
    """The native async RLM path receives an aggregated completion response."""

    import dspy.clients.lm as dspy_lm
    from dspy.clients.engines.lm15_engine import AsyncLM15Engine
    from dspy.lm15 import Request, Response, response_from_openai_chat

    async def complete(self: AsyncLM15Engine, request: Request) -> Response:
        assert self.resolve(request.model).provider == "openai-chat"
        return response_from_openai_chat(
            {
                "id": "offline-test",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "OK"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            model=request.model,
        )

    monkeypatch.setattr(AsyncLM15Engine, "complete", complete)

    lm = factory.build_lm("openai/model", api_key=None, cache=False, reasoning_effort=reasoning_effort)

    process_send_stream = getattr(dspy_lm.dspy.settings, "send_stream", None)
    with dspy_lm.dspy.context(send_stream=None):
        result = await lm.acall(prompt="Reply with exactly OK.")

    assert result == ["OK"]
    assert getattr(dspy_lm.dspy.settings, "send_stream", None) is process_send_stream


def test_build_lm_passes_provider_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    lm = MagicMock(return_value="bounded-lm")
    monkeypatch.setattr(factory.dspy, "LM", lm)

    factory.build_lm("openai/model", api_key=None, timeout_seconds=37)

    assert lm.call_args.kwargs["timeout"] == 37


@pytest.mark.parametrize("trailing_slash", [""])
def test_databricks_deepseek_declares_exact_schema_capability(trailing_slash: str) -> None:
    from dspy.clients.backend_selection import select_backend

    lm = factory.build_lm(
        "uscentral.ai_gateway.deepseek-v4-1-flash-service",
        api_key="token",
        base_url=f"https://workspace.example/ai-gateway/mlflow/v1{trailing_slash}",
        cache=False,
    )

    selected = select_backend(lm)
    assert selected.native is True
    assert selected.resolution.provider == "fleet-databricks"
    assert selected.resolution.model == "uscentral.ai_gateway.deepseek-v4-1-flash-service"
    assert selected.clients["api_base"] == f"https://workspace.example/ai-gateway/mlflow/v1{trailing_slash}"
    assert "response_format" in lm.supported_params
    assert lm.supports_response_schema is True


@pytest.mark.parametrize(
    "base_url",
    [None, "https://workspace.example/v1", "https:///ai-gateway/mlflow/v1", "https://[bad/ai-gateway/mlflow/v1"],
)
def test_databricks_deepseek_requires_ai_gateway_route(base_url: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    lm = MagicMock()
    monkeypatch.setattr(factory.dspy, "LM", lm)
    with pytest.raises(ValueError, match="AI Gateway base URL"):
        factory.build_lm("uscentral.ai_gateway.deepseek-v4-1-flash-service", api_key="token", base_url=base_url)
    lm.assert_not_called()


@pytest.mark.asyncio
async def test_databricks_action_request_carries_json_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    import dspy
    from dspy.clients.engines.lm15_engine import AsyncLM15Engine
    from dspy.lm15 import Request, Response, response_from_openai_chat

    from fleet_rlm.rlm.adapter import FleetJSONAdapter

    class Action(dspy.Signature):
        """One RLM action."""

        request: str = dspy.InputField()
        reasoning: str = dspy.OutputField()
        code: str = dspy.OutputField()

    seen: list[Request] = []

    async def complete(_self: AsyncLM15Engine, request: Request) -> Response:
        seen.append(request)
        return response_from_openai_chat(
            {
                "id": "offline-action",
                "object": "chat.completion",
                "created": 0,
                "model": "uscentral.ai_gateway.deepseek-v4-1-flash-service",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"reasoning":"run","code":"print(1)"}'},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            model=request.model,
        )

    monkeypatch.setattr(AsyncLM15Engine, "complete", complete)
    lm = factory.build_lm(
        "uscentral.ai_gateway.deepseek-v4-1-flash-service",
        api_key="token",
        base_url="https://workspace.example/ai-gateway/mlflow/v1",
        cache=False,
    )
    with dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        result = await dspy.Predict(Action).acall(request="execute")

    assert result.code == "print(1)"
    assert len(seen) == 1
    assert seen[0].model.endswith("/uscentral.ai_gateway.deepseek-v4-1-flash-service")
    assert seen[0].config.response_format is not None
    assert seen[0].config.response_format["type"] == "json_schema"


@pytest.mark.asyncio
async def test_alibaba_action_request_uses_json_object_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import dspy
    from dspy.clients.engines.lm15_engine import AsyncLM15Engine
    from dspy.lm15 import Request, Response, response_from_openai_chat

    from fleet_rlm.rlm.adapter import FleetJSONAdapter

    class Action(dspy.Signature):
        """One RLM action."""

        request: str = dspy.InputField()
        reasoning: str = dspy.OutputField()
        code: str = dspy.OutputField()

    seen: list[Request] = []

    async def complete(_self: AsyncLM15Engine, request: Request) -> Response:
        seen.append(request)
        return response_from_openai_chat(
            {
                "id": "offline-action",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-v4.1-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"reasoning":"run","code":"print(1)"}'},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            model=request.model,
        )

    monkeypatch.setattr(AsyncLM15Engine, "complete", complete)
    lm = factory.build_lm(
        "deepseek-v4.1-flash",
        api_key="token",
        base_url="https://dashscope.example/compatible-mode/v1",
        cache=False,
    )
    with dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        result = await dspy.Predict(Action).acall(request="execute")

    assert result.code == "print(1)"
    assert len(seen) == 1
    assert seen[0].config.response_format == {"type": "json_object"}


@pytest.mark.asyncio
async def test_unsupported_native_request_does_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    import dspy.clients.lm as dspy_lm
    from dspy.utils.exceptions import LMUnsupportedFeatureError

    fallback = MagicMock(side_effect=AssertionError("LiteLLM fallback was called"))
    monkeypatch.setattr(dspy_lm, "_get_litellm", fallback)
    lm = factory.build_lm("openai/test", api_key=None, cache=False)

    with pytest.raises(LMUnsupportedFeatureError, match="allowed_openai_params"):
        await lm.acall(prompt="ping", allowed_openai_params=["unsupported"])

    fallback.assert_not_called()


def test_runtime_does_not_accept_provider_environment_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "FLEET_OPENAI_API_KEY",
        "FLEET_LLM_BASE_URL",
        "FLEET_ROOT_MODEL",
        "FLEET_SUB_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "provider-alias-must-not-be-used")
    monkeypatch.setenv("DSPY_LM_MODEL", "provider/alias-model")

    settings = Settings()

    assert settings.llm_api_key is None
    assert settings.root_model == "openai/gpt-4o-mini"
    with pytest.raises(RuntimeError, match="FLEET_OPENAI_API_KEY"):
        factory.build_model_bundle(settings)


def test_whitespace_legacy_key_is_not_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_OPENAI_API_KEY", raising=False)
    settings = Settings(llm_api_key=SecretStr("   "))

    assert factory.has_llm_credentials(settings) is False


def test_legacy_generic_key_does_not_cross_provider_role_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    settings = Settings(
        llm_api_key=SecretStr("legacy-key"),
        root_llm_api_key_env="DATABRICKS_TOKEN",
        sub_llm_api_key_env="DATABRICKS_TOKEN",
    )

    assert factory.resolve_role_api_key(settings, settings.llm_role("root")) is None


def test_explicit_role_environment_credentials_are_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABRICKS_TOKEN", "provider-key")
    settings = Settings(
        root_llm_api_key_env="DATABRICKS_TOKEN",
        sub_llm_api_key_env="DATABRICKS_TOKEN",
    )

    assert settings.llm_api_key is None
    assert factory.has_llm_credentials(settings) is True
