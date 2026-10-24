"""Private operator setup routes, enabled only for a CLI-owned loopback API."""

from __future__ import annotations

import asyncio
import ipaddress
import secrets
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from fleet_rlm.setup.service import SECTIONS, Operation, SetupConflictError, SetupService

_bearer = HTTPBearer(auto_error=False)


def operator(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> SetupService:
    """Require the private capability of this CLI-owned setup server."""
    service = getattr(request.app.state, "setup_service", None)
    token = getattr(request.app.state, "setup_token", "")
    if service is None or not token:
        raise HTTPException(status_code=404, detail="Setup is managed by the server operator.")
    try:
        local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local:
        raise HTTPException(status_code=403, detail="Setup is available only on the local operator connection.")
    if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), token.encode()):
        raise HTTPException(status_code=401, detail="A local operator token is required.")
    return service


Operator = Annotated[SetupService, Depends(operator)]


class SetupError(BaseModel):
    """Sanitized operator-facing error; credential inputs are never returned."""

    detail: str


router = APIRouter(
    prefix="/setup",
    tags=["setup"],
    dependencies=[Depends(operator)],
    responses={
        code: {"model": SetupError, "description": description}
        for code, description in {
            401: "Operator token required",
            403: "Loopback connection required",
            404: "Setup or operation unavailable",
            409: "Operation or configuration conflict",
            422: "Invalid setup request",
            503: "Provider unavailable",
        }.items()
    },
)


class SetupRequest(BaseModel):
    """Strict input shared by private setup operations."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ConfigEdit(SetupRequest):
    """Masked candidate values; responses never echo submitted credentials."""

    values: dict[str, SecretStr] = Field(max_length=40)


class Action(SetupRequest):
    """Client request identity used to avoid replaying a setup operation."""

    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")


class CreateVolume(Action):
    """Explicit request for a newly named workspace Volume."""

    name: str = Field(min_length=1, max_length=63, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


class VerifyRequest(Action):
    """Recheck all services or selected sections without changing provider state."""

    sections: list[Literal["models", "database", "mlflow", "daytona"]] = Field(
        default_factory=lambda: list(SECTIONS), min_length=1, max_length=4
    )


class SetupField(BaseModel):
    """One sanitized editable field and its effective source."""

    name: str
    section: str
    secret: bool
    configured: bool
    value: str | None
    source: str
    editable: bool


class SetupStatus(BaseModel):
    """Readiness and redacted configuration for the operator checklist."""

    backend: str
    snapshot_name: str
    env_path: str
    fields: list[SetupField]
    checks: dict[str, Any]
    errors: list[str]
    dirty: bool
    busy: bool
    ready: bool
    restart_required: bool


class OperationStarted(BaseModel):
    """Identity of a background operation whose progress can be reconnected."""

    operation_id: str


class VolumeInfo(BaseModel):
    """Safe provider identity of a workspace Volume."""

    id: str
    name: str
    state: str = "unknown"


class ApplyResult(BaseModel):
    """Request for the owning launcher to restart with saved configuration."""

    restart_required: bool


@router.get("/status", response_model=SetupStatus)
def status(service: Operator) -> dict[str, Any]:
    """Return current readiness without triggering provider checks."""
    return service.status()


def _edit_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SetupConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(
        status_code=422, detail="Configuration could not be saved; check the fields and file permissions."
    )


@router.patch("/config", response_model=SetupStatus)
def edit_config(body: ConfigEdit, service: Operator) -> dict[str, Any]:
    """Stage candidate edits without persisting them."""
    try:
        return service.stage({name: value.get_secret_value() for name, value in body.values.items()})
    except ValueError as exc:
        raise _edit_error(exc) from None


@router.post("/save", response_model=SetupStatus)
def save(service: Operator) -> dict[str, Any]:
    """Explicitly persist validated edits to the launch project dotenv file."""
    try:
        return service.save()
    except (ValueError, OSError) as exc:
        raise _edit_error(exc) from None


@router.delete("/config", response_model=SetupStatus)
def discard_config(service: Operator) -> dict[str, Any]:
    """Discard unsaved edits without changing the saved dotenv file."""
    try:
        return service.discard()
    except SetupConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


def _start(service: SetupService, action: str, body: Action, run: Any) -> dict[str, str]:
    try:
        operation = service.start(action, body.request_id, run)
    except SetupConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"operation_id": operation.id}


@router.post("/verify", response_model=OperationStarted)
def verify(body: VerifyRequest, service: Operator) -> dict[str, str]:
    """Start read-only checks for all required service sections."""
    return _start(service, "verify", body, lambda operation: service.verify(operation, tuple(body.sections)))


@router.get("/daytona/volumes", response_model=list[VolumeInfo])
def volumes(service: Operator) -> list[dict[str, str]]:
    """List workspace Volumes using server credentials."""
    try:
        return service.volumes()
    except Exception:
        raise HTTPException(
            status_code=503, detail="Could not list Volumes; check Daytona configuration and access."
        ) from None


@router.post("/daytona/volumes", response_model=OperationStarted)
def create_volume(body: CreateVolume, service: Operator) -> dict[str, str]:
    """Start explicit creation of a persistent provider Volume."""
    return _start(service, "create_volume", body, lambda _operation: service.create_volume(body.name))


@router.post("/daytona/snapshot/build", response_model=OperationStarted)
def build(body: Action, service: Operator) -> dict[str, str]:
    """Start explicit snapshot construction from the packaged recipe."""
    return _start(service, "build_snapshot", body, service.build)


@router.post("/daytona/snapshot/activate", response_model=OperationStarted)
def activate(body: Action, service: Operator) -> dict[str, str]:
    """Activate an existing provider snapshot without rebuilding it."""
    return _start(service, "activate_snapshot", body, service.activate)


@router.post("/models/test", response_model=OperationStarted)
def model_test(body: Action, service: Operator) -> dict[str, str]:
    """Start an optional small, potentially billed model request."""
    return _start(service, "model_test", body, service.test_model)


def operation_for(operation_id: str, service: Operator) -> Operation:
    """Resolve operation identity before starting the SSE response."""
    operation = service.operations.get(operation_id)
    if operation is None:
        raise HTTPException(
            status_code=404, detail="Setup operation was not found; verify provider state before retrying."
        )
    return operation


SetupOperation = Annotated[Operation, Depends(operation_for)]


@router.get("/operations/{operation_id}/events", response_class=EventSourceResponse)
async def operation_events(
    request: Request,
    operation: SetupOperation,
    after: Annotated[int, Query(ge=0)] = 0,
) -> AsyncIterator[ServerSentEvent]:
    """Replay retained progress and stream until one terminal outcome."""
    while True:
        events, finished = operation.read(after)
        for event in events:
            after = event["seq"]
            yield ServerSentEvent(data=event, id=str(after))
        if finished or await request.is_disconnected():
            break
        await asyncio.sleep(0.15)


@router.post("/apply", response_model=ApplyResult)
async def apply(request: Request, service: Operator) -> dict[str, bool]:
    """Fence new Turns and request a restart only when existing work is idle."""
    try:
        service.can_apply()
    except SetupConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    # Fence new admission before checking active locks. Existing Turns are never cancelled here.
    request.app.state.setup_applying = True
    registry = request.app.state.session_registry
    if registry is not None and not registry.request_restart():
        request.app.state.setup_applying = False
        service.cancel_apply()
        raise HTTPException(
            status_code=409, detail="A Turn or workspace operation is running; apply setup when it finishes."
        )
    return {"restart_required": True}
