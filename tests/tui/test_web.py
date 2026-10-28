from __future__ import annotations

import asyncio
import shlex

import pytest

from fleet_rlm.config import FleetSettings
from fleet_rlm.tui import web, web_client


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
