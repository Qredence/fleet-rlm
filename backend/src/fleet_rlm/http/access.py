"""Workspace bearer policy and CORS for FastAPI requests."""

import secrets
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import Settings
from .dependencies import _STREAM_HEADER, RuntimeDep, get_runtime


class _SettingsCORS:
    """Configure native CORS on the first HTTP request after lifespan startup."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self.cors: CORSMiddleware | None = None
        self.origins: tuple[str, ...] | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        runtime = getattr(scope["app"].state, "runtime", None)
        if runtime is None:
            await self.app(scope, receive, send)
            return
        origins = tuple(runtime.settings.cors_origins)
        if self.cors is None or self.origins != origins:
            self.cors = CORSMiddleware(
                self.app,
                allow_origins=origins,
                allow_methods=["GET", "POST", "PATCH", "DELETE"],
                allow_headers=["Authorization", "Content-Type"],
                expose_headers=[_STREAM_HEADER, "Link", "Retry-After"],
            )
            self.origins = origins
        await self.cors(scope, receive, send)


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


_LOCAL_ORIGINS = {
    f"http://{host}:{port}" for host in ("localhost", "127.0.0.1", "[::1]") for port in (5173, 8000)
}


_PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto")


_BEARER = HTTPBearer(
    scheme_name="FleetBearer",
    description="Fleet workspace or provider administrator bearer.",
    auto_error=False,
)


_Access = Literal["local", "workspace", "provider_admin"]


def _direct_loopback(request: Request) -> bool:
    if (
        request.client is None
        or request.client.host not in _LOCAL_HOSTS
        or any(name in request.headers for name in _PROXY_HEADERS)
        or request.headers.get("sec-fetch-site") == "cross-site"
    ):
        return False
    origin = request.headers.get("origin")
    try:
        url = request.url
        return url.hostname in _LOCAL_HOSTS and (
            origin is None or origin in _LOCAL_ORIGINS or origin == f"{url.scheme}://{url.netloc}"
        )
    except ValueError:
        return False


def _invalid_bearer() -> HTTPException:
    return HTTPException(401, "Invalid bearer token", headers={"WWW-Authenticate": "Bearer"})


async def _workspace_access(request: Request, settings: Settings) -> _Access:
    local = not settings.shared_deployment and _direct_loopback(request)
    workspace, admin = settings.api_bearer_token, settings.provider_admin_token
    credentials = await _BEARER(request)
    supplied = credentials.credentials.encode() if credentials else b""
    is_admin = bool(
        supplied and admin and secrets.compare_digest(supplied, admin.get_secret_value().encode())
    )
    if local and workspace is None:
        return "provider_admin" if is_admin else "local"
    if (
        not workspace
        or not supplied
        or not (is_admin or secrets.compare_digest(supplied, workspace.get_secret_value().encode()))
    ):
        raise _invalid_bearer()
    if not local and request.scope.get("scheme") != "https":
        raise HTTPException(403, "Workspace access requires HTTPS")
    return "provider_admin" if is_admin else "workspace"


class _WorkspaceAccess:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope["path"]
            root = scope.get("root_path", "").rstrip("/")
            if root and path.startswith(f"{root}/"):
                path = path[len(root) :]
            if path == "/api" or path.startswith("/api/"):
                request = Request(scope)
                try:
                    runtime = get_runtime(request)
                    request.state.fleet_access = await _workspace_access(request, runtime.settings)
                except HTTPException as error:
                    response = JSONResponse(
                        {"detail": error.detail},
                        status_code=error.status_code,
                        headers=error.headers,
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


async def require_auth(
    request: Request, _credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER)]
) -> None:
    if not getattr(request.state, "fleet_access", None):
        raise _invalid_bearer()


def require_provider_admin(request: Request, runtime: RuntimeDep) -> None:
    access = getattr(request.state, "fleet_access", None)
    if access == "provider_admin":
        return
    if access == "local" and not runtime.settings.provider_admin_token:
        return
    raise HTTPException(403, "Provider administration requires the administrator token")
