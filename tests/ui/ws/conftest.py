from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.ui.fixtures_ui import FakeChatAgent, build_ws_test_app

pytest.importorskip("fastapi")
pytest.importorskip("websockets")


@pytest.fixture
def fake_agent() -> FakeChatAgent:
    return FakeChatAgent()


@pytest.fixture
def ws_test_app(monkeypatch: pytest.MonkeyPatch, fake_agent: FakeChatAgent):
    return build_ws_test_app(monkeypatch, fake_agent)


@pytest.fixture
def ws_client(ws_test_app):
    with TestClient(ws_test_app) as client:
        yield client
