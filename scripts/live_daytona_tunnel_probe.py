#!/usr/bin/env python3
"""Run a local synthetic probe controller behind two temporary Cloudflare tunnels.

The controller remains loopback-only.  The two public tunnel URLs carry only
synthetic probe routes and are injected into a child live-test process; neither
the controller URL nor its bearer token is exposed to a Daytona sandbox.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import queue
import re
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import IO, Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

_LIVE_VALUES = frozenset({"1", "true", "yes"})
_TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)
_PROBE_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_TUNNEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_NAMED_TUNNEL_ENVIRONMENT = (
    "FLEET_OPTIMIZATION_NAMED_TUNNEL_ID",
    "FLEET_OPTIMIZATION_NAMED_TUNNEL_CREDENTIALS_FILE",
    "FLEET_OPTIMIZATION_NAMED_TUNNEL_ALLOWED_ORIGIN",
    "FLEET_OPTIMIZATION_NAMED_TUNNEL_DENIED_ORIGIN",
)
_START_TIMEOUT_SECONDS = 60
_HEALTH_TIMEOUT_SECONDS = 10
_HEALTH_READY_TIMEOUT_SECONDS = 90
_TUNNEL_START_ATTEMPTS = 2
_MAX_DRAIN_BYTES = 16 * 1024
_CLOUDFLARE_DOH_URL = "https://cloudflare-dns.com/dns-query"
_HEALTH_HEADERS = {"User-Agent": "Fleet-Daytona-development-probe/1"}
_BROKER_CALL_TIMEOUT_SECONDS = 60


class TunnelProbeError(RuntimeError):
    """The local development tunnel probe could not start safely."""


@dataclass(slots=True)
class _ProbeObservation:
    allowed_requests: int = 0
    redirect_requests: int = 0
    denied_requests: int = 0


@dataclass(slots=True)
class _BrokerCall:
    call_id: str
    tool_name: str
    args: list[object]
    kwargs: dict[str, object]
    event: threading.Event = field(default_factory=threading.Event)
    lease_token: str | None = None
    result: object | None = None
    error: str | None = None


@dataclass(slots=True)
class _GatewayBroker:
    secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    calls: dict[str, _BrokerCall] = field(default_factory=dict)


@dataclass(slots=True)
class _ControllerState:
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    allowed_origin: str = ""
    denied_origin: str = ""
    observations: dict[str, _ProbeObservation] = field(default_factory=dict)
    brokers: dict[str, _GatewayBroker] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def allowed_domain(self) -> str:
        """Return the hostname of the allowed origin."""
        return _domain(self.allowed_origin)

    @property
    def denied_domain(self) -> str:
        """Return the hostname of the denied origin."""
        return _domain(self.denied_origin)

    def create_probe(self) -> str:
        """Create a probe and return its unique identifier."""
        probe_id = secrets.token_urlsafe(18)
        with self.lock:
            self.observations[probe_id] = _ProbeObservation()
        return probe_id

    def observe(self, probe_id: str, route: str) -> None:
        """
        Record a request observed on a probe route.

        Parameters:
            probe_id (str): Identifier of the probe receiving the request.
            route (str): Probe route to record: `allowed`, `redirect`, or `denied`.
        """
        with self.lock:
            observation = self.observations.get(probe_id)
            if observation is None:
                return
            if route == "allowed":
                observation.allowed_requests += 1
            elif route == "redirect":
                observation.redirect_requests += 1
            elif route == "denied":
                observation.denied_requests += 1

    def observation(self, probe_id: str) -> _ProbeObservation | None:
        """Return a snapshot of the recorded observations for a probe.

        Parameters:
            probe_id (str): The identifier of the probe to inspect.

        Returns:
            _ProbeObservation | None: A copy of the probe's observations, or `None` if the probe does not exist.
        """
        with self.lock:
            observation = self.observations.get(probe_id)
            if observation is None:
                return None
            return _ProbeObservation(
                allowed_requests=observation.allowed_requests,
                redirect_requests=observation.redirect_requests,
                denied_requests=observation.denied_requests,
            )

    def create_broker(self) -> tuple[str, str]:
        """Create a broker and return its identifier and secret.

        Returns:
            tuple[str, str]: The broker identifier and secret."""
        broker_id = secrets.token_urlsafe(18)
        broker = _GatewayBroker()
        with self.lock:
            self.brokers[broker_id] = broker
        return broker_id, broker.secret

    def broker_call(
        self,
        broker_id: str,
        *,
        secret: str,
        tool_name: str,
        args: list[object],
        kwargs: dict[str, object],
    ) -> tuple[object | None, str | None]:
        """
        Queue a validated broker tool call and wait for its completion.

        Parameters:
            broker_id (str): Identifier of the broker receiving the call.
            secret (str): Secret required to authorize the request.
            tool_name (str): Name of the broker tool to invoke.
            args (list[object]): Positional arguments for the tool.
            kwargs (dict[str, object]): Keyword arguments for the tool.

        Returns:
            tuple[object | None, str | None]: The tool result and error message. The error
            is ``None`` for a successful call; unauthorized, unsupported, or timed-out
            calls return an error message.
        """
        with self.lock:
            broker = self.brokers.get(broker_id)
            if broker is None or not secrets.compare_digest(secret, broker.secret):
                return None, "unauthorized"
            if tool_name != "read_curated_input":
                return None, "unknown tool"
            call = _BrokerCall(
                call_id=secrets.token_urlsafe(18),
                tool_name=tool_name,
                args=args,
                kwargs=kwargs,
            )
            broker.calls[call.call_id] = call
        if not call.event.wait(timeout=_BROKER_CALL_TIMEOUT_SECONDS):
            with self.lock:
                broker.calls.pop(call.call_id, None)
            return None, "tool call timed out"
        with self.lock:
            broker.calls.pop(call.call_id, None)
        return call.result, call.error

    def broker_pending(self, broker_id: str) -> list[dict[str, object]] | None:
        """
        Lease and return pending calls for a broker.

        Parameters:
            broker_id (str): Identifier of the broker whose calls should be retrieved.

        Returns:
            list[dict[str, object]] | None: Pending calls with lease tokens, or `None` if the broker does not exist.
        """
        with self.lock:
            broker = self.brokers.get(broker_id)
            if broker is None:
                return None
            pending: list[dict[str, object]] = []
            for call in broker.calls.values():
                if call.lease_token is not None:
                    continue
                call.lease_token = secrets.token_urlsafe(18)
                pending.append(
                    {
                        "id": call.call_id,
                        "tool_name": call.tool_name,
                        "args": call.args,
                        "kwargs": call.kwargs,
                        "lease_token": call.lease_token,
                    }
                )
            return pending

    def broker_result(
        self,
        broker_id: str,
        *,
        call_id: str,
        lease_token: str,
        result: object | None,
        error: str | None,
    ) -> bool:
        """
        Complete a leased broker call with a result or error.

        Parameters:
            broker_id (str): Identifier of the broker containing the call.
            call_id (str): Identifier of the broker call.
            lease_token (str): Token assigned when the call was leased.
            result (object | None): Result value for a successful call.
            error (str | None): Error description for a failed call.

        Returns:
            bool: True if the call was completed, false if the broker, call, or lease token was invalid.
        """
        with self.lock:
            broker = self.brokers.get(broker_id)
            call = broker.calls.get(call_id) if broker is not None else None
            if call is None or call.lease_token is None:
                return False
            if not secrets.compare_digest(lease_token, call.lease_token):
                return False
            call.result = result
            call.error = error
            call.event.set()
            return True


class _ProbeController(ThreadingHTTPServer):
    """Token-guarded controller plus a synthetic public probe application.

    The socket binds to loopback, but ``cloudflared`` also connects from
    loopback, so every tunneled public request passes the client-address
    check.  The bearer token, not the client address, separates the
    controller routes from the public probe routes.
    """

    daemon_threads = True

    def __init__(self, state: _ControllerState) -> None:
        super().__init__(("127.0.0.1", 0), _ProbeRequestHandler)
        self.state = state

    @property
    def controller_url(self) -> str:
        """Return the loopback URL of the controller server."""
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"


class _ProbeRequestHandler(BaseHTTPRequestHandler):
    server: _ProbeController

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Do not record paths, tokens, or request metadata in host logs."""

    def _handle(self) -> None:
        parsed = urlparse(self.path)
        if parsed.query or parsed.fragment:
            self._reply_json(400, {"error": "invalid request"})
            return
        if parsed.path.startswith("/v1/"):
            self._handle_controller(parsed.path)
            return
        if parsed.path.startswith("/broker/"):
            self._handle_public_broker(parsed.path)
            return
        self._handle_public_probe(parsed.path)

    def _handle_controller(self, path: str) -> None:
        """Handle authenticated controller API requests for probe and broker management.

        Creates probes and brokers, reports probe observations, leases pending broker requests, and
        records broker results. Responds with an error for unauthorized, malformed, unsupported, or
        unknown requests.
        """
        if not self._is_loopback_controller_request():
            self._reply_json(403, {"error": "forbidden"})
            return
        if path == "/v1/probes" and self.command == "POST":
            self._drain_body()
            probe_id = self.server.state.create_probe()
            state = self.server.state
            self._reply_json(
                201,
                {
                    "probe_id": probe_id,
                    "allowed_healthy": _health_status(state.allowed_origin).startswith("http-2"),
                    "denied_healthy": _health_status(state.denied_origin).startswith("http-2"),
                    "allowed_url": f"{state.allowed_origin}/probe/{probe_id}/allowed",
                    "redirect_url": f"{state.allowed_origin}/probe/{probe_id}/redirect",
                    "denied_url": f"{state.denied_origin}/probe/{probe_id}/denied",
                    "allowed_domain": state.allowed_domain,
                    "denied_domain": state.denied_domain,
                },
            )
            return
        if path == "/v1/brokers" and self.command == "POST":
            self._drain_body()
            broker_id, secret = self.server.state.create_broker()
            self._reply_json(
                201,
                {
                    "broker_id": broker_id,
                    "broker_secret": secret,
                    "broker_url": f"{self.server.state.allowed_origin}/broker/{broker_id}/call",
                },
            )
            return
        broker_match = re.fullmatch(r"/v1/brokers/([A-Za-z0-9_-]{16,128})/(pending|result)", path)
        if broker_match:
            broker_id, action = broker_match.groups()
            if action == "pending" and self.command == "GET":
                pending = self.server.state.broker_pending(broker_id)
                if pending is None:
                    self._reply_json(404, {"error": "not found"})
                else:
                    self._reply_json(200, {"requests": pending})
                return
            if action == "result" and self.command == "POST":
                payload = self._read_json_body()
                if payload is None:
                    self._reply_json(400, {"error": "invalid request"})
                    return
                call_id = payload.get("id")
                lease_token = payload.get("lease_token")
                if not isinstance(call_id, str) or not isinstance(lease_token, str):
                    self._reply_json(400, {"error": "invalid request"})
                    return
                error = payload.get("error")
                if error is not None and not isinstance(error, str):
                    self._reply_json(400, {"error": "invalid request"})
                    return
                if self.server.state.broker_result(
                    broker_id,
                    call_id=call_id,
                    lease_token=lease_token,
                    result=payload.get("result"),
                    error=error,
                ):
                    self._reply_json(200, {"status": "ok"})
                else:
                    self._reply_json(404, {"error": "not found"})
                return
        match = re.fullmatch(r"/v1/probes/([A-Za-z0-9_-]{16,128})", path)
        if match and self.command == "GET":
            observation = self.server.state.observation(match.group(1))
            if observation is None:
                self._reply_json(404, {"error": "not found"})
                return
            self._reply_json(
                200,
                {
                    "allowed_requests": observation.allowed_requests,
                    "redirect_requests": observation.redirect_requests,
                    "denied_requests": observation.denied_requests,
                },
            )
            return
        self._reply_json(404, {"error": "not found"})

    def _handle_public_broker(self, path: str) -> None:
        """Handle an authenticated public broker request and return its tool result.

        Parameters:
            path (str): Broker call path used to identify the broker.
        """
        match = re.fullmatch(r"/broker/([A-Za-z0-9_-]{16,128})/call", path)
        if match is None or self.command != "POST" or not self._is_allowed_gateway_request():
            self._reply_json(404, {"error": "not found"})
            return
        payload = self._read_json_body()
        if payload is None:
            self._reply_json(400, {"error": "invalid request"})
            return
        tool_name = payload.get("tool_name")
        raw_args = payload.get("args")
        raw_kwargs = payload.get("kwargs")
        if not isinstance(tool_name, str) or not isinstance(raw_args, list) or not isinstance(raw_kwargs, dict):
            self._reply_json(400, {"error": "invalid request"})
            return
        result, error = self.server.state.broker_call(
            match.group(1),
            secret=self.headers.get("X-Broker-Secret", ""),
            tool_name=tool_name,
            args=cast(list[object], raw_args),
            kwargs=cast(dict[str, object], raw_kwargs),
        )
        if error is not None:
            self._reply_json(504 if error == "tool call timed out" else 403, {"error": error})
            return
        self._reply_json(200, {"result": result})

    def _handle_public_probe(self, path: str) -> None:
        """
        Handle public health and synthetic probe requests.

        Records valid probe observations and returns the corresponding health, allowed, redirect, or denied response.
        """
        state = self.server.state
        if self.command == "GET" and path == "/health":
            self._reply_json(200, {"status": "ok"})
            return
        match = re.fullmatch(r"/probe/([A-Za-z0-9_-]{16,128})/(allowed|redirect|denied)", path)
        if match is None:
            self._reply_json(404, {"error": "not found"})
            return
        probe_id, route = match.groups()
        if state.observation(probe_id) is None:
            self._reply_json(404, {"error": "not found"})
            return
        if route == "allowed":
            if self.command != "GET":
                self._reply_json(404, {"error": "not found"})
                return
            state.observe(probe_id, route)
            self._reply_json(200, {"status": "allowed"})
            return
        if route == "redirect":
            if self.command != "GET":
                self._reply_json(404, {"error": "not found"})
                return
            state.observe(probe_id, route)
            redirect_location = f"{state.denied_origin}/probe/{probe_id}/denied"
            if "\r" in redirect_location or "\n" in redirect_location:
                self._reply_json(500, {"error": "invalid redirect"})
                return
            self.send_response(302)
            self.send_header("Location", redirect_location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._drain_body()
        state.observe(probe_id, route)
        self._reply_json(204, None)

    def _is_loopback_controller_request(self) -> bool:
        """Determine whether the request originates from loopback and has the controller bearer token."""
        client_host = self.client_address[0]
        return client_host in {"127.0.0.1", "::1"} and secrets.compare_digest(
            self.headers.get("Authorization", ""), f"Bearer {self.server.state.token}"
        )

    def _is_allowed_gateway_request(self) -> bool:
        """Determine whether the request's host matches the configured allowed domain.

        Returns:
            bool: `True` if the host matches the allowed domain, `False` otherwise.
        """
        host = self.headers.get("Host", "").split(":", 1)[0].lower()
        return host == self.server.state.allowed_domain

    def _drain_body(self) -> None:
        """Drain a bounded request body from the input stream."""
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if 0 < size <= _MAX_DRAIN_BYTES:
            self.rfile.read(size)

    def _read_json_body(self) -> dict[str, object] | None:
        """Parse a bounded request body as a JSON object.

        Returns:
            dict[str, object] | None: The decoded JSON object, or `None` when the body is
                missing, oversized, invalid, or not an object.
        """
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if not 0 < size <= _MAX_DRAIN_BYTES:
            return None
        try:
            value = json.loads(self.rfile.read(size).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _reply_json(self, status: int, payload: dict[str, Any] | None) -> None:
        """Send an HTTP response with an optional JSON body."""
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        if body:
            self.send_header("Content-Type", "application/json")
        self.end_headers()
        if body:
            self.wfile.write(body)


@dataclass(slots=True)
class _Tunnel:
    process: subprocess.Popen[str]
    lines: queue.Queue[str]
    configuration_path: Path | None = None

    def wait_for_url(self) -> str:
        """
        Wait for the tunnel process to publish its HTTPS hostname.

        Returns:
            str: The published lowercase HTTPS tunnel URL.

        Raises:
            TunnelProbeError: If the process exits before publishing a URL or the startup timeout expires.
        """
        deadline = time.monotonic() + _START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise TunnelProbeError("Cloudflare quick tunnel exited before it published an HTTPS hostname")
            try:
                line = self.lines.get(timeout=0.25)
            except queue.Empty:
                continue
            match = _TUNNEL_URL.search(line)
            if match:
                return match.group(0).lower()
        raise TunnelProbeError("Cloudflare quick tunnel did not publish an HTTPS hostname before timeout")

    def stop(self) -> None:
        """
        Stop the tunnel process and remove its temporary configuration file.

        The process is terminated gracefully and force-killed if it does not exit within the timeout.
        """
        try:
            if self.process.poll() is not None:
                return
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        finally:
            if self.configuration_path is not None:
                self.configuration_path.unlink(missing_ok=True)


def _start_tunnel(controller_url: str) -> _Tunnel:
    """
    Start a Cloudflare quick tunnel that forwards traffic to the controller.

    Parameters:
        controller_url (str): Local controller URL to expose through the tunnel.

    Returns:
        _Tunnel: Running tunnel process and its output queue.

    Raises:
        TunnelProbeError: If `cloudflared` is unavailable.
    """
    try:
        process = subprocess.Popen(
            [
                "cloudflared",
                "tunnel",
                "--no-autoupdate",
                "--protocol",
                "http2",
                "--loglevel",
                "info",
                "--url",
                controller_url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError as exc:
        raise TunnelProbeError("cloudflared is required for the development tunnel smoke") from exc
    stdout = cast(IO[str] | None, process.stdout)
    assert stdout is not None
    lines: queue.Queue[str] = queue.Queue()

    def _collect() -> None:
        for line in stdout:
            lines.put(line)

    threading.Thread(target=_collect, daemon=True).start()
    return _Tunnel(process=process, lines=lines)


@dataclass(frozen=True, slots=True)
class _NamedTunnelConfiguration:
    tunnel_id: str
    credentials_file: Path
    allowed_origin: str
    denied_origin: str


def _configured_named_tunnel() -> _NamedTunnelConfiguration | None:
    """
    Load and validate the optional named-tunnel configuration from environment variables.

    Returns:
        _NamedTunnelConfiguration | None: The validated named-tunnel configuration, or `None`
            when no named-tunnel settings are provided.

    Raises:
        TunnelProbeError: If the configuration is incomplete or contains invalid credentials,
            identifiers, origins, or duplicate hostnames.
    """
    values = {name: os.environ.get(name, "").strip() for name in _NAMED_TUNNEL_ENVIRONMENT}
    populated = {name: value for name, value in values.items() if value}
    if not populated:
        return None
    missing = [name for name in _NAMED_TUNNEL_ENVIRONMENT if not values[name]]
    if missing:
        raise TunnelProbeError("named tunnel configuration is incomplete")
    credentials_file = Path(values["FLEET_OPTIMIZATION_NAMED_TUNNEL_CREDENTIALS_FILE"])
    if not credentials_file.is_file():
        raise TunnelProbeError("named tunnel credentials file is not available")
    tunnel_id = values["FLEET_OPTIMIZATION_NAMED_TUNNEL_ID"]
    if not _TUNNEL_ID.fullmatch(tunnel_id):
        raise TunnelProbeError("named tunnel identifier must be a bare identifier")
    configuration = _NamedTunnelConfiguration(
        tunnel_id=tunnel_id,
        credentials_file=credentials_file,
        allowed_origin=_validated_https_origin(values["FLEET_OPTIMIZATION_NAMED_TUNNEL_ALLOWED_ORIGIN"]),
        denied_origin=_validated_https_origin(values["FLEET_OPTIMIZATION_NAMED_TUNNEL_DENIED_ORIGIN"]),
    )
    if _domain(configuration.allowed_origin) == _domain(configuration.denied_origin):
        raise TunnelProbeError("named tunnel origins must use distinct hostnames")
    return configuration


def _validated_https_origin(origin: str) -> str:
    """
    Validate and normalize a named tunnel's HTTPS origin.

    Parameters:
        origin (str): Origin to validate.

    Returns:
        str: Lowercase HTTPS origin containing only the hostname.

    Raises:
        TunnelProbeError: If the origin is not a bare HTTPS origin.
    """
    parsed = urlparse(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise TunnelProbeError("named tunnel origins must be bare HTTPS origins")
    return f"https://{parsed.hostname.lower()}"


def _start_named_tunnel(
    configuration: _NamedTunnelConfiguration, controller_url: str
) -> _Tunnel:
    """
    Start a configured Cloudflare named tunnel that routes both probe origins to the controller.

    Parameters:
        configuration (_NamedTunnelConfiguration): Named-tunnel settings and origin hostnames.
        controller_url (str): Loopback URL to expose through the tunnel.

    Returns:
        _Tunnel: Running tunnel process and its temporary configuration.

    Raises:
        TunnelProbeError: If `cloudflared` is unavailable.
    """
    controller = urlparse(controller_url)
    assert controller.hostname and controller.port
    allowed_domain = _domain(configuration.allowed_origin)
    denied_domain = _domain(configuration.denied_origin)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="fleet-daytona-development-canary-",
        suffix=".yaml",
    )
    configuration_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            temporary_file.write(
                "\n".join(
                    (
                        f"tunnel: {configuration.tunnel_id}",
                        f"credentials-file: {configuration.credentials_file}",
                        "ingress:",
                        f"  - hostname: {allowed_domain}",
                        f"    service: http://{controller.hostname}:{controller.port}",
                        f"  - hostname: {denied_domain}",
                        f"    service: http://{controller.hostname}:{controller.port}",
                        "  - service: http_status:404",
                        "",
                    )
                )
            )
        process = subprocess.Popen(
            [
                "cloudflared",
                "--no-autoupdate",
                "--config",
                str(configuration_path),
                "tunnel",
                "--protocol",
                "http2",
                "run",
                configuration.tunnel_id,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError as exc:
        configuration_path.unlink(missing_ok=True)
        raise TunnelProbeError("cloudflared is required for the development tunnel smoke") from exc
    except BaseException:
        configuration_path.unlink(missing_ok=True)
        raise
    stdout = cast(IO[str] | None, process.stdout)
    assert stdout is not None
    lines: queue.Queue[str] = queue.Queue()

    def _collect() -> None:
        for line in stdout:
            lines.put(line)

    threading.Thread(target=_collect, daemon=True).start()
    return _Tunnel(process=process, lines=lines, configuration_path=configuration_path)


def _health_status(origin: str) -> str:
    """Checks an origin's health endpoint and categorizes the response.

    Parameters:
        origin (str): The HTTPS origin to check.

    Returns:
        str: The HTTP status or a category describing the connectivity failure.
    """
    try:
        request = Request(f"{origin}/health", headers=_HEALTH_HEADERS)
        with urlopen(request, timeout=_HEALTH_TIMEOUT_SECONDS) as response:
            return f"http-{response.status}"
    except HTTPError as exc:
        return f"http-{exc.code}"
    except URLError as exc:
        if isinstance(exc.reason, socket.gaierror):
            return _health_status_via_cloudflare_dns(origin)
        return f"unreachable-{type(exc.reason).__name__}"
    except OSError as exc:
        return f"unreachable-{type(exc).__name__}"


def _health_status_via_cloudflare_dns(origin: str) -> str:
    """
    Check a quick tunnel's health by resolving its hostname through Cloudflare DNS.

    Parameters:
        origin (str): HTTPS tunnel origin to check.

    Returns:
        str: The HTTP status category, or a reason for an invalid hostname, DNS failure, or unreachable edge.
    """
    hostname = _domain(origin)
    if not hostname.endswith(".trycloudflare.com"):
        return "unreachable-invalid-host"
    address = _cloudflare_dns_ipv4(hostname)
    if address is None:
        return "unreachable-dns"
    try:
        with socket.create_connection((address, 443), timeout=_HEALTH_TIMEOUT_SECONDS) as raw_socket:
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            with context.wrap_socket(raw_socket, server_hostname=hostname) as tls_socket:
                tls_socket.settimeout(_HEALTH_TIMEOUT_SECONDS)
                request = (
                    f"GET /health HTTP/1.1\r\nHost: {hostname}\r\nConnection: close\r\nAccept: application/json\r\n\r\n"
                )
                tls_socket.sendall(request.encode())
                response = http.client.HTTPResponse(tls_socket)
                response.begin()
                response.read()
                return f"http-{response.status}"
    except (OSError, ssl.SSLError, http.client.HTTPException):
        return "unreachable-edge"


def _cloudflare_dns_ipv4(hostname: str) -> str | None:
    """
    Resolve a hostname through Cloudflare DNS-over-HTTPS and return its IPv4 address.

    Parameters:
        hostname (str): Hostname to resolve.

    Returns:
        str | None: The resolved IPv4 address, or `None` if resolution fails or no IPv4 address is found.
    """
    current = hostname
    for _attempt in range(4):
        request = Request(
            f"{_CLOUDFLARE_DOH_URL}?name={quote(current)}&type=A",
            headers={"Accept": "application/dns-json"},
        )
        try:
            with urlopen(request, timeout=_HEALTH_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        answers = payload.get("Answer") if isinstance(payload, dict) else None
        if not isinstance(answers, list):
            return None
        cname: str | None = None
        for answer in answers:
            if not isinstance(answer, dict):
                continue
            data = answer.get("data")
            if answer.get("type") == 1 and isinstance(data, str):
                return data
            if answer.get("type") == 5 and isinstance(data, str):
                cname = data.rstrip(".")
        if cname is None:
            return None
        current = cname
    return None


def _wait_healthy(origin: str) -> str:
    """
    Poll the origin until it responds over HTTP/2 or the health-check timeout expires.

    Parameters:
        origin (str): HTTPS origin to check.

    Returns:
        str: The most recent health status, including an HTTP/2 status when the origin becomes ready.
    """
    deadline = time.monotonic() + _HEALTH_READY_TIMEOUT_SECONDS
    last_status = "unreachable"
    while time.monotonic() < deadline:
        last_status = _health_status(origin)
        if last_status.startswith("http-2"):
            return last_status
        time.sleep(0.5)
    return last_status


def _start_healthy_tunnel(controller_url: str) -> tuple[_Tunnel, str]:
    """
    Start a Cloudflare quick tunnel and wait for a healthy HTTPS origin.

    Parameters:
        controller_url (str): Loopback controller URL exposed through the tunnel.

    Returns:
        tuple[_Tunnel, str]: The running tunnel and its healthy public origin.

    Raises:
        TunnelProbeError: If all tunnel startup attempts fail or the origin does not pass the HTTPS health check.
    """
    failures: list[str] = []
    for _attempt in range(_TUNNEL_START_ATTEMPTS):
        tunnel = _start_tunnel(controller_url)
        try:
            origin = tunnel.wait_for_url()
            health = _wait_healthy(origin)
            if health.startswith("http-2"):
                return tunnel, origin
            failures.append(health)
        except TunnelProbeError as exc:
            failures.append(str(exc))
        tunnel.stop()
    raise TunnelProbeError(f"Cloudflare quick tunnel HTTPS health preflight failed ({', '.join(failures)})")


def _domain(origin: str) -> str:
    """
    Extract the lowercase hostname from an origin URL.

    Parameters:
        origin (str): Origin URL whose hostname should be extracted.

    Returns:
        str: The lowercase hostname.
    """
    hostname = urlparse(origin).hostname
    if not hostname:
        raise TunnelProbeError("Cloudflare quick tunnel did not provide a hostname")
    return hostname.lower()


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit wrapper command parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER, help="command to run after --")
    return parser


def main(argv: list[str] | None = None) -> int:
    """
    Start the development-only controller and execute the specified live-test command.

    Parameters:
        argv (list[str] | None): Optional command-line arguments to parse.

    Returns:
        int: The child command's exit status, or 2 if setup validation fails.
    """
    args = build_parser().parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("a live test command is required after --", file=sys.stderr)
        return 2
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        print("FLEET_LIVE=1 is required", file=sys.stderr)
        return 2

    state = _ControllerState()
    controller = _ProbeController(state)
    controller_thread = threading.Thread(target=controller.serve_forever, daemon=True)
    controller_thread.start()
    tunnels: list[_Tunnel] = []
    try:
        named_tunnel = _configured_named_tunnel()
        if named_tunnel is None:
            allowed_tunnel, state.allowed_origin = _start_healthy_tunnel(controller.controller_url)
            tunnels.append(allowed_tunnel)
            denied_tunnel, state.denied_origin = _start_healthy_tunnel(controller.controller_url)
            tunnels.append(denied_tunnel)
            if state.allowed_domain == state.denied_domain:
                raise TunnelProbeError("Cloudflare quick tunnels must publish distinct hostnames")
        else:
            state.allowed_origin = named_tunnel.allowed_origin
            state.denied_origin = named_tunnel.denied_origin
            persistent_tunnel = _start_named_tunnel(named_tunnel, controller.controller_url)
            tunnels.append(persistent_tunnel)
            allowed_health = _wait_healthy(state.allowed_origin)
            denied_health = _wait_healthy(state.denied_origin)
            if not allowed_health.startswith("http-2") or not denied_health.startswith("http-2"):
                raise TunnelProbeError("Cloudflare named tunnel HTTPS health preflight failed")

        child_env: dict[str, str] = {
            key: value
            for key, value in os.environ.items()
        }
        child_env.update(
            {
            "RUN_LIVE_DAYTONA_STRICT_SMOKE": "1",
            "FLEET_OPTIMIZATION_PROBE_CONTROLLER_URL": controller.controller_url,
            "FLEET_OPTIMIZATION_PROBE_CONTROLLER_TOKEN": state.token,
            }
        )
        for environment_name in _NAMED_TUNNEL_ENVIRONMENT:
            child_env.pop(environment_name, None)
        print("development Daytona tunnel probe ready")
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=child_env, check=False)
        print(f"development Daytona tunnel probe child exited with status {result.returncode}")
        return result.returncode
    except TunnelProbeError as exc:
        print(f"development Daytona tunnel smoke setup failed: {exc}", file=sys.stderr)
        return 2
    finally:
        for tunnel in reversed(tunnels):
            tunnel.stop()
        controller.shutdown()
        controller.server_close()
        controller_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
