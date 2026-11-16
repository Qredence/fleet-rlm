from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api.deps import get_store
from fleet_rlm.api.routes.chat import router


class ActivityStore:
    def __init__(self, saved: bool = True) -> None:
        self.saved = saved
        self.call: tuple[str, str, list[dict[str, object]]] | None = None

    def save_message_activity(self, session_id: str, message_id: str, activity: list[dict[str, object]]) -> bool:
        self.call = (session_id, message_id, activity)
        return self.saved


def client_for(store: ActivityStore) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_store] = lambda: store
    return TestClient(app)


def test_activity_is_saved_on_completed_assistant_message():
    store = ActivityStore()
    activity = [{"type": "data-fleetActivity", "id": "route", "data": {"kind": "route"}}]

    response = client_for(store).post(
        "/sessions/session-a/messages/turn-1_assistant/activity", json={"activity": activity}
    )

    assert response.status_code == 204
    assert store.call == ("session-a", "turn-1_assistant", activity)


def test_activity_rejects_missing_or_invalid_message_and_oversized_payloads():
    assert (
        client_for(ActivityStore(saved=False))
        .post("/sessions/session-a/messages/turn-1_assistant/activity", json={"activity": []})
        .status_code
        == 404
    )
    client = client_for(ActivityStore())
    assert (
        client.post(
            "/sessions/session-a/messages/turn-1_assistant/activity", json={"activity": [{"type": "text"}]}
        ).status_code
        == 422
    )
    oversized = [{"type": "data-fleetActivity", "id": "x", "data": {"detail": "x" * (512 * 1024)}}]
    assert (
        client.post("/sessions/session-a/messages/turn-1_assistant/activity", json={"activity": oversized}).status_code
        == 422
    )
