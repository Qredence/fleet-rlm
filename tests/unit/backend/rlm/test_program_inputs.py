"""Prepared native-RLM program inputs, child workspace paths, and bounded Session context.

* ``test_program_inputs.py``: behavior contracts for program inputs.
* Child inputs are selected by relative Workspace path and materialized by the Turn host.
* ``test_session_context.py``: bounded Session context at the prepared native-RLM input seam.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest
from pydantic import ValidationError

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
from fleet_rlm.skills.catalog import build_bundled_skill_catalog
from tests.support.rlm_inputs import ATTACHMENT_ID, SESSION_ID, SKILL_ID, _payload
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


def test_custom_signature_drops_optional_history_input() -> None:
    class CustomSignature(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()

    payload = build_rlm_input_kwargs(
        request="custom signature",
        history=dspy.History(messages=[]),
        session_context=SessionContextManifest(SESSION_ID, 0, 0, ()),
        signature=CustomSignature,
    )

    assert set(payload) == {"request"}


def test_manifest_skill_affordances_reach_the_model_unchanged() -> None:

    catalog = build_bundled_skill_catalog()
    payload = build_rlm_input_kwargs(
        request="Inspect relevant Skills",
        history=dspy.History(messages=[]),
        session_context=SessionContextManifest(SESSION_ID, 0, 0, ()),
        skill_cards=catalog.cards(),
    )

    by_name = {card["name"]: card for card in payload["skill_cards"]}  # type: ignore[attr-defined]
    assert by_name["dspy-rlm"]["affordances"] == ["interpreter", "llm_query"]
    assert by_name["long-context"]["affordances"] == ["sandbox.search", "llm_query_batched", "workspace.files"]
    assert by_name["workspace-files"]["affordances"] == ["workspace.files", "artifacts.publish"]
    assert by_name["data-analysis"]["affordances"] == ["artifacts.publish", "llm_query_batched"]
    assert by_name["report-builder"]["affordances"] == ["workspace.files", "artifacts.publish"]


def test_model_visible_skill_discovery_snapshot_matches_the_bundled_catalog() -> None:

    catalog = build_bundled_skill_catalog()
    payload = build_rlm_input_kwargs(
        request="Inspect relevant Skills",
        history=dspy.History(messages=[]),
        session_context=SessionContextManifest(SESSION_ID, 0, 0, ()),
        skill_cards=catalog.cards(),
    )

    assert payload["skill_cards"] == [
        {
            "id": "f4d260fa-a663-5ef9-835f-eac46c10c1bf",
            "name": "data-analysis",
            "description": "Compute and verify descriptive statistics, trends, and qualified anomalies.",
            "scope": "system",
            "version": "1.1.0",
            "trust": "system",
            "affordances": ["artifacts.publish", "llm_query_batched"],
            "resources_available": False,
        },
        {
            "id": "83f7de82-1fea-5bc0-90e0-795631f3d5d0",
            "name": "dspy-rlm",
            "description": "Use when analyzing, explaining, or implementing dspy.RLM "
            "(Recursive Language Model / REPL code agent). Not for RAG or dspy.Retrieve.",
            "scope": "system",
            "version": "1.1.0",
            "trust": "system",
            "affordances": ["interpreter", "llm_query"],
            "resources_available": True,
        },
        {
            "id": "015a133e-7b90-50c7-bb61-4b2772f57c1c",
            "name": "long-context",
            "description": (
                "Discover public sources and analyze large documents, transcripts, code, or datasets "
                "with sandbox Python."
            ),
            "scope": "system",
            "version": "2.2.0",
            "trust": "system",
            "affordances": ["sandbox.search", "llm_query_batched", "workspace.files"],
            "resources_available": True,
        },
        {
            "id": "90bd89fb-66c8-558d-acdb-55c59ba7106c",
            "name": "report-builder",
            "description": "Create, save, read back, and verify reports from trusted source data.",
            "scope": "system",
            "version": "1.2.0",
            "trust": "system",
            "affordances": ["workspace.files", "artifacts.publish"],
            "resources_available": False,
        },
        {
            "id": "94eedfa7-4b0c-5316-96af-5e3924e128e7",
            "name": "workspace-files",
            "description": "Use durable Session Workspace, Project, Attachment, and Artifact tools correctly.",
            "scope": "system",
            "version": "1.3.0",
            "trust": "system",
            "affordances": ["workspace.files", "artifacts.publish"],
            "resources_available": True,
        },
    ]


def test_custom_skill_payload_matches_dspy_rlm_declared_inputs() -> None:
    from fleet_rlm.skills.signatures import DataAnalysisSignature

    dspy.RLM(DataAnalysisSignature)._validate_inputs(_payload())


def test_model_visible_payload_excludes_bodies_paths_and_runtime_objects() -> None:
    payload = _payload()
    # The empty ``dspy.History`` carrier is not JSON-serializable; strip it
    # for the wire-format check while keeping the body search exhaustive.
    wire_payload = {name: value for name, value in payload.items() if name != "history"}
    serialized = json.dumps(wire_payload, sort_keys=True)

    for forbidden in (
        "instruction body",
        "resource body",
        "attachment bytes",
        "/home/daytona/fleet",
        "staged-secret-path",
        "api-key-secret",
        "old committed message",
    ):
        assert forbidden not in repr(payload)
        assert forbidden not in serialized


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attachment_body", "secret"),
        ("volume_path", "/home/daytona/fleet/private"),
        ("message_body", "old history"),
    ],
)
def test_session_context_rejects_unknown_startup_data(field: str, value: object) -> None:
    context = _payload()["session_context"]
    assert isinstance(context, dict)
    with pytest.raises(ValidationError):
        SessionContextInput.model_validate(
            {
                **context,
                "session_id": SESSION_ID,
                "recent": tuple(context["recent"]),  # type: ignore[index]
                field: value,
            },
            strict=True,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("checkpoint_version", -1),
        ("message_count", True),
        ("session_id", "not-a-uuid"),
    ],
)
def test_session_context_rejects_malformed_values(field: str, value: object) -> None:
    context = _payload()["session_context"]
    assert isinstance(context, dict)
    with pytest.raises(ValidationError):
        SessionContextInput.model_validate(
            {
                **context,
                "session_id": SESSION_ID,
                "recent": tuple(context["recent"]),  # type: ignore[index]
                field: value,
            },
            strict=True,
        )


def test_nested_dtos_reject_oversized_and_wrong_concrete_values() -> None:
    with pytest.raises(ValidationError):
        SkillCardInput.model_validate(
            {
                "id": SKILL_ID,
                "name": "report-builder",
                "description": "x" * 513,
                "scope": "system",
                "version": "1.0.0",
                "trust": "system",
                "affordances": (),
                "resources_available": True,
            },
            strict=True,
        )
    with pytest.raises(ValidationError):
        AttachmentInput.model_validate(
            {
                "id": ATTACHMENT_ID,
                "filename": "report.md",
                "content_type": "text/markdown",
                "byte_size": -1,
                "checksum_sha256": "a" * 64,
            },
            strict=True,
        )


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
    from fleet_rlm.rlm.program import FleetRLMSignature
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
        prediction = await rlm.acall(interpreter, **kwargs)

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
            interpreter,
            request="inspect",
            attachments=capsule,
        )

    interpreter.shutdown()
    assert lm.history == []


def test_backend_module_suffix_is_reserved_for_dspy_modules() -> None:
    source_root = Path(__file__).resolve().parents[4] / "src" / "fleet_rlm"
    offenders: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not node.name.endswith("Module"):
                continue
            if not any(
                (
                    isinstance(base, ast.Attribute)
                    and isinstance(base.value, ast.Name)
                    and base.value.id == "dspy"
                    and base.attr == "Module"
                )
                or (isinstance(base, ast.Name) and base.id == "Module")
                for base in node.bases
            ):
                offenders.append(f"{path.relative_to(source_root)}:{node.name}")
    assert offenders == [], f"non-DSPy Module classes remain: {offenders}"


def test_dspy_imports_stay_out_of_deterministic_backend_layers() -> None:
    source_root = Path(__file__).resolve().parents[4] / "src" / "fleet_rlm"
    allowed_tool_adapters = {
        "sessions/history_tools.py",
        "sessions/task_tools.py",
        # P43.7 narrow SandboxSerializable transport for committed Session
        # conversation; the dspy coupling is sanctioned by the plan and
        # required for the Daytona broker, which cannot inject a raw
        # dspy.History Pydantic value into a Sandbox.
        "sessions/history_transport.py",
        # P44.1 canonical History factory returns the exact installed
        # dspy.History Pydantic model; the dspy coupling is the point.
        "sessions/history.py",
        "attachments/service.py",
        "attachments/tools.py",
        "artifacts/tools.py",
        "workspace/memory.py",
        "workspace/projects.py",
        "workspace/workspace.py",
    }
    offenders: list[str] = []
    for package in ("api", "persistence", "sessions", "workspace", "attachments", "artifacts"):
        for path in sorted((source_root / package).rglob("*.py")):
            relative = path.relative_to(source_root).as_posix()
            if relative in allowed_tool_adapters:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(alias.name == "dspy" for alias in node.names):
                    offenders.append(relative)
                    break
                if isinstance(node, ast.ImportFrom) and node.module == "dspy":
                    offenders.append(relative)
                    break
    assert offenders == [], f"DSPy imports leaked into deterministic layers: {offenders}"


_SESSION_ID = "00000000-0000-0000-0000-000000000001"


def _manifest():
    from fleet_rlm.sessions.context import SessionContextManifest

    return SessionContextManifest(
        session_id=__import__("uuid").UUID(_SESSION_ID),
        checkpoint_version=0,
        message_count=0,
        recent=(),
    )


def test_fleet_signature_declares_history_as_required_input() -> None:
    """``FleetRLMSignature`` declares ``history: dspy.History`` as a required input."""

    assert "history" in FleetRLMSignature.input_fields

    history_field = FleetRLMSignature.input_fields["history"]
    assert history_field.annotation is dspy.History
    assert history_field.is_required()

    extra = getattr(history_field, "json_schema_extra", None)
    assert isinstance(extra, dict)
    assert extra.get("__dspy_field_type") == "input"

    # The description mirrors the P44 canonical-conversation wording.
    desc = extra.get("desc", "")
    assert isinstance(desc, str)
    for needle in (
        "Canonical committed Session conversation",
        "history.messages",
        "do not assume previews are complete",
        "do not treat",
        "hidden trajectory",
        "failed Runs as conversation",
    ):
        assert needle in desc, f"history description missing canonical phrase: {needle!r}"


def test_fleet_signature_still_declares_unchanged_common_fields() -> None:
    """The P41 common inputs and ``answer`` output are unchanged by P44.3."""

    assert set(FleetRLMSignature.input_fields) == {
        "request",
        "history",
        "session_context",
        "skill_cards",
        "attachments",
    }
    for required in ("request", "session_context", "skill_cards", "attachments"):
        assert FleetRLMSignature.input_fields[required].is_required()
    assert "answer" in FleetRLMSignature.output_fields
    assert FleetRLMSignature.output_fields["answer"].is_required()


def test_build_rlm_input_kwargs_includes_history_when_supplied() -> None:
    """The optional ``history`` keyword round-trips into the kwargs dict."""

    history = dspy.History(messages=[{"request": "earlier", "answer": "earlier answer"}])
    kwargs = build_rlm_input_kwargs(
        request="current",
        session_context=_manifest(),
        history=history,
    )

    assert "history" in kwargs
    # The exact installed ``dspy.History`` instance is forwarded unchanged
    # (no transformation, no preview, no replacement).
    assert kwargs["history"] is history
    assert type(kwargs["history"]) is dspy.History
    assert list(kwargs["history"].messages) == [{"request": "earlier", "answer": "earlier answer"}]


def test_build_rlm_input_kwargs_omits_history_when_not_supplied() -> None:
    """Without ``history`` the key is absent so existing call sites keep working."""

    kwargs = build_rlm_input_kwargs(
        request="current",
        session_context=_manifest(),
    )

    assert "history" not in kwargs
    # Existing default payload shape is preserved.
    assert set(kwargs) == {"request", "session_context", "skill_cards", "attachments"}


@pytest.mark.parametrize(
    "bad_value",
    [
        {"messages": [{"request": "r", "answer": "a"}]},
        [{"request": "r", "answer": "a"}],
        "raw-string",
        42,
        object(),
    ],
)
def test_build_rlm_input_kwargs_rejects_non_dspy_history_values(bad_value: object) -> None:
    """``build_rlm_input_kwargs`` fails closed on non-``dspy.History`` values."""

    with pytest.raises(RLMConfigError, match="Turn input metadata is invalid"):
        build_rlm_input_kwargs(
            request="current",
            session_context=_manifest(),
            history=bad_value,  # type: ignore[arg-type]
        )


def test_build_rlm_input_kwargs_rejects_history_subclass_or_shadow() -> None:
    """A subclass of ``dspy.History`` is rejected; only the exact class is accepted."""

    class _HistorySubclass(dspy.History):
        pass

    with pytest.raises(RLMConfigError, match="Turn input metadata is invalid"):
        build_rlm_input_kwargs(
            request="current",
            session_context=_manifest(),
            history=_HistorySubclass(messages=[]),
        )


def test_dspy_rlm_validates_end_to_end_payload_with_history() -> None:
    """``dspy.RLM(FleetRLMSignature)._validate_inputs`` accepts the full payload."""

    history = dspy.History(messages=[{"request": "earlier", "answer": "earlier answer"}])
    kwargs = build_rlm_input_kwargs(
        request="current",
        session_context=_manifest(),
        history=history,
    )
    # The contract pinned by this test: the production payload with a real
    # ``dspy.History`` instance satisfies the native RLM input validator.
    dspy.RLM(FleetRLMSignature)._validate_inputs(kwargs)


# --- child input request validation -----------------------------------
def test_child_inputs_use_relative_workspace_paths() -> None:
    from fleet_rlm.rlm.recursion import ChildRequest

    request = ChildRequest(task="Read selected evidence", inputs=("selected/evidence.txt",), context="Summarize it")
    assert request.inputs == ("selected/evidence.txt",)
    assert "selected/evidence.txt" in request.render()
    assert "Summarize it" in request.render()

    with pytest.raises(ValidationError):
        ChildRequest(task="Read selected evidence", inputs=("artifact://not-a-uuid",))


# --- from test_session_context.py -------------------------------------
@pytest.mark.asyncio
async def test_prepared_rlm_kwargs_bound_a_large_session_to_recent_previews() -> None:
    from fleet_rlm.attachments import PreparedAttachments
    from fleet_rlm.rlm.execution import RLMExecutionSpec, RLMRunner
    from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
    from fleet_rlm.sessions.models import HistoryMessage, TurnAccess
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )
    from fleet_rlm.turn_preparation import RunEnvironment

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
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=Environments(),
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
