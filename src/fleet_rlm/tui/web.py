"""Serve Fleet's existing Textual client in a browser with Textual Serve."""

from __future__ import annotations

import shlex
import sys
from urllib.parse import urlsplit

from aiohttp import web
from textual_serve.server import Server

from fleet_rlm.workspace import validate_session_id


def _origin(value: str) -> tuple[str, str, int]:
    """Parse one HTTP origin without accepting credentials or URL suffixes."""
    if not value or any(character.isspace() for character in value):
        raise ValueError("invalid origin")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or "\\" in value
        or "?" in value
        or "#" in value
        or parsed.netloc.endswith(":")
    ):
        raise ValueError("invalid origin")
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("invalid origin port")
    return (
        parsed.scheme,
        parsed.hostname.encode("idna").decode("ascii").lower(),
        port or (443 if parsed.scheme == "https" else 80),
    )


class FleetWebServer(Server):
    """Guard the terminal handshake before Textual Serve accepts or starts work."""

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """Permit only the configured browser origin, including behind a TLS proxy."""
        try:
            public = urlsplit(self.public_url)
            allowed = _origin(f"{public.scheme}://{public.netloc}")
            origins = request.headers.getall("Origin", [])
            hosts = request.headers.getall("Host", [])
            if (
                len(origins) != 1
                or len(hosts) != 1
                or _origin(origins[0]) != allowed
                or _origin(f"{public.scheme}://{hosts[0]}") != allowed
            ):
                raise ValueError("origin mismatch")
        except (ValueError, UnicodeError):
            raise web.HTTPForbidden(text="Terminal connection origin is not allowed.") from None
        return await super().handle_websocket(request)


def validate_api_url(value: str) -> str:
    """Require a credential-free absolute API URL safe to embed in a subprocess command."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("Fleet API URL must be a valid HTTP(S) URL") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("Fleet API URL must be HTTP(S), without credentials, query, or fragment")
    return value.rstrip("/")


def build_web_app_command(
    *,
    api_url: str,
    mlflow_mode: str,
    session_id: str | None = None,
    mlflow_port: int | None = None,
    mlflow_experiment: str | None = None,
) -> str:
    """Build the shell-quoted Textual Serve command for one browser client."""
    command = [
        sys.executable,
        "-m",
        "fleet_rlm.tui.web_client",
        "--url",
        validate_api_url(api_url),
        "--mlflow",
        mlflow_mode,
    ]
    if session_id is not None:
        command.extend(("--session", validate_session_id(session_id)))
    if mlflow_port is not None:
        command.extend(("--mlflow-port", str(mlflow_port)))
    if mlflow_experiment is not None:
        command.extend(("--mlflow-experiment", mlflow_experiment))
    return shlex.join(command)


def serve_web_client(
    *,
    api_url: str,
    mlflow_mode: str,
    host: str,
    port: int,
    session_id: str | None = None,
    mlflow_port: int | None = None,
    mlflow_experiment: str | None = None,
    public_url: str | None = None,
) -> None:
    """Start Textual Serve; each browser connection gets its own Textual subprocess."""
    server = FleetWebServer(
        build_web_app_command(
            api_url=api_url,
            mlflow_mode=mlflow_mode,
            session_id=session_id,
            mlflow_port=mlflow_port,
            mlflow_experiment=mlflow_experiment,
        ),
        host=host,
        port=port,
        title="Fleet RLM Lite",
        public_url=public_url,
    )
    server.serve()
