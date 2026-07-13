"""Hermetic vertical proof for Skill composition and the AI SDK UI stream."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import dspy
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.chat.capabilities import CapabilityContextBuilder
from fleet_rlm.chat.context_builder import OfflineContextBuilder, ephemeral_lease
from fleet_rlm.chat.turn_coordinator import TurnCoordinator
from fleet_rlm.config import Settings
from fleet_rlm.daytona.in_process import InProcessInterpreterBackend
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter
from fleet_rlm.rlm.budgets import RLMBudget
from fleet_rlm.rlm.context import RLMTurnContext
from fleet_rlm.rlm.factory import RLMFactory
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.skills.capabilities import DSPySkillSelector, SkillSelection, TaskContract


class _LongContext(dspy.SandboxSerializable):
    def __init__(self, text: str) -> None:
        self.text = text

    def sandbox_setup(self) -> str:
        return ""

    def to_sandbox(self) -> bytes:
        return self.text.encode("utf-8")

    def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
        return f"{var_name} = {data_expr}.decode('utf-8') if isinstance({data_expr}, bytes) else {data_expr}"

    def rlm_preview(self, max_chars: int = 500) -> str:
        return f"Authorized long context: {self.text[:max_chars]}"


class _TypedResult(dspy.Signature):
    request: str = dspy.InputField()
    corpus: _LongContext = dspy.InputField()
    capability_knowledge: list[str] = dspy.InputField()
    answer: str = dspy.OutputField()
    evidence: str = dspy.OutputField()


class _ActionPredictor:
    def __init__(self, code: str) -> None:
        self._code = code

    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        return dspy.Prediction(reasoning="Use registered capabilities and committed context.", code=self._code)


class _ScriptedRLMFactory:
    """Build the real fresh ObservableRLM while making its model action deterministic."""

    def __init__(self, actions: tuple[str, ...]) -> None:
        self._actions = iter(actions)

    def create(self, **kwargs: Any) -> Any:
        rlm = RLMFactory().create(**kwargs)
        rlm.generate_action = _ActionPredictor(next(self._actions))
        return rlm


class _InProcessContextBuilder:
    def __init__(self) -> None:
        self._offline = OfflineContextBuilder(
            budget=RLMBudget(
                max_iterations=2,
                max_llm_calls=4,
                max_output_chars=4_000,
                max_wall_seconds=20,
                max_sub_lm_concurrency=2,
                max_tool_calls=12,
            )
        )

    def build(self, command: Any) -> RLMTurnContext:
        base = self._offline.build(command)

        class _DeterministicSubLM:
            model = "offline/deterministic-sub"

            def __call__(self, _prompt: str) -> list[str]:
                return ["sub-model-ok"]

        return RLMTurnContext(
            run_id=base.run_id,
            session_id=base.session_id,
            user_id=base.user_id,
            workspace_id=base.workspace_id,
            request=base.request,
            models=RLMModelBundle(root_lm=base.models.root_lm, sub_lm=_DeterministicSubLM()),
            budget=base.budget,
            lease=ephemeral_lease(DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())),
        )


def _chunks(body: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for line in body.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        value = json.loads(line.removeprefix("data: "))
        if isinstance(value, dict):
            chunks.append(value)
    return chunks


def _text(chunks: list[dict[str, Any]]) -> str:
    return "".join(str(chunk.get("delta") or "") for chunk in chunks if chunk.get("type") == "text-delta")


def test_skill_composed_rlm_commits_and_reloads_ai_sdk_ui_message(tmp_path: Path) -> None:
    database = tmp_path / "fleet.sqlite"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{database}",
        upload_root=str(tmp_path / "uploads"),
        artifact_root=str(tmp_path / "artifacts"),
    )
    app = create_app(settings=settings)
    user_id, workspace_id = uuid4(), uuid4()
    headers = {
        "X-Fleet-User-Id": str(user_id),
        "X-Fleet-Workspace-Id": str(workspace_id),
    }
    corpus_text = "hermetic-long-context-42"

    with TestClient(app) as client:
        registry = app.state.skill_registry
        capabilities = app.state.capability_registry

        def attachment_input(context: RLMTurnContext) -> dict[str, Any]:
            assert context.file_tool_host is not None and context.attachments
            result = context.file_tool_host.read_attachment(str(context.attachments[0].id))
            assert result.get("ok") is True
            return {"corpus": _LongContext(str(result["content"]))}

        def verify_corpus(value: str) -> str:
            return f"verified:{value}"

        hint = dspy.Tool(
            lambda: "Use the authorized corpus and produce one committed Artifact.",
            name="corpus_hint",
            desc="Return the registered workflow hint",
        )
        contract = TaskContract(
            id="hermetic-typed-result",
            signature=_TypedResult,
            input_mapper=lambda context: {"request": context.request},
            output_serializer=lambda prediction: {
                "answer": prediction.answer,
                "evidence": prediction.evidence,
            },
            validator=lambda value: (
                None
                if str(value.get("evidence") or "").startswith("verified:")
                else (_ for _ in ()).throw(ValueError("invalid evidence"))
            ),
        )
        capabilities.register(
            "hermetic-primary",
            tools=(verify_corpus,),
            task_contract=contract,
            input_adapters=(attachment_input,),
        )
        capabilities.register(
            "hermetic-auxiliary",
            tools=(hint,),
            knowledge=("Preserve exact authorized evidence.",),
        )
        primary = registry.register(
            name="hermetic-primary",
            description="Typed long-context analysis",
            instructions="Use the registered typed workflow.",
            capability_refs=("hermetic-primary",),
            task_contract_ref="hermetic-primary",
            resource_bodies={"guide.md": "Private progressive workflow guidance."},
        )
        auxiliary = registry.register(
            name="hermetic-auxiliary",
            description="Artifact and evidence support",
            instructions="Create one evidence Artifact.",
            capability_refs=("hermetic-auxiliary",),
        )

        session_id = UUID(client.post("/api/sessions", headers=headers, json={}).json()["id"])
        upload = client.post(
            "/api/files",
            headers=headers,
            files={"file": ("long.txt", corpus_text.encode(), "text/plain")},
        )
        assert upload.status_code == 200
        attachment_id = upload.json()["id"]

        first_action = (
            f"load_skill('{primary.id}')\n"
            f"load_skill('{auxiliary.id}')\n"
            f"read_skill_resource('{primary.id}', 'guide.md')\n"
            "corpus_hint()\n"
            "sub_result = llm_query('Classify the authorized corpus')\n"
            "evidence = verify_corpus(corpus) + ':' + sub_result\n"
            "create_artifact('text', corpus, title='hermetic-proof')\n"
            "SUBMIT(answer=corpus, evidence=evidence)"
        )
        builder = CapabilityContextBuilder(
            _InProcessContextBuilder(),
            skill_registry=registry,
            attachment_store=app.state.attachment_store,
            volume_fs=app.state.workspace_volume_mirror,
            volume_paths=app.state.workspace_volume_mirror.volume_paths,
            capability_registry=capabilities,
        )
        app.state.turn_coordinator = TurnCoordinator(
            runner=RLMRunner(factory=_ScriptedRLMFactory((first_action,))),
            context_builder=builder,
            session_repository=app.state.session_repository,
        )

        async def select_for_request(_selector: Any, **kwargs: Any) -> SkillSelection:
            if str(kwargs.get("request") or "").startswith("follow up"):
                return SkillSelection()
            return SkillSelection(
                selected_skill_ids=(primary.id, auxiliary.id),
                primary_skill_id=primary.id,
            )

        with patch.object(DSPySkillSelector, "select", select_for_request):
            first = client.post(
                "/api/chat",
                headers=headers,
                json={
                    "message": "analyze the authorized long context",
                    "session_id": str(session_id),
                    "attachment_ids": [attachment_id],
                },
            )
        assert first.status_code == 200
        assert first.headers["x-vercel-ai-ui-message-stream"] == "v1"
        assert first.text.rstrip().endswith("data: [DONE]")
        chunks = _chunks(first.text)
        types = [str(chunk.get("type")) for chunk in chunks]
        for expected in (
            "data-skill",
            "reasoning-delta",
            "data-rlm-code",
            "tool-input-available",
            "tool-output-available",
            "data-attachment",
            "data-artifact",
            "data-usage",
            "data-structured-result",
            "text-delta",
            "finish",
        ):
            assert expected in types, first.text
        assert types.index("data-artifact") < types.index("data-structured-result") < types.index("finish")
        assert _text(chunks) == corpus_text

        structured = next(chunk["data"] for chunk in chunks if chunk.get("type") == "data-structured-result")
        usage = next(chunk["data"]["usage"] for chunk in chunks if chunk.get("type") == "data-usage")
        assert usage["sub_model_profile"] == "offline/deterministic-sub"
        assert usage["sub_lm_calls"] == 1
        assert usage["tool_calls"] >= 7
        assert usage["tool_call_limit"] == 12
        assert usage["estimated_cost"] is None
        assert structured == {
            "schemaId": "hermetic-typed-result",
            "schemaVersion": "1",
            "value": {
                "answer": corpus_text,
                "evidence": f"verified:{corpus_text}:sub-model-ok",
            },
        }
        artifact = next(chunk["data"] for chunk in chunks if chunk.get("type") == "data-artifact")
        artifact_id = UUID(str(artifact["artifact_id"]))
        turns = client.get(f"/api/sessions/{session_id}/turns", headers=headers).json()["items"]
        first_assistant = turns[1]
        part_types = [part["type"] for part in first_assistant["parts"]]
        assert "reasoning" in part_types
        assert "dynamic-tool" in part_types
        assert "data-artifact" in part_types
        assert "data-usage" in part_types
        assert "data-structured-result" in part_types
        assert first_assistant["metadata"]["structuredResult"] == structured

    restarted_app = create_app(settings=settings)
    with TestClient(restarted_app) as restarted:
        restarted_builder = CapabilityContextBuilder(
            _InProcessContextBuilder(),
            skill_registry=restarted_app.state.skill_registry,
            attachment_store=restarted_app.state.attachment_store,
            volume_fs=restarted_app.state.workspace_volume_mirror,
            volume_paths=restarted_app.state.workspace_volume_mirror.volume_paths,
            capability_registry=restarted_app.state.capability_registry,
        )
        restarted_app.state.turn_coordinator = TurnCoordinator(
            runner=RLMRunner(factory=_ScriptedRLMFactory(("SUBMIT(answer=history[-1]['content'])",))),
            context_builder=restarted_builder,
            session_repository=restarted_app.state.session_repository,
        )

        restored_artifact = restarted.get(f"/api/artifacts/{artifact_id}", headers=headers)
        assert restored_artifact.status_code == 200
        assert (
            asyncio.run(
                restarted_app.state.artifact_store.read_bytes(
                    artifact_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                )
            )
            == corpus_text.encode()
        )
        restored_turns = restarted.get(
            f"/api/sessions/{session_id}/turns",
            headers=headers,
        ).json()["items"]
        restored_artifact_parts = [part for part in restored_turns[1]["parts"] if part.get("type") == "data-artifact"]
        assert restored_artifact_parts[0]["data"]["artifact_id"] == str(artifact_id)

        async def select_none(_selector: Any, **_kwargs: Any) -> SkillSelection:
            return SkillSelection()

        with patch.object(DSPySkillSelector, "select", select_none):
            second = restarted.post(
                "/api/chat",
                headers=headers,
                json={"message": "follow up from committed history", "session_id": str(session_id)},
            )
        assert second.status_code == 200
        assert _text(_chunks(second.text)) == corpus_text
        reloaded = restarted.get(f"/api/sessions/{session_id}/turns", headers=headers).json()["items"]
        assert [item["role"] for item in reloaded] == ["user", "assistant", "user", "assistant"]
        assert reloaded[-1]["content"] == corpus_text
