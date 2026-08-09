"""Public Turn selection, preparation, and progressive Skill-loading contract."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from hashlib import sha256
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
from fleet_rlm.composition.inventory import RuntimeInventory
from fleet_rlm.config import Settings
from fleet_rlm.files.models import AttachmentRef, PreparedAttachments, StagedAttachment
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
    app.state.settings = Settings()
    app.state.composition_ready = True
    app.state.runtime_inventory = RuntimeInventory(turn_coordinator=coordinator)
    install_error_handlers(app)
    app.include_router(turns_router)
    return TestClient(app)


def _catalog() -> SkillCatalog:
    return build_bundled_skill_catalog()


def _turn(
    *,
    selections: tuple[SkillSelectionRef, ...] = (),
    attachment_ids: tuple[UUID, ...] = (),
) -> ExecuteTurn:
    async def not_cancelled() -> bool:
        return False

    return ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("analyze the supplied material", attachment_ids, selections),
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


def test_invalid_exact_selection_is_generic_inside_the_stream() -> None:
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

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [line.removeprefix("data: ") for line in response.text.splitlines() if line.startswith("data: ")]
    chunks = [json.loads(value) for value in frames if value != "[DONE]"]
    assert frames[-1] == "[DONE]"
    assert chunks[-2:] == [
        {"type": "error", "errorText": "Invalid Skill selection"},
        {"type": "finish", "finishReason": "error"},
    ]
    assert supplied_version not in response.text


@pytest.mark.asyncio
async def test_private_progressive_tools_preload_exact_selection_and_keep_events_metadata_only() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.composition.testing import TestingCapabilityPreparer, TestingRunEnvironmentProvider

    catalog = _catalog()
    selected = catalog.require(stable_skill_id("long-context"))
    other = catalog.require(stable_skill_id("workspace-files"))
    turn = _turn(selections=(SkillSelectionRef(selected.card.id, selected.card.version),))
    environment = await TestingRunEnvironmentProvider().acquire(turn, deadline=float("inf"))
    prepared = await TestingCapabilityPreparer(
        skill_catalog=catalog,
        models=RLMModelBundle(MagicMock(), MagicMock()),
        options=RLMOptions(),
        max_artifact_bytes=1024,
    ).prepare(turn, environment, PreparedAttachments((), ()), deadline=float("inf"))

    tools = {str(tool.name): tool for tool in prepared.spec.tools}
    assert len(prepared.spec.skill_cards) == 5
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
    from fleet_rlm.daytona.run_environment import _LiveCapabilityPreparer
    from fleet_rlm.skills.tools import SkillToolHost

    catalog = _catalog()
    selected = catalog.require(stable_skill_id("long-context"))
    host = SkillToolHost(catalog)
    resource_path = next(iter(selected.resources))
    assert host.read_skill_resource(str(selected.card.id), resource_path) == {"ok": False, "error": "skill_not_loaded"}
    assert host.load_skill(str(selected.card.id), selected.card.version)["ok"] is True
    assert host.read_skill_resource(str(selected.card.id), resource_path, selected.card.version)["ok"] is True

    settings = Settings(_env_file=None, run_environment="daytona")
    environment = SimpleNamespace(attachment_sink=SimpleNamespace(volume_fs=SimpleNamespace(sandbox=object())))
    turn = _turn()
    prepared = await _LiveCapabilityPreparer(settings, catalog).prepare(
        turn,
        environment,
        PreparedAttachments((), ()),
        deadline=float("inf"),
    )

    assert prepared.spec.skill_cards == catalog.cards()
    tools = {str(tool.name): tool for tool in prepared.spec.tools}
    assert {"load_skill", "read_skill_resource"} <= tools.keys()
    assert prepared.spec.workspace.available is True
    loaded = tools["load_skill"](skill_id=str(selected.card.id), expected_version=selected.card.version)
    assert loaded["ok"] is True
    resource = tools["read_skill_resource"](
        skill_id=str(selected.card.id),
        resource_path=resource_path,
        expected_version=selected.card.version,
    )
    assert resource["ok"] is True

    details = prepared.drain_public_details()
    assert [detail.kind for detail in details] == ["skill.activated", "skill.loaded"]
    from fleet_rlm.api.sse import AISDKUIProjector

    recorder = EventRecorder(turn.run_id, turn.session_id)
    serialized = json.dumps(
        [chunk for detail in details for chunk in AISDKUIProjector().project(recorder.record(detail))]
    )
    assert selected.instructions not in serialized
    assert resource["content"] not in serialized
    assert "skill_markdown" not in serialized
    assert "content" not in serialized


@pytest.mark.asyncio
async def test_data_analysis_signature_and_report_builder_selection_use_host_tools_only() -> None:
    from fleet_rlm.composition.testing import TestingCapabilityPreparer, TestingRunEnvironmentProvider

    catalog = _catalog()
    csv = b"value,group\n1,a\n2,a\n"
    attachment_id = uuid4()
    attachment = AttachmentRef(attachment_id, "data.csv", "text/csv", len(csv), sha256(csv).hexdigest())
    staged = StagedAttachment(attachment_id, "/attachments/data.csv")
    data_analysis = catalog.require(stable_skill_id("data-analysis"))
    report_builder = catalog.require(stable_skill_id("report-builder"))
    turn = _turn(
        attachment_ids=(attachment_id,),
        selections=(
            SkillSelectionRef(data_analysis.card.id, data_analysis.card.version),
            SkillSelectionRef(report_builder.card.id, report_builder.card.version),
        ),
    )
    environment = await TestingRunEnvironmentProvider().acquire(turn, deadline=float("inf"))
    environment.attachment_sink.values[staged.sandbox_path] = csv
    prepared = await TestingCapabilityPreparer(
        skill_catalog=catalog,
        models=RLMModelBundle(MagicMock(), MagicMock()),
        options=RLMOptions(),
        max_artifact_bytes=1024,
    ).prepare(turn, environment, PreparedAttachments((attachment,), (staged,)), deadline=float("inf"))

    assert prepared.spec.output_schema_id == "skill.data-analysis"
    assert prepared.spec.output_schema_version == "1.0.0"
    assert prepared.spec.signature.output_fields["answer"].annotation is str
    assert set(prepared.spec.signature.output_fields) == {"answer", "findings", "metrics", "anomalies"}
    assert {str(tool.name) for tool in prepared.spec.tools} == {
        "read_attachment",
        "read_session_history",
        "load_skill",
        "read_skill_resource",
        "fetch_url",
    }
    attachment_result = next(tool for tool in prepared.spec.tools if str(tool.name) == "read_attachment")(
        attachment_id=str(attachment_id)
    )
    assert attachment_result["ok"] is True
    assert attachment_result["content"] == csv.decode()
    lifecycle = prepared.drain_public_details()
    assert [detail.kind for detail in lifecycle] == [
        "attachment.read",
        "skill.activated",
        "skill.loaded",
        "skill.activated",
        "skill.loaded",
    ]
    assert {detail.name for detail in lifecycle if detail.kind == "skill.activated"} == {
        "data-analysis",
        "report-builder",
    }
    assert prepared.spec.workspace.available is False

    from fleet_rlm.api.sse import AISDKUIProjector

    recorder = EventRecorder(turn.run_id, turn.session_id)
    skill_details = [detail for detail in lifecycle if detail.kind.startswith("skill.")]
    serialized = json.dumps(
        [chunk for detail in skill_details for chunk in AISDKUIProjector().project(recorder.record(detail))]
    )
    assert data_analysis.instructions not in serialized
    assert report_builder.instructions not in serialized
    assert "content" not in serialized


@pytest.mark.asyncio
async def test_deterministic_composition_runs_data_analysis_signature() -> None:
    from fleet_rlm.composition.testing import DeterministicTurnPreparation, TestingRLMFactory
    from fleet_rlm.rlm.runner import RLMRunner

    class NoAttachments:
        async def prepare_run(self, access, attachment_ids, run, sink) -> PreparedAttachments:
            del access, attachment_ids, run, sink
            return PreparedAttachments((), ())

    catalog = _catalog()
    selected = catalog.require(stable_skill_id("data-analysis"))
    prepared = await DeterministicTurnPreparation(
        attachments=NoAttachments(),
        skill_catalog=catalog,
    ).prepare(
        _turn(selections=(SkillSelectionRef(selected.card.id, selected.card.version),)),
        deadline=float("inf"),
    )
    stream = RLMRunner(factory=TestingRLMFactory()).stream(prepared.execution)
    _ = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.schema_id == "skill.data-analysis"
    assert stream.outcome.prediction.schema_version == "1.0.0"
    assert set(stream.outcome.prediction.outputs) == {"answer", "findings", "metrics", "anomalies"}
    await prepared.aclose()


@pytest.mark.asyncio
async def test_daytona_report_builder_workspace_selection_keeps_workspace_host_owned(monkeypatch) -> None:
    from fleet_rlm.config import Settings
    from fleet_rlm.daytona.run_environment import _LiveCapabilityPreparer
    from fleet_rlm.files.workspace_models import WorkspaceEntry, WorkspaceListResult, WorkspaceTextPage

    class FakeWorkspace:
        last_warnings: tuple[dict[str, object], ...] = ()

        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def list_entries(self, path: str, *, limit: int = 100, after: str | None = None) -> WorkspaceListResult:
            del path
            del limit
            del after
            return WorkspaceListResult(
                tuple(WorkspaceEntry(name, "file", len(value), None) for name, value in self.values.items()), False
            )

        def stat(self, path: str) -> WorkspaceEntry | None:
            value = self.values.get(path)
            return None if value is None else WorkspaceEntry(path, "file", len(value), None)

        def read_text_page(
            self,
            path: str,
            *,
            cursor: str | None,
            max_chars: int,
            max_bytes: int,
        ) -> WorkspaceTextPage:
            value = self.values[path]
            if len(value.encode()) > max_bytes:
                raise ValueError("too large")
            if cursor is not None:
                raise ValueError("cursor")
            return WorkspaceTextPage(value[:max_chars], None, len(value.encode()), len(value) <= max_chars)

        def write_text(self, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry:
            if path in self.values and not overwrite:
                raise FileExistsError(path)
            self.values[path] = content
            return WorkspaceEntry(path, "file", len(content.encode()), None)

        def append_text(self, path: str, content: str) -> WorkspaceEntry:
            self.values[path] = self.values.get(path, "") + content
            return WorkspaceEntry(path, "file", len(self.values[path].encode()), None)

    fake_workspace = FakeWorkspace()
    monkeypatch.setattr(
        "fleet_rlm.daytona.workspace_fs.DaytonaSessionWorkspaceFS",
        lambda *_args, **_kwargs: fake_workspace,
    )
    catalog = _catalog()
    report_builder = catalog.require(stable_skill_id("report-builder"))
    workspace_files = catalog.require(stable_skill_id("workspace-files"))
    turn = _turn(
        selections=(
            SkillSelectionRef(report_builder.card.id, report_builder.card.version),
            SkillSelectionRef(workspace_files.card.id, workspace_files.card.version),
        )
    )
    settings = Settings(_env_file=None, run_environment="daytona")
    environment = SimpleNamespace(attachment_sink=SimpleNamespace(volume_fs=SimpleNamespace(sandbox=object())))
    prepared = await _LiveCapabilityPreparer(settings, catalog).prepare(
        turn,
        environment,
        PreparedAttachments((), ()),
        deadline=float("inf"),
    )

    tools = {str(tool.name): tool for tool in prepared.spec.tools}
    assert {"load_skill", "read_skill_resource"} <= tools.keys()
    assert {name for name in tools if name in {"load_skill", "read_skill_resource"}} == {
        "load_skill",
        "read_skill_resource",
    }
    assert tools["load_skill"](skill_id=str(stable_skill_id("long-context")))["error"] == "skill_not_found"
    assert tools["write_workspace_text"](path="report.md", content="# Report")["ok"] is True
    assert tools["read_workspace_text"](path="report.md")["content"] == "# Report"
    assert {detail.name for detail in prepared.drain_public_details() if detail.kind == "skill.activated"} == {
        "report-builder",
        "workspace-files",
    }
