"""Exit-bar L1 — tip-matched live promotion via create_live_app composition.

Gate: FLEET_CLEAN_LIVE=1
Requires Daytona + LLM credentials (FLEET_CLEAN_* / DAYTONA_API_KEY).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fleet_rlm_clean.app import create_live_app
from fleet_rlm_clean.chat.live_context import settings_with_env_fallbacks
from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.daytona.bindings import SandboxBinding
from fleet_rlm_clean.rlm.events import RuntimeEventKind

pytestmark = [pytest.mark.live_daytona]

_EVIDENCE = Path(".scratch/clean-backend-refoundation/assets/live-promotion-evidence.json")


def _live_enabled() -> bool:
    return os.environ.get("FLEET_CLEAN_LIVE", "").strip().lower() in {"1", "true", "yes"}


def _have_daytona() -> bool:
    return bool(os.environ.get("DAYTONA_API_KEY") or os.environ.get("FLEET_CLEAN_DAYTONA_API_KEY"))


def _have_llm() -> bool:
    return bool(
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("FLEET_CLEAN_LLM_API_KEY")
        or os.environ.get("DSPY_LLM_API_KEY")
    )


def _skip_unless_live() -> None:
    if not _live_enabled():
        pytest.skip("Set FLEET_CLEAN_LIVE=1 for exit-bar L1")
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
    kinds: list[str] = []
    for payload in _parse_sse_events(body):
        kind = payload.get("kind") or payload.get("type")
        if isinstance(kind, str):
            kinds.append(kind)
    return kinds


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

    base = settings_with_env_fallbacks(Settings())
    daytona_key = (
        base.daytona_api_key.get_secret_value()
        if base.daytona_api_key is not None
        else os.environ.get("DAYTONA_API_KEY") or os.environ.get("FLEET_CLEAN_DAYTONA_API_KEY") or ""
    )
    llm_key = (
        base.llm_api_key.get_secret_value()
        if base.llm_api_key is not None
        else (
            os.environ.get("FLEET_CLEAN_LLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("DSPY_LLM_API_KEY")
            or ""
        )
    )
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
        skill = registry.register(
            name="l1-exit-helper",
            description="Exit-bar L1 progressive-load skill",
            instructions="After load, tell the agent to read the attached file then submit its text.",
            version="1.0.0",
            trust="system",
        )
        observed["skill_id"] = str(skill.id)

        chat1 = client.post(
            "/api/chat",
            headers=headers,
            json={
                "message": (
                    "In the REPL, call host tools then submit. Exact sequence:\n"
                    f"1) load_skill(skill_id='{skill.id}')\n"
                    f"2) read_attachment(attachment_id='{attachment_id}')\n"
                    "3) create_artifact(kind='text', content='l1-artifact-on-volume', title='l1-art')\n"
                    "4) SUBMIT(answer=<the attachment text content>)\n"
                    "Do not invent other tools. Keep the trajectory short."
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
        assert kinds1[-1] in {RuntimeEventKind.RUN_COMPLETED.value, "run.completed"}, (
            f"L1 requires successful terminal after Turn Commit; got {kinds1[-1]!r}"
        )
        observed["turn1_terminal"] = kinds1[-1]
        assert RuntimeEventKind.SKILL_LOADED.value in kinds1, (
            f"L1 requires Host-Mediated Skill Progressive Load; kinds={kinds1}"
        )
        assert RuntimeEventKind.ATTACHMENT_READ.value in kinds1, (
            f"L1 requires Host-Mediated attachment read; kinds={kinds1}"
        )
        assert RuntimeEventKind.ARTIFACT_CREATED.value in kinds1, (
            f"L1 requires Artifact publication after Turn Commit; kinds={kinds1}"
        )
        assert kinds1.index(RuntimeEventKind.ARTIFACT_CREATED.value) < kinds1.index(
            RuntimeEventKind.RUN_COMPLETED.value
        )
        observed["skill_loaded"] = True
        observed["attachment_read"] = True
        artifact_event = next(
            event
            for event in events1
            if (event.get("kind") or event.get("type")) == RuntimeEventKind.ARTIFACT_CREATED.value
        )
        artifact_payload = (
            artifact_event.get("payload") if isinstance(artifact_event.get("payload"), dict) else artifact_event
        )
        artifact_id = UUID(str((artifact_payload or {}).get("artifact_id")))
        observed["artifact_id"] = str(artifact_id)
        text_done = next(
            (e for e in events1 if (e.get("kind") or e.get("type")) == RuntimeEventKind.TEXT_COMPLETED.value),
            None,
        )
        answer_text = ""
        if text_done is not None:
            payload = text_done.get("payload") if isinstance(text_done.get("payload"), dict) else text_done
            answer_text = str((payload or {}).get("text") or "")
        observed["turn1_answer_excerpt"] = answer_text[:120]
        assert attachment_secret in answer_text, (
            f"L1 answer must include attachment body after read_attachment; got {answer_text!r}"
        )

        turns1 = client.get(f"/api/sessions/{session_id}/turns", headers=headers)
        assert turns1.status_code == 200, turns1.text
        turn_items = turns1.json().get("items") or []
        observed["history_turns_after_turn1"] = len(turn_items)
        assert len(turn_items) >= 2

        committed_artifact = client.get(f"/api/artifacts/{artifact_id}", headers=headers)
        assert committed_artifact.status_code == 200, committed_artifact.text
        observed["artifact_checksum"] = committed_artifact.json()["checksum_sha256"]

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
        assert kinds2[-1] in {RuntimeEventKind.RUN_COMPLETED.value, "run.completed"}
        events2 = _parse_sse_events(chat2.text)
        text_done2 = next(
            (e for e in events2 if (e.get("kind") or e.get("type")) == RuntimeEventKind.TEXT_COMPLETED.value),
            None,
        )
        payload2 = (
            text_done2.get("payload")
            if text_done2 is not None and isinstance(text_done2.get("payload"), dict)
            else text_done2
        )
        answer2 = str((payload2 or {}).get("text") or "")
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
