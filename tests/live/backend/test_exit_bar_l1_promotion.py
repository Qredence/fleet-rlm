"""Exit-bar L1 — tip-matched live promotion via create_live_app composition.

Gate: FLEET_LIVE=1
Requires Daytona + LLM credentials (FLEET_*).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import dspy
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fleet_rlm.app import create_live_app
from fleet_rlm.chat.live_context import resolve_settings
from fleet_rlm.config import Settings
from fleet_rlm.daytona.bindings import SandboxBinding
from fleet_rlm.skills.capabilities import (
    DSPySkillSelector,
    SkillSelection,
    TaskContract,
)
from tests.live.backend._database import upgrade_to_head

pytestmark = [pytest.mark.live_daytona]

_EVIDENCE = Path(".scratch/clean-backend-refoundation/assets/live-promotion-evidence.json")


def _live_enabled() -> bool:
    return os.environ.get("FLEET_LIVE", "").strip().lower() in {"1", "true", "yes"}


def _have_daytona() -> bool:
    return bool(os.environ.get("FLEET_DAYTONA_API_KEY"))


def _have_llm() -> bool:
    return bool(os.environ.get("FLEET_LLM_API_KEY"))


def _skip_unless_live() -> None:
    if not _live_enabled():
        pytest.skip("Set FLEET_LIVE=1 for exit-bar L1")
    if not _have_daytona():
        pytest.skip("Daytona API key not configured")
    if not _have_llm():
        pytest.skip("LLM API key not configured")


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _lockfile_fingerprint() -> str:
    lock = Path("uv.lock")
    if not lock.exists():
        return "missing-uv.lock"
    return hashlib.sha256(lock.read_bytes()).hexdigest()[:16]


def _parse_sse_events(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        raw = line[6:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _parse_sse_kinds(body: str) -> list[str]:
    return [str(payload["type"]) for payload in _parse_sse_events(body) if isinstance(payload.get("type"), str)]


def _text_from_chunks(events: list[dict[str, Any]]) -> str:
    return "".join(str(event.get("delta") or "") for event in events if event.get("type") == "text-delta")


class _LongContextAttachment(dspy.SandboxSerializable):
    """Host-approved Attachment body reconstructed as text in the Run REPL."""

    def __init__(self, text: str) -> None:
        self.text = text

    def sandbox_setup(self) -> str:
        return ""

    def to_sandbox(self) -> bytes:
        return self.text.encode("utf-8")

    def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
        return f"{var_name} = {data_expr}.decode('utf-8')"

    def rlm_preview(self, max_chars: int = 500) -> str:
        return f"Authorized long-context Attachment: {self.text[:max_chars]}"


class _L1TypedResult(dspy.Signature):
    request: str = dspy.InputField()
    corpus: _LongContextAttachment = dspy.InputField()
    capability_knowledge: list[str] = dspy.InputField()
    answer: str = dspy.OutputField()
    evidence: str = dspy.OutputField()


def _write_evidence(payload: dict[str, Any]) -> None:
    _EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    _EVIDENCE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@pytest.mark.timeout(900)
def test_exit_bar_l1_promotion_live_composition(tmp_path: Path) -> None:
    """L1: authenticated chat SSE through create_live_app + volume durability."""
    _skip_unless_live()

    db_path = tmp_path / "l1.sqlite"
    upload_root = tmp_path / "uploads"
    artifact_root = tmp_path / "artifacts"
    upload_root.mkdir()
    artifact_root.mkdir()

    base = resolve_settings(Settings())
    daytona_key = base.daytona_api_key.get_secret_value() if base.daytona_api_key is not None else ""
    llm_key = base.llm_api_key.get_secret_value() if base.llm_api_key is not None else ""
    settings = base.model_copy(
        update={
            "live_kernel": True,
            "auth_mode": "dev",
            "database_url": f"sqlite+aiosqlite:///{db_path.resolve()}",
            "upload_root": str(upload_root),
            "artifact_root": str(artifact_root),
            "daytona_api_key": SecretStr(daytona_key),
            "llm_api_key": SecretStr(llm_key),
        }
    )
    upgrade_to_head(settings.database_url or "")

    user_id, workspace_id = uuid4(), uuid4()
    headers = {
        "X-Fleet-User-Id": str(user_id),
        "X-Fleet-Workspace-Id": str(workspace_id),
    }
    tip = _git_commit()
    observed: dict[str, object] = {
        "gate": "L1",
        "entrypoint": "create_live_app",
        "tip_commit": tip,
        "uv_lock_sha256_16": _lockfile_fingerprint(),
    }

    app = create_live_app(settings=settings)

    with TestClient(app) as client:
        assert getattr(app.state, "live_mode", False) is True
        assert getattr(app.state, "turn_coordinator", None) is not None
        observed["live_mode"] = True

        models = app.state.rlm_model_bundle
        try:
            sub_sample = str(models.sub_lm("Reply with exactly the word ok and nothing else."))
            observed["sub_lm_ok"] = True
            observed["sub_lm_sample"] = sub_sample[:80]
        except Exception as exc:  # noqa: BLE001
            observed["result"] = "FAIL"
            observed["llm_error_type"] = type(exc).__name__
            observed["llm_error"] = str(exc)[:240]
            _write_evidence(observed)
            pytest.skip(f"LLM not usable for L1: {type(exc).__name__}")

        sess = client.post("/api/sessions", headers=headers, json={"title": "l1-exit-bar"})
        assert sess.status_code == 201, sess.text
        session_id = UUID(sess.json()["id"])
        observed["session_id"] = str(session_id)

        attachment_secret = "l1-attachment-secret-7f3a"
        up = client.post(
            "/api/files",
            headers=headers,
            files={"file": ("l1.txt", attachment_secret.encode(), "text/plain")},
        )
        assert up.status_code in {200, 201}, up.text
        attachment_id = up.json()["id"]
        observed["attachment_id"] = attachment_id

        registry = app.state.skill_registry
        capability_registry = app.state.capability_registry

        def _attachment_input(context: Any) -> dict[str, Any]:
            assert context.file_tool_host is not None
            assert context.attachments
            read = context.file_tool_host.read_attachment(str(context.attachments[0].id))
            assert read.get("ok") is True
            return {"corpus": _LongContextAttachment(str(read["content"]))}

        def verify_corpus(value: str) -> str:
            """Return a short marker after inspecting serialized corpus text."""
            return f"verified:{value[:40]}"

        corpus_hint = dspy.Tool(
            lambda: "Use the corpus variable and create one Artifact Candidate.",
            name="corpus_hint",
            desc="Return the registered long-context workflow hint",
        )
        typed_contract = TaskContract(
            id="l1-typed-result",
            signature=_L1TypedResult,
            input_mapper=lambda context: {"request": context.request},
            output_serializer=lambda prediction: {
                "answer": prediction.answer,
                "evidence": prediction.evidence,
            },
        )
        capability_registry.register(
            "l1-primary",
            tools=(verify_corpus,),
            task_contract=typed_contract,
            input_adapters=(_attachment_input,),
        )
        capability_registry.register(
            "l1-auxiliary",
            tools=(corpus_hint,),
            knowledge=("Preserve exact evidence strings from authorized Attachments.",),
        )

        primary_skill = registry.register(
            name="l1-exit-helper",
            description="Primary typed long-context analysis capability",
            instructions="After load, tell the agent to read the attached file then submit its text.",
            version="1.0.0",
            trust="system",
            capability_refs=("l1-primary",),
            task_contract_ref="l1-primary",
        )
        auxiliary_skill = registry.register(
            name="l1-evidence-helper",
            description="Auxiliary evidence and Artifact capability",
            instructions="Use exact Attachment evidence and create the requested Artifact Candidate.",
            version="1.0.0",
            trust="system",
            capability_refs=("l1-auxiliary",),
        )
        observed["skill_ids"] = [str(primary_skill.id), str(auxiliary_skill.id)]

        async def _select_two(_selector: Any, **_kwargs: Any) -> SkillSelection:
            return SkillSelection(
                selected_skill_ids=(primary_skill.id, auxiliary_skill.id),
                primary_skill_id=primary_skill.id,
            )

        with patch.object(DSPySkillSelector, "select", _select_two):
            chat1 = client.post(
                "/api/chat",
                headers=headers,
                json={
                    "message": (
                        "Use the serialized corpus variable and registered tools. Exact sequence:\n"
                        f"1) load_skill(skill_id='{primary_skill.id}')\n"
                        f"2) load_skill(skill_id='{auxiliary_skill.id}')\n"
                        "3) corpus_hint() and verify_corpus(corpus)\n"
                        "4) create_artifact(kind='text', content='l1-artifact-on-volume', title='l1-art')\n"
                        "5) SUBMIT(answer=corpus, evidence='typed-live-evidence')\n"
                        "Keep the trajectory short."
                    ),
                    "session_id": str(session_id),
                    "attachment_ids": [attachment_id],
                },
            )
        assert chat1.status_code == 200, chat1.text
        events1 = _parse_sse_events(chat1.text)
        kinds1 = _parse_sse_kinds(chat1.text)
        observed["turn1_event_kinds"] = kinds1
        assert kinds1, "expected SSE events"
        assert kinds1[-1] == "finish", f"L1 requires AI SDK finish; got {kinds1[-1]!r}"
        assert events1[-1].get("finishReason") == "stop"
        observed["turn1_terminal"] = kinds1[-1]
        assert kinds1.count("data-skill") >= 4, f"expected two activated and two loaded Skills; kinds={kinds1}"
        for required in (
            "reasoning-start",
            "reasoning-delta",
            "reasoning-end",
            "data-rlm-code",
            "tool-input-available",
            "tool-output-available",
            "data-rlm-output",
            "data-attachment",
            "data-artifact",
            "data-usage",
            "data-structured-result",
            "text-start",
            "text-delta",
            "text-end",
        ):
            assert required in kinds1, f"L1 missing {required}; kinds={kinds1}"
        assert (
            kinds1.index("data-artifact")
            < kinds1.index("data-usage")
            < kinds1.index("data-structured-result")
            < kinds1.index("text-start")
            < kinds1.index("finish")
        )
        observed["skill_loaded"] = True
        observed["attachment_read"] = True
        artifact_event = next(event for event in events1 if event.get("type") == "data-artifact")
        artifact_payload = artifact_event.get("data")
        artifact_id = UUID(str((artifact_payload or {}).get("artifact_id")))
        observed["artifact_id"] = str(artifact_id)
        structured = next(event["data"] for event in events1 if event.get("type") == "data-structured-result")
        assert structured["schemaId"] == "l1-typed-result"
        assert structured["value"]["evidence"] == "typed-live-evidence"
        answer_text = _text_from_chunks(events1)
        observed["turn1_answer_excerpt"] = answer_text[:120]
        assert attachment_secret in answer_text, (
            f"L1 answer must include attachment body after read_attachment; got {answer_text!r}"
        )

        turns1 = client.get(f"/api/sessions/{session_id}/turns", headers=headers)
        assert turns1.status_code == 200, turns1.text
        turn_items = turns1.json().get("items") or []
        observed["history_turns_after_turn1"] = len(turn_items)
        assert len(turn_items) >= 2
        assistant_parts = turn_items[-1].get("parts") or []
        assistant_part_types = [part.get("type") for part in assistant_parts]
        observed["reloaded_assistant_part_types"] = assistant_part_types
        for required in (
            "reasoning",
            "data-rlm-code",
            "dynamic-tool",
            "data-artifact",
            "data-structured-result",
            "text",
        ):
            assert required in assistant_part_types

        committed_artifact = client.get(f"/api/artifacts/{artifact_id}", headers=headers)
        assert committed_artifact.status_code == 200, committed_artifact.text
        observed["artifact_checksum"] = committed_artifact.json()["checksum_sha256"]

        async def _select_none(_selector: Any, **_kwargs: Any) -> SkillSelection:
            return SkillSelection()

        with patch.object(DSPySkillSelector, "select", _select_none):
            chat2 = client.post(
                "/api/chat",
                headers=headers,
                json={
                    "message": (
                        "Using only the prior Session History, submit the exact attachment secret "
                        "that appeared in the previous assistant answer. Do not call tools."
                    ),
                    "session_id": str(session_id),
                },
            )
        assert chat2.status_code == 200, chat2.text
        kinds2 = _parse_sse_kinds(chat2.text)
        observed["turn2_event_kinds"] = kinds2
        assert kinds2[-1] == "finish"
        events2 = _parse_sse_events(chat2.text)
        answer2 = _text_from_chunks(events2)
        observed["turn2_history_answer_excerpt"] = answer2[:120]
        assert attachment_secret in answer2

        resources = app.state.live_kernel_resources

        async def _replace_and_verify() -> dict[str, object]:
            binding = await resources.bindings.get(session_id)
            assert binding is not None and binding.sandbox_id
            old_sid = binding.sandbox_id
            new_binding = await resources.session_manager.replace(
                SandboxBinding(
                    session_id=session_id,
                    sandbox_id=old_sid,
                    workspace_id=workspace_id,
                    volume_id=binding.volume_id or "",
                    volume_subpath=binding.volume_subpath or f"workspaces/{workspace_id}",
                    mount_path=binding.mount_path or "/home/daytona/fleet",
                    provider_state="unrecoverable",
                ),
                workspace_id=workspace_id,
                user_id=user_id,
            )
            assert new_binding.sandbox_id != old_sid
            resources.track_sandbox(new_binding.sandbox_id)
            store = app.state.artifact_store
            body = await store.read_bytes(artifact_id, user_id=user_id, workspace_id=workspace_id)
            assert body == b"l1-artifact-on-volume"
            return {
                "volume_id": binding.volume_id,
                "volume_subpath": binding.volume_subpath,
                "sandbox_id_before_replace": old_sid,
                "sandbox_id_after_replace": new_binding.sandbox_id,
                "artifact_survived_replace": True,
                "private_volume_fs_mutation": False,
            }

        observed.update(client.portal.call(_replace_and_verify))

        turns2 = client.get(f"/api/sessions/{session_id}/turns", headers=headers)
        assert turns2.status_code == 200
        observed["history_turns_after_replace"] = len(turns2.json().get("items") or [])
        observed["result"] = "PASS"
        _write_evidence(observed)
        assert _EVIDENCE.is_file()
        assert observed["tip_commit"] == tip
