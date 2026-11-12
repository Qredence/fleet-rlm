"""Browser terminal security and local setup UI interactions."""

from __future__ import annotations

import asyncio
import io
import shlex
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from rich.console import Console
from textual.widgets import Button, Input

from fleet_rlm.api.app import create_app
from fleet_rlm.config import FleetSettings
from fleet_rlm.setup import SECTIONS, SetupService
from fleet_rlm.tui import web, web_client
from fleet_rlm.tui.setup import SetupApp, run_setup

# Browser terminal security


def test_build_web_app_command_quotes_arguments_and_preserves_options() -> None:
    command = web.build_web_app_command(
        api_url="http://fleet.test/api's-root",
        mlflow_mode="local",
        session_id="browser-session",
        mlflow_port=5100,
        mlflow_experiment="team's runtime",
    )

    assert shlex.split(command) == [
        web.sys.executable,
        "-m",
        "fleet_rlm.tui.web_client",
        "--url",
        "http://fleet.test/api's-root",
        "--mlflow",
        "local",
        "--session",
        "browser-session",
        "--mlflow-port",
        "5100",
        "--mlflow-experiment",
        "team's runtime",
    ]


def test_build_web_app_command_rejects_credential_bearing_api_urls() -> None:
    import pytest

    with pytest.raises(ValueError, match="without credentials"):
        web.build_web_app_command(
            api_url="https://user:secret@fleet.test",
            mlflow_mode="local",
        )


def test_serve_web_client_configures_textual_serve(monkeypatch) -> None:
    captured = {}

    class FakeServer:
        def __init__(self, command, **kwargs):
            captured.update(command=command, **kwargs)

        def serve(self):
            captured["served"] = True

    monkeypatch.setattr(web, "FleetWebServer", FakeServer)

    web.serve_web_client(
        api_url="http://fleet.test",
        mlflow_mode="local",
        host="127.0.0.1",
        port=8080,
        public_url="https://fleet.example.test",
    )

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8080
    assert captured["title"] == "Fleet RLM Lite"
    assert captured["public_url"] == "https://fleet.example.test"
    assert shlex.split(captured["command"])[-2:] == ["--mlflow", "local"]
    assert captured["served"] is True


@pytest.mark.parametrize(
    ("public_url", "origin", "host"),
    [
        (None, "http://127.0.0.1:8080", "127.0.0.1:8080"),
        ("https://fleet.example.test/proxy", "https://FLEET.example.test:443", "fleet.example.test"),
        ("http://fleet.example.test:80", "http://fleet.example.test", "fleet.example.test:80"),
        ("http://[::1]:8080", "http://[::1]:8080", "[::1]:8080"),
    ],
)
def test_terminal_handshake_delegates_only_for_configured_origin(monkeypatch, public_url, origin, host):
    from aiohttp.test_utils import make_mocked_request
    from textual_serve.server import Server

    accepted = []
    response = object()

    async def handle(_self, request):
        accepted.append(request)
        return response

    monkeypatch.setattr(Server, "handle_websocket", handle)
    server = web.FleetWebServer("unused", host="127.0.0.1", port=8080, public_url=public_url)
    request = make_mocked_request("GET", "/ws", headers={"Origin": origin, "Host": host})
    assert asyncio.run(server.handle_websocket(request)) is response
    assert accepted == [request]


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        ("https://attacker.example", "fleet.example.test"),
        (None, "fleet.example.test"),
        ("null", "fleet.example.test"),
        ("https://fleet.example.test/path", "fleet.example.test"),
        ("https://user@fleet.example.test", "fleet.example.test"),
        ("https://fleet.example.test:444", "fleet.example.test"),
        ("http://fleet.example.test", "fleet.example.test"),
        ("https://fleet.example.test.evil", "fleet.example.test"),
        ("https://fleet.example.test", "attacker.example"),
        ("https://attacker.example", "attacker.example"),
        ("https://fleet.example.test", "fleet.example.test:444"),
        ("https://fleet.example.test https://attacker.example", "fleet.example.test"),
        ("https://fleet.example.test:bad", "fleet.example.test"),
        ("https://fleet.example.test?", "fleet.example.test"),
        ("https://fleet.example.test#", "fleet.example.test"),
        ("https://fleet.example.test:", "fleet.example.test"),
    ],
)
def test_terminal_rejection_precedes_websocket_acceptance_and_process_start(monkeypatch, origin, host):
    from aiohttp import web as aiohttp_web
    from aiohttp.test_utils import make_mocked_request
    from textual_serve import server as textual_server

    def forbidden(*_args, **_kwargs):
        pytest.fail("rejected handshake must not accept a socket or construct an AppService")

    monkeypatch.setattr(textual_server.web, "WebSocketResponse", forbidden)
    monkeypatch.setattr(textual_server, "AppService", forbidden)
    server = web.FleetWebServer("unused", public_url="https://fleet.example.test")
    headers = {"Host": host, "X-Forwarded-Host": "fleet.example.test"}
    if origin is not None:
        headers["Origin"] = origin
    request = make_mocked_request("GET", "/ws", headers=headers)
    with pytest.raises(aiohttp_web.HTTPForbidden):
        asyncio.run(server.handle_websocket(request))


@pytest.mark.parametrize("header", ["Origin", "Host"])
def test_terminal_rejects_duplicate_security_headers(header):
    from aiohttp import web as aiohttp_web
    from aiohttp.test_utils import make_mocked_request
    from multidict import CIMultiDict

    headers = CIMultiDict({"Origin": "https://fleet.example.test", "Host": "fleet.example.test"})
    headers.add(header, headers[header])
    server = web.FleetWebServer("unused", public_url="https://fleet.example.test")
    request = make_mocked_request("GET", "/ws", headers=headers)
    with pytest.raises(aiohttp_web.HTTPForbidden):
        asyncio.run(server.handle_websocket(request))


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "fleet.example.test"])
@pytest.mark.parametrize("token", [None, "", "short", "x" * 32 + "\n"])
def test_non_loopback_web_listener_requires_secret(monkeypatch, host, token):
    monkeypatch.delenv("FLEET_WEB_AUTH_TOKEN", raising=False)
    if token is not None:
        monkeypatch.setenv("FLEET_WEB_AUTH_TOKEN", token)
    with pytest.raises(ValueError, match="FLEET_WEB_AUTH_TOKEN"):
        web.FleetWebServer("unused", host=host)


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic " + "s" * 32, "duplicate", "valid"])
def test_non_loopback_websocket_requires_secret_before_start(monkeypatch, authorization):
    from aiohttp import web as aiohttp_web
    from aiohttp.test_utils import make_mocked_request
    from multidict import CIMultiDict
    from textual_serve.server import Server

    token = "s" * 32
    monkeypatch.setenv("FLEET_WEB_AUTH_TOKEN", token)
    accepted = []

    async def handle(_self, request):
        accepted.append(request)
        return "accepted"

    monkeypatch.setattr(Server, "handle_websocket", handle)
    server = web.FleetWebServer("unused", host="0.0.0.0", public_url="https://fleet.example.test")
    headers = CIMultiDict({"Origin": "https://fleet.example.test", "Host": "fleet.example.test"})
    if authorization in {"valid", "duplicate"}:
        headers.add("Authorization", f"Bearer {token}")
        if authorization == "duplicate":
            headers.add("Authorization", f"Bearer {token}")
    elif authorization is not None:
        headers.add("Authorization", authorization)
    request = make_mocked_request("GET", "/ws", headers=headers)
    if authorization == "valid":
        assert asyncio.run(server.handle_websocket(request)) == "accepted"
        assert accepted == [request]
    else:
        with pytest.raises(aiohttp_web.HTTPUnauthorized):
            asyncio.run(server.handle_websocket(request))
        assert accepted == []


def test_authenticated_non_loopback_client_still_requires_matching_origin(monkeypatch):
    from aiohttp import web as aiohttp_web
    from aiohttp.test_utils import make_mocked_request

    monkeypatch.setenv("FLEET_WEB_AUTH_TOKEN", "s" * 32)
    server = web.FleetWebServer("unused", host="0.0.0.0", public_url="https://fleet.example.test")
    request = make_mocked_request(
        "GET",
        "/ws",
        headers={"Authorization": "Bearer " + "s" * 32, "Origin": "https://evil.test", "Host": "fleet.example.test"},
    )
    with pytest.raises(aiohttp_web.HTTPForbidden):
        asyncio.run(server.handle_websocket(request))


@pytest.mark.parametrize(
    ("arguments", "expected_session", "mlflow_mode"),
    [
        (["--mlflow", "databricks"], "generated", "databricks"),
        (["--session", "shared-browser-session"], "shared-browser-session", "local"),
    ],
)
def test_web_client_session_and_mlflow_configuration(monkeypatch, arguments, expected_session, mlflow_mode) -> None:
    settings = FleetSettings(_env_file=None)
    selected = []
    apps = []

    class FakeApp:
        def __init__(self, _client, session_id, state):
            apps.append((session_id, state))

        def run(self):
            pass

    monkeypatch.setattr(web_client, "get_settings", lambda mode: selected.append(mode) or settings)
    monkeypatch.setattr(web_client, "FleetTextualApp", FakeApp)

    web_client.main(["--url", "http://fleet.test", *arguments])

    assert selected == [mlflow_mode]
    session_id, state = apps[0]
    if expected_session == "generated":
        assert session_id.startswith("sess_web_")
    else:
        assert session_id == expected_session
    assert state.mlflow_settings is settings


# Local setup UI


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


def _block_daytona(monkeypatch):
    monkeypatch.setattr(
        SetupService,
        "check_daytona",
        lambda _self, _settings: {
            "status": "blocked",
            "message": "Snapshot missing",
            "state": "missing",
        },
    )


def running_setup(app, size):
    return app.run_test(size=size)


@pytest.mark.asyncio
async def test_checklist_masks_secrets_and_saves_through_fastapi(setup_client, tmp_path):
    status = setup_client.get("/setup/status").json()
    app = SetupApp(setup_client, status)
    async with running_setup(app, (140, 55)) as pilot:
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
    async with running_setup(app, (140, 55)) as pilot:
        for _ in range(100):
            await pilot.pause(0.03)
            if app.return_value is True:
                break
        assert app.return_value is True
        assert setup_client.app.state.setup_applying


@pytest.mark.asyncio
async def test_provider_mutation_requires_the_explicit_confirmation(setup_client, monkeypatch):
    called = []
    _block_daytona(monkeypatch)

    def built(_self, _operation):
        called.append(True)
        return {"id": "snapshot-id", "name": "versioned-snapshot"}

    monkeypatch.setattr(SetupService, "build", built)
    app = SetupApp(setup_client, setup_client.get("/setup/status").json())
    async with running_setup(app, (140, 55)) as pilot:
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


def test_noninteractive_setup_applies_when_only_model_discovery_warns(setup_client, monkeypatch):
    monkeypatch.setattr(
        SetupService,
        "check_models",
        lambda _self, _settings: {"status": "warning", "message": "Model discovery is unavailable."},
    )
    monkeypatch.setattr("fleet_rlm.tui.setup.sys.stdin", SimpleNamespace(isatty=lambda: False))
    stream = io.StringIO()

    assert run_setup(setup_client, console=Console(file=stream, force_terminal=False))
    assert "models: Model discovery is unavailable." in stream.getvalue()
    assert setup_client.app.state.setup_applying


@pytest.mark.asyncio
async def test_small_terminal_controls_and_keyboard_build_action(setup_client, monkeypatch):
    _block_daytona(monkeypatch)
    app = SetupApp(setup_client, setup_client.get("/setup/status").json())
    async with running_setup(app, (80, 24)) as pilot:
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
    _block_daytona(monkeypatch)
    app = SetupApp(setup_client, setup_client.get("/setup/status").json())
    async with running_setup(app, (100, 40)) as pilot:
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
