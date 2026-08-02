"""Unit contracts for the local development Daytona tunnel probe controller."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import IO
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import pytest

_SCRIPT = Path(__file__).parents[3] / "scripts" / "live_daytona_tunnel_probe.py"


@pytest.fixture
def subject(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """
    Load the tunnel probe script with a deterministic health status.
    
    Returns:
        ModuleType: The loaded and patched tunnel probe module.
    """
    spec = importlib.util.spec_from_file_location("live_daytona_tunnel_probe_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_health_status", lambda _origin: "http-200")
    return module


def _request(url: str, *, token: str = "", data: bytes | None = None, method: str = "GET") -> dict[str, object]:
    """
    Send an HTTP request and decode its JSON response.
    
    Parameters:
    	url (str): The request URL.
    	token (str): Optional bearer token for authorization.
    	data (bytes | None): Optional request body.
    	method (str): The HTTP method to use.
    
    Returns:
    	dict[str, object]: The decoded JSON response, or an empty dictionary when the response has no body.
    """
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )
    with urlopen(request, timeout=2) as response:
        body = response.read()
        return json.loads(body.decode()) if body else {}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> Request | None:
        del req, fp, code, msg, headers, newurl
        return None


def test_controller_keeps_observation_credentials_loopback_only(subject: ModuleType) -> None:
    state = subject._ControllerState(  # type: ignore[attr-defined]
        allowed_origin="https://allow.trycloudflare.com",
        denied_origin="https://deny.trycloudflare.com",
    )
    controller = subject._ProbeController(state)  # type: ignore[attr-defined]
    thread = threading.Thread(target=controller.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(HTTPError) as unauthorized:
            _request(f"{controller.controller_url}/v1/probes", data=b"{}", method="POST")
        assert unauthorized.value.code == 403

        created = _request(
            f"{controller.controller_url}/v1/probes",
            token=state.token,
            data=b'{"schema":"test"}',
            method="POST",
        )
        probe_id = str(created["probe_id"])
        assert created["allowed_domain"] == "allow.trycloudflare.com"
        assert created["denied_domain"] == "deny.trycloudflare.com"

        _request(f"{controller.controller_url}/probe/{probe_id}/allowed")
        with pytest.raises(HTTPError) as redirect:
            build_opener(_NoRedirect()).open(
                Request(f"{controller.controller_url}/probe/{probe_id}/redirect"), timeout=2
            )
        assert redirect.value.code == 302
        _request(
            f"{controller.controller_url}/probe/{probe_id}/denied",
            data=b"synthetic-marker",
            method="POST",
        )
        observed = _request(f"{controller.controller_url}/v1/probes/{probe_id}", token=state.token)
        assert observed == {"allowed_requests": 1, "redirect_requests": 1, "denied_requests": 1}
    finally:
        controller.shutdown()
        controller.server_close()
        thread.join(timeout=2)


def test_named_tunnel_configuration_requires_two_bare_https_origins(
    subject: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Verify that named-tunnel configuration accepts two bare HTTPS origins and rejects paths or incomplete settings.
    """
    credentials = tmp_path / "tunnel-credentials.json"
    credentials.touch()
    environment = {
        "FLEET_OPTIMIZATION_NAMED_TUNNEL_ID": "tunnel-id",
        "FLEET_OPTIMIZATION_NAMED_TUNNEL_CREDENTIALS_FILE": str(credentials),
        "FLEET_OPTIMIZATION_NAMED_TUNNEL_ALLOWED_ORIGIN": "https://allow.example.test",
        "FLEET_OPTIMIZATION_NAMED_TUNNEL_DENIED_ORIGIN": "https://deny.example.test",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    configuration = subject._configured_named_tunnel()  # type: ignore[attr-defined]
    assert configuration is not None
    assert configuration.allowed_origin == "https://allow.example.test"
    assert configuration.denied_origin == "https://deny.example.test"

    monkeypatch.setenv("FLEET_OPTIMIZATION_NAMED_TUNNEL_DENIED_ORIGIN", "https://deny.example.test/path")
    with pytest.raises(subject.TunnelProbeError, match="bare HTTPS origins"):  # type: ignore[attr-defined]
        subject._configured_named_tunnel()  # type: ignore[attr-defined]

    monkeypatch.delenv("FLEET_OPTIMIZATION_NAMED_TUNNEL_ID")
    with pytest.raises(subject.TunnelProbeError, match="incomplete"):  # type: ignore[attr-defined]
        subject._configured_named_tunnel()  # type: ignore[attr-defined]


def test_gateway_broker_delivers_only_claimed_host_tool_calls(subject: ModuleType) -> None:
    """
    Verify that the gateway broker delivers a claimed host's tool call and returns its submitted result.
    """
    state = subject._ControllerState(  # type: ignore[attr-defined]
        allowed_origin="https://allow.trycloudflare.com",
        denied_origin="https://deny.trycloudflare.com",
    )
    controller = subject._ProbeController(state)  # type: ignore[attr-defined]
    thread = threading.Thread(target=controller.serve_forever, daemon=True)
    thread.start()
    try:
        broker = _request(
            f"{controller.controller_url}/v1/brokers",
            token=state.token,
            data=b"{}",
            method="POST",
        )
        broker_id = str(broker["broker_id"])
        broker_secret = str(broker["broker_secret"])
        result_box: list[dict[str, object]] = []

        def invoke() -> None:
            """
            Submit a broker tool-call request and store the decoded response.
            """
            request = Request(
                f"{controller.controller_url}/broker/{broker_id}/call",
                data=b'{"tool_name":"read_curated_input","args":[],"kwargs":{"transaction_id":"t","sha256":"s"}}',
                method="POST",
                headers={
                    "Host": "allow.trycloudflare.com",
                    "Content-Type": "application/json",
                    "X-Broker-Secret": broker_secret,
                },
            )
            with urlopen(request, timeout=2) as response:
                result_box.append(json.loads(response.read().decode()))

        caller = threading.Thread(target=invoke, daemon=True)
        caller.start()
        deadline = threading.Event()
        pending: dict[str, object] = {}
        for _ in range(20):
            candidate = _request(f"{controller.controller_url}/v1/brokers/{broker_id}/pending", token=state.token)
            requests = candidate["requests"]
            if requests:
                pending = requests[0]
                break
            deadline.wait(0.05)
        assert pending["tool_name"] == "read_curated_input"
        _request(
            f"{controller.controller_url}/v1/brokers/{broker_id}/result",
            token=state.token,
            data=json.dumps(
                {
                    "id": pending["id"],
                    "lease_token": pending["lease_token"],
                    "result": {"json": "synthetic"},
                }
            ).encode(),
            method="POST",
        )
        caller.join(timeout=2)
        assert result_box == [{"result": {"json": "synthetic"}}]
    finally:
        controller.shutdown()
        controller.server_close()
        thread.join(timeout=2)
