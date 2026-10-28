"""Private operator setup routes, enabled only for a CLI-owned loopback API."""

from __future__ import annotations

import asyncio
import ipaddress
import secrets
from collections.abc import AsyncIterator
from contextlib import nullcontext
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from fleet_rlm.optimization_runs import ACTIVE_STATUSES
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


@router.get("/status")
def status(service: Operator) -> SetupStatus:
    """Return current readiness without triggering provider checks."""
    return SetupStatus.model_validate(service.status())


def _edit_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SetupConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(
        status_code=422, detail="Configuration could not be saved; check the fields and file permissions."
    )


@router.patch("/config")
def edit_config(body: ConfigEdit, service: Operator) -> SetupStatus:
    """Stage candidate edits without persisting them."""
    try:
        return SetupStatus.model_validate(
            service.stage({name: value.get_secret_value() for name, value in body.values.items()})
        )
    except ValueError as exc:
        raise _edit_error(exc) from None


@router.post("/save")
def save(service: Operator) -> SetupStatus:
    """Explicitly persist validated edits to the launch project dotenv file."""
    try:
        return SetupStatus.model_validate(service.save())
    except (ValueError, OSError) as exc:
        raise _edit_error(exc) from None


@router.delete("/config")
def discard_config(service: Operator) -> SetupStatus:
    """Discard unsaved edits without changing the saved dotenv file."""
    try:
        return SetupStatus.model_validate(service.discard())
    except SetupConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


def _start(service: SetupService, action: str, body: Action, run: Any) -> OperationStarted:
    """Start a background action and return its operation ID.

    Reuse a retained operation for the same request ID and action without running
    it again. Conflicting requests raise HTTP 409; failures in ``run`` are
    reported through operation events.
    """
    try:
        operation = service.start(action, body.request_id, run)
    except SetupConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return OperationStarted(operation_id=operation.id)


@router.post("/verify")
def verify(body: VerifyRequest, service: Operator) -> OperationStarted:
    """Start read-only checks for the requested sections, defaulting to all sections."""
    return _start(service, "verify", body, lambda operation: service.verify(operation, tuple(body.sections)))


@router.get("/daytona/volumes")
def volumes(service: Operator) -> list[VolumeInfo]:
    """List workspace Volumes using server credentials."""
    try:
        return [VolumeInfo.model_validate(v) for v in service.volumes()]
    except Exception:
        raise HTTPException(
            status_code=503, detail="Could not list Volumes; check Daytona configuration and access."
        ) from None


@router.post("/daytona/volumes")
def create_volume(body: CreateVolume, service: Operator) -> OperationStarted:
    """Start explicit creation of a persistent provider Volume."""
    return _start(service, "create_volume", body, lambda _operation: service.create_volume(body.name))


@router.post("/daytona/snapshot/build")
def build(body: Action, service: Operator) -> OperationStarted:
    """Start explicit snapshot construction from the packaged recipe."""
    return _start(service, "build_snapshot", body, service.build)


@router.post("/daytona/snapshot/activate")
def activate(body: Action, service: Operator) -> OperationStarted:
    """Activate an existing provider snapshot without rebuilding it."""
    return _start(service, "activate_snapshot", body, service.activate)


@router.post("/models/test")
def model_test(body: Action, service: Operator) -> OperationStarted:
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
        events, finished = await asyncio.to_thread(operation.wait_for_events, after, 0.15)
        for event in events:
            after = event["seq"]
            yield ServerSentEvent(data=event, id=str(after))
        if finished or await request.is_disconnected():
            break


@router.post("/apply")
async def apply(request: Request, service: Operator) -> ApplyResult:
    """Fence new Turns and request a restart only when existing work is idle.

    Return restart_required=True on acceptance. Return 409 when setup is not
    ready or a Turn or workspace operation is active, leaving that work running.
    Concurrent attempts are serialized so a rejected request cannot reopen setup
    after an accepted restart.
    """
    # Both locks belong to this event loop; provider/configuration work stays off-loop.
    manager = request.app.state.experiments
    async with request.app.state.setup_apply_lock, manager._lock if manager is not None else nullcontext():
        try:
            await asyncio.to_thread(service.can_apply)
        except SetupConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        if manager is not None:
            records = await asyncio.to_thread(manager.store.list_optimization_runs)
            if manager.busy or any(record["status"] in ACTIVE_STATUSES for record in records):
                service.cancel_apply()
                raise HTTPException(status_code=409, detail="An experiment is active; apply setup when it finishes.")
        # Fence new admission before checking active locks. Existing Turns are never cancelled here.
        request.app.state.setup_applying = True
        registry = request.app.state.session_registry
        if registry is not None and not await asyncio.to_thread(registry.request_restart):
            request.app.state.setup_applying = False
            service.cancel_apply()
            raise HTTPException(
                status_code=409, detail="A Turn or workspace operation is running; apply setup when it finishes."
            )
        if manager is not None:
            manager.accepting = False
    return ApplyResult(restart_required=True)


@router.post("/experiments/drain")
async def drain_experiments(request: Request, _service: Operator) -> dict[str, Any]:
    """Fence experiment admission before a local owner waits to stop its services."""
    manager = request.app.state.experiments
    if manager is None:
        return {"accepting": False, "busy": False, "experiments": []}
    return await manager.begin_drain()


@router.get("/experiments/status")
async def experiment_status(request: Request, _service: Operator) -> dict[str, Any]:
    """Observe cleanup through the same private, loopback-only operator capability."""
    manager = request.app.state.experiments
    if manager is None:
        return {"accepting": False, "busy": False, "experiments": []}
    return await manager.status()
