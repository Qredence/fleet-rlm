"""The setup checklist is an HTTP client; credentials never enter chat input or history."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from rich.console import Console
from textual.widgets import Button, Input

from fleet_rlm.api.app import create_app
from fleet_rlm.setup.service import SECTIONS, SetupService
from fleet_rlm.tui.setup import SetupApp, run_setup


@pytest.fixture
def setup_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for section in SECTIONS:
        monkeypatch.setattr(
            SetupService, "check_" + section, lambda _self, _settings: {"status": "ready", "message": "Verified"}
        )
    app = create_app(setup_mode=True, setup_token="test-token", project_dir=tmp_path)
    with TestClient(app, client=("127.0.0.1", 32100), headers={"Authorization": "Bearer test-token"}) as client:
        yield client


async def idle(app, pilot):
    for _ in range(100):
        await pilot.pause(0.03)
        if not app._working:
            return
    pytest.fail("Setup operation did not finish")


@pytest.mark.asyncio
async def test_checklist_masks_secrets_and_saves_through_fastapi(setup_client, tmp_path):
    status = setup_client.get("/setup/status").json()
    app = SetupApp(setup_client, status)
    async with app.run_test(size=(140, 55)) as pilot:
        await idle(app, pilot)
        field = app.query_one("#DATABRICKS_TOKEN", Input)
        assert field.password
        field.value = "user-entered-secret"
        app.query_one("#save", Button).press()
        await idle(app, pilot)
        assert "user-entered-secret" in (tmp_path / ".env").read_text()
        assert "user-entered-secret" not in str(app.query_one("#progress").lines)
        assert not (tmp_path / ".fleet/history").exists()
        assert not app.status["dirty"]


@pytest.mark.asyncio
async def test_automatic_healthy_launch_requests_apply_and_returns_true(setup_client):
    app = SetupApp(setup_client, setup_client.get("/setup/status").json(), automatic=True)
    async with app.run_test(size=(140, 55)) as pilot:
        for _ in range(100):
            await pilot.pause(0.03)
            if app.return_value is True:
                break
        assert app.return_value is True
        assert setup_client.app.state.setup_applying


@pytest.mark.asyncio
async def test_provider_mutation_requires_the_explicit_confirmation(setup_client, monkeypatch):
    called = []
    monkeypatch.setattr(
        SetupService,
        "check_daytona",
        lambda _self, _settings: {
            "status": "blocked",
            "message": "Snapshot missing",
            "state": "missing",
        },
    )

    def built(_self, _operation):
        called.append(True)
        return {"id": "snapshot-id", "name": "versioned-snapshot"}

    monkeypatch.setattr(SetupService, "build", built)
    app = SetupApp(setup_client, setup_client.get("/setup/status").json())
    async with app.run_test(size=(140, 55)) as pilot:
        await idle(app, pilot)
        assert called == []
        app.query_one("#build", Button).press()
        await pilot.pause()
        app.screen.query_one("#cancel", Button).press()
        await pilot.pause()
        assert called == []
        app.query_one("#build", Button).press()
        await pilot.pause()
        app.screen.query_one("#confirm", Button).press()
        await idle(app, pilot)
        assert called == [True]
        assert "FLEET_DAYTONA_VOLUME_ID" not in setup_client.app.state.setup_service.draft


def test_noninteractive_setup_reports_blockers_without_consuming_stdin(setup_client, monkeypatch):
    monkeypatch.setattr(
        SetupService, "check_daytona", lambda _self, _settings: {"status": "blocked", "message": "Snapshot missing"}
    )
    monkeypatch.setattr("fleet_rlm.tui.setup.sys.stdin", SimpleNamespace(isatty=lambda: False))
    stream = io.StringIO()
    assert not run_setup(setup_client, console=Console(file=stream, force_terminal=False))
    assert "Snapshot missing" in stream.getvalue()


@pytest.mark.asyncio
async def test_small_terminal_controls_and_keyboard_build_action(setup_client, monkeypatch):
    monkeypatch.setattr(
        SetupService,
        "check_daytona",
        lambda _self, _settings: {
            "status": "blocked",
            "message": "Snapshot missing",
            "state": "missing",
        },
    )
    app = SetupApp(setup_client, setup_client.get("/setup/status").json())
    async with app.run_test(size=(80, 24)) as pilot:
        await idle(app, pilot)
        for name in ("save", "verify", "reconnect", "apply", "leave", "discard"):
            region = app.query_one("#" + name, Button).region
            assert region.right <= 80 and region.bottom <= 24
        app.query_one("#DATABRICKS_HOST", Input).focus()
        await pilot.press("alt+b")
        await pilot.pause()
        assert app.screen.query_one("#confirm", Button)
        app.screen.query_one("#cancel", Button).press()


def test_normal_chat_client_does_not_carry_the_setup_capability(monkeypatch):
    import httpx

    from fleet_rlm.tui import app as chat

    captured = []
    client_class = httpx.Client

    def client(**kwargs):
        captured.append(kwargs)
        return client_class(**kwargs, transport=httpx.MockTransport(lambda _request: httpx.Response(200)))

    monkeypatch.setattr(chat.httpx, "Client", client)
    monkeypatch.setattr(chat.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(chat.CONSOLE, "input", lambda *_a, **_kw: "/exit")
    chat.run_interactive_cli("http://server.test", "setup-session", setup_token="private-capability")
    assert "private-capability" not in repr(captured)


@pytest.mark.asyncio
async def test_unsaved_inputs_cannot_build_in_the_previous_organization(setup_client, monkeypatch):
    monkeypatch.setattr(
        SetupService,
        "check_daytona",
        lambda _self, _settings: {
            "status": "blocked",
            "message": "Snapshot missing",
            "state": "missing",
        },
    )
    app = SetupApp(setup_client, setup_client.get("/setup/status").json())
    async with app.run_test(size=(100, 40)) as pilot:
        await idle(app, pilot)
        assert not app.query_one("#build", Button).disabled
        field = app.query_one("#FLEET_DAYTONA_ORG_ID", Input)
        original = field.value
        field.value = "new-organization"
        await pilot.pause()
        for name in ("build", "activate", "model-test", "list-volumes", "verify"):
            assert app.query_one("#" + name, Button).disabled
        assert not app.query_one("#save", Button).disabled
        field.value = original
        await pilot.pause()
        assert not app.query_one("#build", Button).disabled
