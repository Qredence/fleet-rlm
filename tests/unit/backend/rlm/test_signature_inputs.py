"""Characterization and validation tests for model-visible RLM inputs."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from uuid import UUID

import dspy
import pytest
from pydantic import ValidationError

from fleet_rlm.chat.session_context import SessionContextManifest, TurnPreview
from fleet_rlm.files.models import PreparedAttachment
from fleet_rlm.files.workspace_models import DAYTONA_WORKSPACE_CAPABILITY
from fleet_rlm.rlm.errors import RLMConfigError
from fleet_rlm.rlm.input_models import (
    AttachmentInput,
    SessionContextInput,
    SkillCardInput,
)
from fleet_rlm.rlm.inputs import AttachmentContextCapsule, AttachmentContextEntry, build_rlm_input_kwargs
from fleet_rlm.skills.models import SkillCard

SESSION_ID = UUID("00000000-0000-0000-0000-000000000001")
SKILL_ID = UUID("00000000-0000-0000-0000-000000000002")
ATTACHMENT_ID = UUID("00000000-0000-0000-0000-000000000003")


def _payload() -> dict[str, object]:
    return build_rlm_input_kwargs(
        request="Summarize the report",
        session_context=SessionContextManifest(
            session_id=SESSION_ID,
            checkpoint_version=7,
            message_count=3,
            recent=(TurnPreview(ordinal=3, role="user", preview="Recent request"),),
        ),
        skill_cards=(
            SkillCard(
                id=SKILL_ID,
                name="report-builder",
                description="Build bounded reports",
                version="1.0.0",
                resources_available=True,
            ),
        ),
        attachments=(
            PreparedAttachment(
                ATTACHMENT_ID,
                "report.md",
                "text/markdown",
                128,
                "a" * 64,
            ),
        ),
        workspace=DAYTONA_WORKSPACE_CAPABILITY,
    )


def test_default_input_payload_contains_only_bounded_metadata() -> None:
    payload = _payload()

    assert set(payload) == {"request", "session_context", "skill_cards", "attachments"}
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


def test_manifest_skill_affordances_reach_the_model_unchanged() -> None:
    from fleet_rlm.skills.catalog import build_bundled_skill_catalog

    catalog = build_bundled_skill_catalog()
    payload = build_rlm_input_kwargs(
        request="Inspect relevant Skills",
        session_context=SessionContextManifest(SESSION_ID, 0, 0, ()),
        skill_cards=catalog.cards(),
    )

    by_name = {card["name"]: card for card in payload["skill_cards"]}  # type: ignore[attr-defined]
    assert by_name["dspy-rlm"]["affordances"] == ["interpreter", "llm_query"]
    assert by_name["long-context"]["affordances"] == ["fetch_url", "llm_query_batched", "workspace.files"]
    assert by_name["workspace-files"]["affordances"] == ["workspace.files", "artifacts.publish"]
    assert by_name["data-analysis"]["affordances"] == ["artifacts.publish", "llm_query_batched"]
    assert by_name["report-builder"]["affordances"] == ["workspace.files", "artifacts.publish"]


def test_model_visible_skill_discovery_snapshot_matches_the_bundled_catalog() -> None:
    from fleet_rlm.skills.catalog import build_bundled_skill_catalog

    catalog = build_bundled_skill_catalog()
    payload = build_rlm_input_kwargs(
        request="Inspect relevant Skills",
        session_context=SessionContextManifest(SESSION_ID, 0, 0, ()),
        skill_cards=catalog.cards(),
    )

    assert payload["skill_cards"] == [
        {
            "id": "f4d260fa-a663-5ef9-835f-eac46c10c1bf",
            "name": "data-analysis",
            "description": "Compute and verify descriptive statistics, trends, and qualified anomalies.",
            "scope": "system",
            "version": "1.0.0",
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
            "version": "1.0.0",
            "trust": "system",
            "affordances": ["interpreter", "llm_query"],
            "resources_available": True,
        },
        {
            "id": "015a133e-7b90-50c7-bb61-4b2772f57c1c",
            "name": "long-context",
            "description": "Use bounded retrieval to analyze large documents, transcripts, code, or datasets.",
            "scope": "system",
            "version": "2.0.0",
            "trust": "system",
            "affordances": ["fetch_url", "llm_query_batched", "workspace.files"],
            "resources_available": True,
        },
        {
            "id": "90bd89fb-66c8-558d-acdb-55c59ba7106c",
            "name": "report-builder",
            "description": "Create, save, read back, and verify reports from trusted source data.",
            "scope": "system",
            "version": "1.1.0",
            "trust": "system",
            "affordances": ["workspace.files", "artifacts.publish"],
            "resources_available": False,
        },
        {
            "id": "94eedfa7-4b0c-5316-96af-5e3924e128e7",
            "name": "workspace-files",
            "description": "Use durable Session Workspace, Project, Attachment, and Artifact tools correctly.",
            "scope": "system",
            "version": "1.1.0",
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
    serialized = json.dumps(payload, sort_keys=True)

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

    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
    from fleet_rlm.rlm.signature import FleetRLMSignature

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
    from fleet_rlm.rlm.inputs import _materialize_context_manifest

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
        "files/tools.py",
        "files/url_tool.py",
        "files/workspace_tools.py",
        "files/memory_tools.py",
        "files/memory_candidate_tools.py",
        "files/project_tools.py",
    }
    offenders: list[str] = []
    for package in ("api", "persistence", "sessions", "files", "artifacts"):
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
