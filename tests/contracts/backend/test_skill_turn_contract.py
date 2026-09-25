"""Public Turn selection, preparation, and progressive Skill-loading contract."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from hashlib import sha256
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api.dependencies import get_turn_runtime
from fleet_rlm.api.errors import install_error_handlers
from fleet_rlm.api.routes.turns import router as turns_router
from fleet_rlm.api.schemas import CreateTurnRequest
from fleet_rlm.attachments import AttachmentRef, PreparedAttachments, StagedAttachment
from fleet_rlm.config.settings import Settings
from fleet_rlm.paths import volume_paths_from_settings
from fleet_rlm.rlm.events import EventRecorder, RuntimeEvent
from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
from fleet_rlm.sessions.run_state import (
    ClaimedRun,
    _RunClaimToken,
)
from fleet_rlm.skills.catalog import SkillCatalog, build_bundled_skill_catalog, stable_skill_id
from fleet_rlm.skills.errors import InvalidSkillSelectionError
from fleet_rlm.skills.models import SkillSelectionRef
from fleet_rlm.turns import OpenTurnCommand
from fleet_rlm.workspace.storage import DaytonaSandboxWorkspaceStorage, WorkspaceMemoryStorage


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

    def open_owned(self, command: OpenTurnCommand):
        from fleet_rlm.turns import OpenedTurnStream

        self.command = command
        if self.error is not None:

            async def fail() -> _EmptyOpenedTurn:
                raise self.error

            return OpenedTurnStream(None, open_task=asyncio.create_task(fail()))
        opened = _EmptyOpenedTurn()
        return OpenedTurnStream(opened.run_id, opened.__aiter__())


class _DaytonaFilesystem:
    """In-memory implementation of the SDK filesystem surface used by storage."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.directories = {"/"}

    @staticmethod
    def _path(value: str) -> str:
        return str(PurePosixPath(value))

    async def get_file_info(self, value: str) -> dict[str, object]:
        path = self._path(value)
        if path in self.directories:
            return {"is_dir": True, "is_symlink": False, "size": 0, "mod_time": "0"}
        if path in self.files:
            return {"is_dir": False, "is_symlink": False, "size": len(self.files[path]), "mod_time": "0"}
        raise FileNotFoundError(path)

    async def list_files(self, value: str, *, depth: int) -> list[dict[str, object]]:
        del depth
        path = self._path(value)
        if path not in self.directories:
            raise FileNotFoundError(path)
        entries = []
        for child in self.directories | self.files.keys():
            candidate = PurePosixPath(child)
            if candidate.parent != PurePosixPath(path):
                continue
            is_dir = child in self.directories
            entries.append(
                {
                    "path": child,
                    "is_dir": is_dir,
                    "is_symlink": False,
                    "size": 0 if is_dir else len(self.files[child]),
                    "mod_time": "0",
                }
            )
        return entries

    async def download_file(self, value: str) -> bytes:
        try:
            return self.files[self._path(value)]
        except KeyError as exc:
            raise FileNotFoundError(value) from exc

    async def upload_file(self, data: bytes, value: str) -> None:
        path = PurePosixPath(self._path(value))
        parents = tuple(path.parents)
        self.directories.update(str(parent) for parent in parents)
        self.files[str(path)] = bytes(data)

    async def delete_file(self, value: str) -> None:
        self.files.pop(self._path(value), None)


def _live_capability_environment(settings: Settings, session_id: UUID):
    from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher
    from fleet_rlm.daytona.turn_environment import _DaytonaRunSink
    from fleet_rlm.paths import volume_paths_from_settings
    from fleet_rlm.turn_preparation import RunEnvironment
    from fleet_rlm.workspace.memory import build_workspace_memory_store
    from tests.support.workspace_storage import daytona_host_io_for_test_sandbox

    paths = volume_paths_from_settings(settings)
    sandbox = SimpleNamespace(fs=_DaytonaFilesystem())
    dispatcher = SyncBridgeDispatcher()
    dispatcher.set_loop(asyncio.get_running_loop())
    host_io = daytona_host_io_for_test_sandbox(
        sandbox,
        workspace_id=uuid4(),
        dispatcher=dispatcher,
        volume_root=str(paths.mount_path),
        max_file_bytes=settings.max_upload_bytes,
    )
    sink = _DaytonaRunSink(
        sandbox,
        dispatcher=dispatcher,
        paths=paths,
        host_io=host_io,
        run_id=uuid4(),
    )
    memory_session = DaytonaSandboxWorkspaceStorage(
        sink.sandbox,
        volume_root=str(paths.mount_path),
        root=str(paths.mount_path),
        max_file_bytes=settings.max_upload_bytes,
        allow_volume_root=True,
    )
    memory_store = build_workspace_memory_store(
        WorkspaceMemoryStorage(memory_session),
        max_upload_bytes=settings.max_upload_bytes,
    )
    session_workspace = DaytonaSandboxWorkspaceStorage(
        sink.sandbox,
        volume_root=str(paths.mount_path),
        root=str(paths.session_workspace_dir(session_id)),
        max_file_bytes=settings.max_upload_bytes,
    )
    project_workspace = DaytonaSandboxWorkspaceStorage(
        sink.sandbox,
        volume_root=str(paths.mount_path),
        root=str(paths.projects_root()),
        max_file_bytes=settings.max_upload_bytes,
    )

    async def release() -> None:
        return None

    return RunEnvironment(
        interpreter=None,
        attachment_sink=sink,
        artifact_sink=sink,
        release=release,
        workspace_memory_store=memory_store,
        volume_fs=sink.volume_fs,
        session_workspace=session_workspace,
        project_workspace=project_workspace,
    )


def _turn_client(coordinator: _Coordinator) -> TestClient:
    app = FastAPI()
    app.state.settings = Settings()
    app.dependency_overrides[get_turn_runtime] = lambda: coordinator
    install_error_handlers(app)
    app.include_router(turns_router)
    return TestClient(app)


def _catalog() -> SkillCatalog:
    return build_bundled_skill_catalog()


def _turn(
    *,
    selections: tuple[SkillSelectionRef, ...] = (),
    attachment_ids: tuple[UUID, ...] = (),
) -> ClaimedRun:
    async def not_cancelled() -> bool:
        return False

    return ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("analyze the supplied material", attachment_ids, selections),
        SessionHistory(()),
        not_cancelled,
        _RunClaimToken(uuid4()),
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
    from tests.support.testing_app import TestingCapabilityPreparer, TestingRunEnvironmentProvider

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
    assert prepared.spec.skill_cards == (selected.card,)
    assert other.card not in prepared.spec.skill_cards
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
    from fleet_rlm.config.settings import Settings
    from fleet_rlm.skills.tools import SkillToolHost
    from fleet_rlm.turn_preparation import DaytonaCapabilityPreparer

    catalog = _catalog()
    selected = catalog.require(stable_skill_id("long-context"))
    host = SkillToolHost(catalog)
    resource_path = next(iter(selected.resources))
    assert host.read_skill_resource(str(selected.card.id), resource_path) == {"ok": False, "error": "skill_not_loaded"}
    assert host.load_skill(str(selected.card.id), selected.card.version)["ok"] is True
    assert host.read_skill_resource(str(selected.card.id), resource_path, selected.card.version)["ok"] is True

    settings = Settings(run_environment="daytona")
    turn = _turn()
    environment = _live_capability_environment(settings, turn.session_id)
    prepared = await DaytonaCapabilityPreparer(settings, catalog, volume_paths_from_settings(settings)).prepare(
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
    environment.attachment_sink.sandbox.close()


@pytest.mark.asyncio
async def test_data_analysis_signature_and_report_builder_selection_use_host_tools_only() -> None:
    from tests.support.testing_app import TestingCapabilityPreparer, TestingRunEnvironmentProvider

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

    assert prepared.spec.skill_cards == (data_analysis.card, report_builder.card)
    tools_by_name = {str(tool.name): tool for tool in prepared.spec.tools}
    assert tools_by_name["load_skill"](skill_id=str(stable_skill_id("long-context")))["error"] == "skill_not_found"
    assert prepared.spec.output_schema_id == "skill.data-analysis"
    assert prepared.spec.output_schema_version == "1.2.0"
    assert prepared.spec.signature.output_fields["answer"].annotation is str
    assert set(prepared.spec.signature.output_fields) == {"answer", "findings", "metrics", "anomalies"}
    assert {str(tool.name) for tool in prepared.spec.tools} == {
        "read_attachment",
        "read_session_history",
        "load_skill",
        "read_skill_resource",
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
    from fleet_rlm.rlm.execution import RLMRunner
    from tests.support.testing_app import DeterministicTurnPreparation, build_testing_rlm

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
    stream = RLMRunner(program_builder=build_testing_rlm).stream(prepared.execution)
    _ = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.result_contract is not None
    from fleet_rlm.rlm.result import validate_prediction

    projected = validate_prediction(stream.outcome.prediction, stream.outcome.result_contract)
    assert projected.schema_id == "skill.data-analysis"
    assert projected.schema_version == "1.2.0"
    assert set(projected.outputs) == {"answer", "findings", "metrics", "anomalies"}
    await prepared.aclose()


@pytest.mark.asyncio
async def test_daytona_report_builder_workspace_selection_keeps_workspace_host_owned() -> None:
    from fleet_rlm.config.settings import Settings
    from fleet_rlm.turn_preparation import DaytonaCapabilityPreparer

    catalog = _catalog()
    report_builder = catalog.require(stable_skill_id("report-builder"))
    workspace_files = catalog.require(stable_skill_id("workspace-files"))
    turn = _turn(
        selections=(
            SkillSelectionRef(report_builder.card.id, report_builder.card.version),
            SkillSelectionRef(workspace_files.card.id, workspace_files.card.version),
        )
    )
    settings = Settings(run_environment="daytona")
    environment = _live_capability_environment(settings, turn.session_id)
    prepared = await DaytonaCapabilityPreparer(settings, catalog, volume_paths_from_settings(settings)).prepare(
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
    written = await asyncio.to_thread(tools["write_workspace_text"], path="report.md", content="# Report")
    read = await asyncio.to_thread(tools["read_workspace_text"], path="report.md")
    assert written["ok"] is True
    assert read["content"] == "# Report"
    environment.attachment_sink.sandbox.close()
    assert {detail.name for detail in prepared.drain_public_details() if detail.kind == "skill.activated"} == {
        "report-builder",
        "workspace-files",
    }
