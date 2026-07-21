"""Public Turn selection, preparation, and progressive Skill-loading contract."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api.errors import install_error_handlers
from fleet_rlm.api.routes.turns import router as turns_router
from fleet_rlm.api.schemas import CreateTurnRequest
from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
from fleet_rlm.files.models import PreparedAttachments
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.events import EventRecorder, RuntimeEvent
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
from fleet_rlm.skills.catalog import SkillCatalog, build_bundled_skill_catalog, stable_skill_id
from fleet_rlm.skills.errors import InvalidSkillSelectionError
from fleet_rlm.skills.models import SkillSelectionRef


class _EmptyOpenedTurn:
    run_id = uuid4()

    def __aiter__(self) -> AsyncIterator[RuntimeEvent]:
        return self._events()

    async def _events(self) -> AsyncIterator[RuntimeEvent]:
        if False:
            yield

    async def aclose(self) -> None:
        return None


class _Coordinator:
    def __init__(self, error: BaseException | None = None) -> None:
        self.command: OpenTurnCommand | None = None
        self.error = error

    async def open(self, command: OpenTurnCommand) -> _EmptyOpenedTurn:
        self.command = command
        if self.error is not None:
            raise self.error
        return _EmptyOpenedTurn()


def _turn_client(coordinator: _Coordinator) -> TestClient:
    app = FastAPI()
    app.state.composition_ready = True
    app.state.turn_coordinator = coordinator
    install_error_handlers(app)
    app.include_router(turns_router)
    return TestClient(app)


def _catalog() -> SkillCatalog:
    return build_bundled_skill_catalog()


def _turn(*, selections: tuple[SkillSelectionRef, ...] = ()) -> ExecuteTurn:
    async def not_cancelled() -> bool:
        return False

    return ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("analyze the supplied material", (), selections),
        SessionHistory(()),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )


def test_turn_request_accepts_zero_to_four_unique_exact_skill_selections() -> None:
    assert CreateTurnRequest.model_validate({"text": "inspect"}).skill_selections == []
    ids = tuple(UUID(int=index) for index in range(1, 5))
    request = CreateTurnRequest.model_validate(
        {
            "text": "inspect",
            "skill_selections": [
                {"id": str(skill_id), "expected_version": f"{index}.0.0"} for index, skill_id in enumerate(ids, start=1)
            ],
        }
    )
    assert [(item.id, item.expected_version) for item in request.skill_selections] == [
        (skill_id, f"{index}.0.0") for index, skill_id in enumerate(ids, start=1)
    ]

    with pytest.raises(ValueError):
        CreateTurnRequest.model_validate(
            {
                "text": "inspect",
                "skill_selections": [
                    {"id": str(skill_id), "expected_version": "1.0.0"} for skill_id in (*ids, UUID(int=5))
                ],
            }
        )
    with pytest.raises(ValueError):
        CreateTurnRequest.model_validate(
            {
                "text": "inspect",
                "skill_selections": [
                    {"id": str(ids[0]), "expected_version": "1.0.0"},
                    {"id": str(ids[0]), "expected_version": "2.0.0"},
                ],
            }
        )


def test_invalid_exact_selection_is_generic_before_stream_headers() -> None:
    supplied_version = "private-version-detail"
    coordinator = _Coordinator(InvalidSkillSelectionError())
    with _turn_client(coordinator) as client:
        response = client.post(
            f"/api/sessions/{uuid4()}/turns",
            json={
                "text": "inspect",
                "skill_selections": [{"id": str(uuid4()), "expected_version": supplied_version}],
            },
            headers={"Idempotency-Key": "invalid-skill"},
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"code": "invalid_skill_selection", "message": "Invalid Skill selection"}
    assert supplied_version not in response.text


@pytest.mark.asyncio
async def test_deno_progressive_tools_preload_exact_selection_and_keep_events_metadata_only() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.chat.deno_run_environment import DenoRunEnvironmentProvider, _DenoCapabilityPreparer

    catalog = _catalog()
    selected = catalog.require(stable_skill_id("long-context"))
    other = catalog.require(stable_skill_id("workspace-files"))
    turn = _turn(selections=(SkillSelectionRef(selected.card.id, selected.card.version),))
    environment = await DenoRunEnvironmentProvider().acquire(turn, deadline=float("inf"))
    prepared = await _DenoCapabilityPreparer(
        skill_catalog=catalog,
        models=RLMModelBundle(MagicMock(), MagicMock()),
        options=RLMOptions(),
        max_artifact_bytes=1024,
    ).prepare(turn, environment, PreparedAttachments((), ()), deadline=float("inf"))

    tools = {str(tool.name): tool for tool in prepared.spec.tools}
    assert {"load_skill", "read_skill_resource"} <= tools.keys()
    assert tools["load_skill"](skill_id=str(other.card.id))["error"] == "skill_not_found"
    loaded = tools["load_skill"](skill_id=str(selected.card.id), expected_version=selected.card.version)
    assert loaded["ok"] is True
    resource_path = loaded["resources"][0]["path"]
    resource = tools["read_skill_resource"](
        skill_id=str(selected.card.id),
        resource_path=resource_path,
        expected_version=selected.card.version,
    )
    assert resource["ok"] is True

    details = prepared.drain_public_details()
    assert [detail.kind for detail in details] == ["skill.activated", "skill.loaded"]
    recorder = EventRecorder(turn.run_id, turn.session_id)
    chunks = [chunk for detail in details for chunk in AISDKUIProjector().project(recorder.record(detail))]
    serialized = json.dumps(chunks)
    assert "skill_markdown" not in serialized
    assert "content" not in serialized
    assert selected.instructions not in serialized
    assert resource["content"] not in serialized


@pytest.mark.asyncio
async def test_progressive_resource_requires_load_and_daytona_preparation_is_provider_free() -> None:
    from fleet_rlm.config import Settings
    from fleet_rlm.daytona.run_environment import LiveKernelResources, _LiveCapabilityPreparer
    from fleet_rlm.skills.tools import SkillToolHost

    catalog = _catalog()
    selected = catalog.require(stable_skill_id("long-context"))
    host = SkillToolHost(catalog)
    resource_path = next(iter(selected.resources))
    assert host.read_skill_resource(str(selected.card.id), resource_path) == {"ok": False, "error": "skill_not_loaded"}
    assert host.load_skill(str(selected.card.id), selected.card.version)["ok"] is True
    assert host.read_skill_resource(str(selected.card.id), resource_path, selected.card.version)["ok"] is True

    resources = object.__new__(LiveKernelResources)
    resources.settings = Settings(_env_file=None, run_environment="daytona")
    resources.models = RLMModelBundle(MagicMock(), MagicMock())
    resources.skill_catalog = catalog
    environment = SimpleNamespace(attachment_sink=SimpleNamespace(volume_fs=SimpleNamespace(sandbox=object())))
    prepared = await _LiveCapabilityPreparer(resources).prepare(
        _turn(),
        environment,
        PreparedAttachments((), ()),
        deadline=float("inf"),
    )

    tool_names = {str(tool.name) for tool in prepared.spec.tools}
    assert {"load_skill", "read_skill_resource"} <= tool_names
    assert prepared.spec.workspace.available is True
