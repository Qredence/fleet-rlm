from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.server.config import ServerRuntimeConfig
from fleet_rlm.server.main import create_app

from ._fakes import FakeChatAgent

pytest.importorskip("fastapi")
pytest.importorskip("websockets")


@pytest.fixture
def fake_agent() -> FakeChatAgent:
    return FakeChatAgent()


@pytest.fixture
def ws_test_app(monkeypatch: pytest.MonkeyPatch, fake_agent: FakeChatAgent):
    def _fake_build_agent(**kwargs):
        _ = kwargs
        return fake_agent

    monkeypatch.setattr("fleet_rlm.runners.build_react_chat_agent", _fake_build_agent)
    config = ServerRuntimeConfig(
        app_env="local",
        database_required=False,
        database_url=None,
        db_validate_on_startup=False,
        secret_name="TEST_SECRET",
        volume_name="test-volume",
        timeout=60,
        react_max_iters=5,
        rlm_max_iterations=10,
        rlm_max_llm_calls=15,
    )
    return create_app(config=config)


@pytest.fixture
def ws_client(ws_test_app):
    with TestClient(ws_test_app) as client:
        yield client
