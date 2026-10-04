"""Prepared native-RLM program inputs, child workspace paths, and bounded Session context.

* ``test_program_inputs.py``: behavior contracts for program inputs.
* Child inputs are selected by relative Workspace path and materialized by the Turn host.
* ``test_session_context.py``: bounded Session context at the prepared native-RLM input seam.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.rlm.program import (
    AttachmentContextCapsule,
    AttachmentContextEntry,
    AttachmentInput,
    FleetRLMSignature,
    SessionContextInput,
    SkillCardInput,
    build_rlm_input_kwargs,
)
from fleet_rlm.rlm.result import RLMConfigError
from fleet_rlm.sessions.context import SessionContextManifest
from fleet_rlm.sessions.models import SessionHistory, TurnInput
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
    from fleet_rlm.attachments import PreparedAttachments
    from fleet_rlm.rlm.execution import RLMExecutionSpec, RLMRunner
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.sessions.models import HistoryMessage, TurnAccess
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )
    from fleet_rlm.turns.preparation import RunEnvironment

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
    ).prepare(turn, deadline=float("inf"))

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
