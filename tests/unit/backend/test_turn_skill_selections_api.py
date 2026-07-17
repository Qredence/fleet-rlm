"""Version-pinned Skill selections at the prepare-before-headers API boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api.errors import install_error_handlers
from fleet_rlm.api.routes.turns import router
from fleet_rlm.api.schemas import CreateTurnRequest
from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.rlm.events import RuntimeEvent
from fleet_rlm.skills.authorize import InvalidSkillSelectionError


class _EmptyOpenedTurn:
    def __init__(self) -> None:
        self.run_id = uuid4()

    def __aiter__(self) -> AsyncIterator[RuntimeEvent]:
        return self._events()

    async def _events(self) -> AsyncIterator[RuntimeEvent]:
        if False:
            yield

    async def aclose(self) -> None:
        return None


class _CapturingCoordinator:
    def __init__(self, error: BaseException | None = None) -> None:
        self.command: OpenTurnCommand | None = None
        self.error = error

    async def open(self, command: OpenTurnCommand) -> _EmptyOpenedTurn:
        self.command = command
        if self.error is not None:
            raise self.error
        return _EmptyOpenedTurn()


def _client(coordinator: _CapturingCoordinator) -> TestClient:
    app = FastAPI()
    app.state.composition_ready = True
    app.state.turn_coordinator = coordinator
    install_error_handlers(app)
    app.include_router(router)
    return TestClient(app)


def test_create_turn_request_accepts_at_most_four_unique_skill_ids() -> None:
    ids = [uuid4() for _ in range(4)]

    request = CreateTurnRequest.model_validate(
        {
            "text": "inspect",
            "skill_selections": [{"id": str(skill_id), "expected_version": "2.0.0"} for skill_id in ids],
        }
    )

    assert [selection.id for selection in request.skill_selections] == ids

    with pytest.raises(ValueError):
        CreateTurnRequest.model_validate(
            {
                "text": "inspect",
                "skill_selections": [
                    {"id": str(skill_id), "expected_version": "2.0.0"} for skill_id in [*ids, uuid4()]
                ],
            }
        )

    with pytest.raises(ValueError):
        CreateTurnRequest.model_validate(
            {
                "text": "inspect",
                "skill_selections": [
                    {"id": str(ids[0]), "expected_version": "2.0.0"},
                    {"id": str(ids[0]), "expected_version": "1.0.0"},
                ],
            }
        )


def test_turn_route_passes_exact_selections_into_persisted_input() -> None:
    coordinator = _CapturingCoordinator()
    skill_id = uuid4()

    with _client(coordinator) as client:
        response = client.post(
            f"/api/sessions/{uuid4()}/turns",
            json={
                "text": "inspect",
                "skill_selections": [{"id": str(skill_id), "expected_version": "2.0.0"}],
            },
            headers={"Idempotency-Key": "skill-selection"},
        )

    assert response.status_code == 200
    assert coordinator.command is not None
    assert [(selection.id, selection.expected_version) for selection in coordinator.command.input.skill_selections] == [
        (skill_id, "2.0.0")
    ]


def test_invalid_skill_selection_is_a_generic_pre_header_422() -> None:
    secret = "private-version-detail"
    coordinator = _CapturingCoordinator(InvalidSkillSelectionError())

    with _client(coordinator) as client:
        response = client.post(
            f"/api/sessions/{uuid4()}/turns",
            json={
                "text": "inspect",
                "skill_selections": [{"id": str(uuid4()), "expected_version": secret}],
            },
            headers={"Idempotency-Key": "invalid-skill-selection"},
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "code": "invalid_skill_selection",
        "message": "Invalid Skill selection",
    }
    assert secret not in response.text


def test_skill_selection_request_rejects_extra_fields_and_blank_versions() -> None:
    skill_id = UUID(int=1)

    for selection in (
        {"id": str(skill_id), "expected_version": " "},
        {"id": str(skill_id), "expected_version": "1.0.0", "instructions": "private"},
    ):
        with pytest.raises(ValueError):
            CreateTurnRequest.model_validate({"text": "inspect", "skill_selections": [selection]})


@pytest.mark.parametrize(
    "selections",
    [
        [{"id": "not-a-uuid", "expected_version": "1.0.0"}],
        [
            {"id": str(UUID(int=1)), "expected_version": "1.0.0"},
            {"id": str(UUID(int=1)), "expected_version": "1.0.0"},
        ],
        [{"id": str(UUID(int=value)), "expected_version": "1.0.0"} for value in range(1, 6)],
    ],
)
def test_structurally_invalid_skill_selection_is_a_generic_http_422(selections: list[dict[str, str]]) -> None:
    with _client(_CapturingCoordinator()) as client:
        response = client.post(
            f"/api/sessions/{uuid4()}/turns",
            json={"text": "inspect", "skill_selections": selections},
            headers={"Idempotency-Key": "invalid-structure"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_skill_selection",
        "message": "Invalid Skill selection",
    }
