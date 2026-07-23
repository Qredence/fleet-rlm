"""Characterization and validation tests for model-visible RLM inputs."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from uuid import UUID

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
from fleet_rlm.rlm.inputs import build_rlm_input_kwargs
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


def test_backend_module_suffix_is_reserved_for_dspy_modules() -> None:
    source_root = Path(__file__).resolve().parents[4] / "src" / "fleet_rlm"
    offenders: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not node.name.endswith("Module"):
                continue
            if not any(
                isinstance(base, ast.Attribute)
                and isinstance(base.value, ast.Name)
                and base.value.id == "dspy"
                and base.attr == "Module"
                or isinstance(base, ast.Name)
                and base.id == "Module"
                for base in node.bases
            ):
                offenders.append(f"{path.relative_to(source_root)}:{node.name}")
    assert offenders == [], f"non-DSPy Module classes remain: {offenders}"


def test_dspy_imports_stay_out_of_deterministic_backend_layers() -> None:
    source_root = Path(__file__).resolve().parents[4] / "src" / "fleet_rlm"
    allowed_tool_adapters = {
        "sessions/history_tools.py",
        "files/tools.py",
        "files/workspace_tools.py",
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
